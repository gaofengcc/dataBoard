#!/usr/bin/env python3
"""
HA 温湿度实时采集 — 每 10 秒推送到 VictoriaMetrics

直接从 HA API 获取当前温湿度传感器状态，
推送到 VM，供 DataBoard 看板展示。
"""

import json
import os
import sys
import time
import base64
import urllib.request
import urllib.error

# ── 配置 ──────────────────────────────────
VM_URL = os.environ.get("VM_URL", "http://localhost:8428/api/v1/import")
HA_URL = os.environ.get("HA_URL", "http://localhost:8123")
INTERVAL = int(os.environ.get("COLLECT_INTERVAL", "10"))

TOKEN_FILE = os.path.expanduser("~/.hermes/.ha_token.b64")

# 传感器映射: entity_id -> {room, cat}
SENSORS = {
    # 客厅
    "sensor.xiaomi_cn_blt_3_1nubg558l0401_mini_temperature_p_2_1001":
        {"room": "客厅", "metric": "ha_temp"},
    "sensor.xiaomi_cn_blt_3_1nubg558l0401_mini_relative_humidity_p_2_1002":
        {"room": "客厅", "metric": "ha_hum"},
    # 主卧
    "sensor.xiaomi_cn_blt_3_1nubjd3ogcc00_mini_temperature_p_2_1001":
        {"room": "主卧", "metric": "ha_temp"},
    "sensor.xiaomi_cn_blt_3_1nubjd3ogcc00_mini_relative_humidity_p_2_1002":
        {"room": "主卧", "metric": "ha_hum"},
    # 书房
    "sensor.miaomiaoc_cn_blt_3_1nubi71590g00_t9_temperature_p_3_1001":
        {"room": "书房", "metric": "ha_temp"},
    "sensor.miaomiaoc_cn_blt_3_1nubi71590g00_t9_relative_humidity_p_3_1002":
        {"room": "书房", "metric": "ha_hum"},
    # 阳台
    "sensor.miaomiaoc_cn_blt_3_1b1nu924o5g02_t2_temperature_p_2_1":
        {"room": "阳台", "metric": "ha_temp"},
    "sensor.miaomiaoc_cn_blt_3_1b1nu924o5g02_t2_relative_humidity_p_2_2":
        {"room": "阳台", "metric": "ha_hum"},
    # 主卫
    "sensor.miaomiaoc_cn_blt_3_1ati931bk5o00_t2_temperature_p_2_1":
        {"room": "主卫", "metric": "ha_temp"},
    "sensor.miaomiaoc_cn_blt_3_1ati931bk5o00_t2_relative_humidity_p_2_2":
        {"room": "主卫", "metric": "ha_hum"},
    # 客卫
    "sensor.miaomiaoc_cn_blt_3_1b229tvvklo00_t2_temperature_p_2_1":
        {"room": "客卫", "metric": "ha_temp"},
    "sensor.miaomiaoc_cn_blt_3_1b229tvvklo00_t2_relative_humidity_p_2_2":
        {"room": "客卫", "metric": "ha_hum"},
    # 甲醛监测仪
    "sensor.yuemee_cn_blt_3_1ic2lmrrkkk00_mhfdv2_temperature_p_4_1001":
        {"room": "甲醛监测仪", "metric": "ha_temp"},
    "sensor.yuemee_cn_blt_3_1ic2lmrrkkk00_mhfdv2_relative_humidity_p_4_1008":
        {"room": "甲醛监测仪", "metric": "ha_hum"},
    "sensor.yuemee_cn_blt_3_1ic2lmrrkkk00_mhfdv2_hcho_density_p_4_1030":
        {"room": "甲醛监测仪", "metric": "ha_hcho"},
}


def get_ha_token() -> str:
    """读取 HA 长令牌"""
    with open(TOKEN_FILE) as f:
        return base64.b64decode(f.read().strip()).decode().strip()


def fetch_states(token: str) -> dict[str, str]:
    """获取所有实体状态，返回 entity_id -> state 字典"""
    req = urllib.request.Request(
        f"{HA_URL}/api/states",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read())
    return {e["entity_id"]: e["state"] for e in data}


def push_to_vm(lines: list[str]) -> bool:
    """NDJSON 推送到 VictoriaMetrics"""
    if not lines:
        return True
    payload = "\n".join(lines) + "\n"
    req = urllib.request.Request(
        VM_URL,
        data=payload.encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=5)
        # 强制刷盘
        try:
            urllib.request.urlopen(
                "http://localhost:8428/internal/force_flush", timeout=2
            )
        except Exception:
            pass
        return True
    except urllib.error.URLError as e:
        print(f"[ERROR] Push failed: {e.reason}", file=sys.stderr)
        return False


def collect() -> list[str]:
    """单次采集：获取 HA 状态 → 拼接 NDJSON 行"""
    try:
        token = get_ha_token()
        states = fetch_states(token)
    except Exception as e:
        print(f"[ERROR] HA fetch failed: {e}", file=sys.stderr)
        return []

    now_ms = int(time.time() * 1000)
    lines = []

    for eid, info in SENSORS.items():
        state_str = states.get(eid)
        if state_str is None or state_str in ("unavailable", "unknown"):
            continue
        try:
            val = float(state_str)
        except (ValueError, TypeError):
            continue

        lines.append(json.dumps({
            "metric": {
                "__name__": info["metric"],
                "room": info["room"],
                "cat": "温湿度",
            },
            "values": [val],
            "timestamps": [now_ms],
        }, ensure_ascii=True))

    return lines


# ── 入口 ──────────────────────────────────
if __name__ == "__main__":
    loop = "--loop" in sys.argv
    single = "--once" in sys.argv or not loop

    if loop:
        print(f"[HA Collector] Starting loop (interval={INTERVAL}s)")
        while True:
            ts = time.strftime("%H:%M:%S")
            lines = collect()
            if lines:
                ok = push_to_vm(lines)
                print(f"  [{ts}] {len(lines)} metrics → {'✓' if ok else '✗'}")
            else:
                print(f"  [{ts}] no data")
            time.sleep(INTERVAL)
    else:
        lines = collect()
        if lines:
            ok = push_to_vm(lines)
            print(f"{len(lines)} metrics → {'✓' if ok else '✗'}")
        else:
            print("no data")
