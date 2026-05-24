#!/usr/bin/env python3
"""
RPi 系统指标采集 — 每 30 秒推送到 VictoriaMetrics

采集项：
  - CPU 温度  (/sys/class/thermal/thermal_zone0/temp)
  - CPU 负载  (/proc/loadavg) — 1min/5min/15min
  - 内存使用  (/proc/meminfo)
  - 磁盘使用  (df /)
  - CPU 频率  (scaling_cur_freq)

用法:
  python3 collect_rpi.py                     # 单次采集推送
  python3 collect_rpi.py --loop              # 持续采集(30s间隔)

安装 systemd 定时器:
  sudo cp collect_rpi.service /etc/systemd/system/
  sudo systemctl enable --now collect_rpi.timer
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

# ── 配置 ──────────────────────────────────
VM_URL = os.environ.get("VM_URL", "http://localhost:8428/api/v1/import")
HOST = "gaofengpi"
INTERVAL = int(os.environ.get("COLLECT_INTERVAL", "30"))


# ── 采集函数 ──────────────────────────────
def read_sysfs(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def get_cpu_temp() -> float | None:
    val = read_sysfs("/sys/class/thermal/thermal_zone0/temp")
    if val:
        return round(int(val) / 1000, 1)
    return None


def get_cpu_freq() -> float | None:
    val = read_sysfs(
        "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
    )
    if val:
        return round(int(val) / 1000, 1)  # MHz
    return None


def get_cpu_load() -> dict | None:
    val = read_sysfs("/proc/loadavg")
    if val:
        parts = val.split()
        return {
            "load1": float(parts[0]),
            "load5": float(parts[1]),
            "load15": float(parts[2]),
        }
    return None


def get_mem_usage() -> dict | None:
    meminfo = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                p = line.split(":")
                if len(p) == 2:
                    k = p[0].strip()
                    v = p[1].strip().split()[0]
                    meminfo[k] = int(v)
    except Exception:
        return None

    total = meminfo.get("MemTotal")
    available = meminfo.get("MemAvailable")
    if total and available:
        return {
            "total_mb": round(total / 1024, 1),
            "available_mb": round(available / 1024, 1),
            "used_pct": round((total - available) / total * 100, 1),
        }
    return None


def get_disk_usage() -> dict | None:
    try:
        r = subprocess.run(
            ["df", "-B1", "/"],
            capture_output=True, text=True, timeout=5,
        )
        lines = r.stdout.strip().split("\n")
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 6:
                total = int(parts[1])
                used = int(parts[2])
                avail = int(parts[3])
                used_pct = float(parts[4].rstrip("%"))
                return {
                    "total_gb": round(total / (1024**3), 1),
                    "used_gb": round(used / (1024**3), 1),
                    "avail_gb": round(avail / (1024**3), 1),
                    "used_pct": used_pct,
                }
    except Exception:
        pass
    return None


def get_fan_speed() -> int | None:
    """风扇转速 RPM"""
    try:
        for hwmon in [f"/sys/devices/platform/cooling_fan/hwmon/hwmon{n}/fan1_input" for n in range(10)]:
            try:
                with open(hwmon) as f:
                    return int(f.read().strip())
            except (FileNotFoundError, OSError):
                continue
    except Exception:
        pass
    return None


def get_ip_address() -> str | None:
    """本机 IP 地址"""
    try:
        r = subprocess.run(
            ["ip", "-4", "addr", "show"],
            capture_output=True, text=True, timeout=5,
        )
        for line in r.stdout.split("\n"):
            if "inet " in line and "127.0.0.1" not in line:
                return line.strip().split()[1].split("/")[0]
    except Exception:
        pass
    return None


def get_uptime_seconds() -> int | None:
    """系统运行时间（秒）"""
    val = read_sysfs("/proc/uptime")
    if val:
        return int(float(val.split()[0]))
    return None


def get_docker_status() -> dict | None:
    """Docker 容器状态"""
    try:
        r = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Status}}"],
            capture_output=True, text=True, timeout=10,
        )
        lines = [l.strip() for l in r.stdout.split("\n") if l.strip()]
        total = len(lines)
        running = sum(1 for l in lines if l.startswith("Up"))
        return {"total": total, "running": running}
    except Exception:
        return None


def get_docker_disk_bytes() -> int | None:
    """Docker 占用的磁盘空间（字节）"""
    try:
        r = subprocess.run(
            ["docker", "system", "df", "--format", "{{.Type}}\t{{.Size}}"],
            capture_output=True, text=True, timeout=10,
        )
        total_bytes = 0
        for line in r.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) == 2 and parts[0] != "Build Cache":
                size_str = parts[1].strip()
                total_bytes += parse_size_to_bytes(size_str)
        return total_bytes if total_bytes > 0 else None
    except Exception:
        return None


def parse_size_to_bytes(s: str) -> int:
    """10.5GB → bytes, 143.6MB → bytes"""
    s = s.strip()
    # 长后缀优先，避免 "GB" 被 "B" 匹配
    multipliers = sorted(
        {"GB": 1024**3, "MB": 1024**2, "KB": 1024, "TB": 1024**4, "B": 1, "kB": 1000}.items(),
        key=lambda x: -len(x[0]),
    )
    for suffix, mult in multipliers:
        if s.endswith(suffix):
            try:
                num = float(s[: -len(suffix)])
                return int(num * mult)
            except ValueError:
                return 0
    # 纯数字
    try:
        return int(s)
    except ValueError:
        return 0


def get_net_bytes(iface: str = "wlan0") -> dict | None:
    """网卡累计流量"""
    try:
        with open("/proc/net/dev") as f:
            for line in f:
                if line.strip().startswith(iface + ":"):
                    parts = line.split()
                    rx = int(parts[1])
                    tx = int(parts[9])
                    return {"rx_bytes": rx, "tx_bytes": tx}
    except Exception:
        pass
    return None


# ── 推送 ──────────────────────────────────
def push(metrics: list[dict]) -> bool:
    """NDJSON 批量推送到 VictoriaMetrics"""
    now_ms = int(time.time() * 1000)
    lines = []
    for m in metrics:
        labels = {"host": HOST, **(m.get("labels", {}))}
        lines.append(json.dumps({
            "metric": {"__name__": m["name"], **labels},
            "values": [m["value"]],
            "timestamps": [now_ms],
        }))

    data = "\n".join(lines) + "\n"
    req = urllib.request.Request(
        VM_URL,
        data=data.encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=5)
        return True
    except urllib.error.URLError as e:
        print(f"[ERROR] Push failed: {e.reason}")
        return False


def collect() -> list[dict]:
    metrics = []

    # CPU 温度
    temp = get_cpu_temp()
    if temp is not None:
        metrics.append({"name": "rpi_cpu_temp", "value": temp, "labels": {"unit": "celsius"}})
        print(f"  rpi_cpu_temp: {temp}°C")

    # CPU 频率
    freq = get_cpu_freq()
    if freq is not None:
        metrics.append({"name": "rpi_cpu_freq", "value": freq, "labels": {"unit": "mhz"}})
        print(f"  rpi_cpu_freq: {freq} MHz")

    # CPU 负载
    load = get_cpu_load()
    if load:
        metrics.append({"name": "rpi_load1", "value": load["load1"], "labels": {"type": "load"}})
        metrics.append({"name": "rpi_load5", "value": load["load5"], "labels": {"type": "load"}})
        metrics.append({"name": "rpi_load15", "value": load["load15"], "labels": {"type": "load"}})
        print(f"  rpi_load: {load['load1']} / {load['load5']} / {load['load15']}")

    # 内存
    mem = get_mem_usage()
    if mem:
        metrics.append({"name": "rpi_mem_usage_pct", "value": mem["used_pct"], "labels": {"unit": "percent"}})
        metrics.append({"name": "rpi_mem_available_mb", "value": mem["available_mb"], "labels": {"unit": "mb"}})
        print(f"  rpi_mem: {mem['used_pct']}% used ({mem['available_mb']}MB avail)")

    # 磁盘
    disk = get_disk_usage()
    if disk:
        metrics.append({"name": "rpi_disk_usage_pct", "value": disk["used_pct"], "labels": {"unit": "percent"}})
        metrics.append({"name": "rpi_disk_avail_gb", "value": disk["avail_gb"], "labels": {"unit": "gb"}})
        metrics.append({"name": "rpi_disk_total_gb", "value": disk["total_gb"], "labels": {"unit": "gb"}})
        metrics.append({"name": "rpi_disk_used_gb", "value": disk["used_gb"], "labels": {"unit": "gb"}})
        print(f"  rpi_disk: {disk['used_pct']}% used ({disk['used_gb']}/{disk['total_gb']}GB)")

    # 风扇转速
    fan = get_fan_speed()
    if fan is not None:
        metrics.append({"name": "rpi_fan_speed", "value": fan, "labels": {"unit": "rpm"}})
        print(f"  rpi_fan_speed: {fan} RPM")

    # IP 地址（作为标签值，推数值 1 表示存在）
    ip = get_ip_address()
    if ip:
        metrics.append({"name": "rpi_ip_info", "value": 1, "labels": {"ip": ip}})
        print(f"  rpi_ip: {ip}")

    # 运行时间（秒）
    uptime = get_uptime_seconds()
    if uptime is not None:
        metrics.append({"name": "rpi_uptime_seconds", "value": uptime, "labels": {"unit": "seconds"}})
        days = uptime // 86400
        hours = (uptime % 86400) // 3600
        mins = (uptime % 3600) // 60
        print(f"  rpi_uptime: {days}d {hours:02d}:{mins:02d}")

    # Docker 状态
    docker = get_docker_status()
    if docker:
        metrics.append({"name": "rpi_docker_running", "value": docker["running"], "labels": {"type": "running"}})
        metrics.append({"name": "rpi_docker_total", "value": docker["total"], "labels": {"type": "total"}})
        print(f"  rpi_docker: {docker['running']}/{docker['total']} running")

    # Docker 磁盘占用
    dk_disk = get_docker_disk_bytes()
    if dk_disk is not None:
        metrics.append({"name": "rpi_docker_disk_bytes", "value": dk_disk, "labels": {"unit": "bytes"}})
        print(f"  rpi_docker_disk: {dk_disk / (1024**3):.1f}GB")

    # 网络累计流量（用于在 dashboard 用 rate() 算网速）
    net = get_net_bytes()
    if net:
        metrics.append({"name": "rpi_net_rx_bytes", "value": net["rx_bytes"], "labels": {"interface": "wlan0", "unit": "bytes"}})
        metrics.append({"name": "rpi_net_tx_bytes", "value": net["tx_bytes"], "labels": {"interface": "wlan0", "unit": "bytes"}})
        rx_mb = round(net["rx_bytes"] / 1048576, 1)
        tx_mb = round(net["tx_bytes"] / 1048576, 1)
        print(f"  rpi_net: ↓{rx_mb}MB ↑{tx_mb}MB")

    return metrics


# ── 入口 ──────────────────────────────────
if __name__ == "__main__":
    loop = "--loop" in sys.argv

    if loop:
        print(f"[RPi Collector] Starting loop (interval={INTERVAL}s)")
        while True:
            ts = time.strftime("%H:%M:%S")
            print(f"\n[{ts}] Collecting...")
            metrics = collect()
            if metrics:
                ok = push(metrics)
                print(f"  → {'✓ pushed' if ok else '✗ failed'}")
            time.sleep(INTERVAL)
    else:
        metrics = collect()
        if metrics:
            ok = push(metrics)
            print(f"→ {'✓ pushed' if ok else '✗ failed'}")
        else:
            print("No metrics collected")
