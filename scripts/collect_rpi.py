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
        print(f"  rpi_disk: {disk['used_pct']}% used ({disk['avail_gb']}GB avail)")

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
