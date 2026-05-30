#!/usr/bin/env python3
"""
HA 全量实时采集 — 每 10 秒推送到 VictoriaMetrics

采集：温湿度、甲醛、门窗、人体存在、空调状态、
      电池电量、功耗、扫地机、窗帘、手机位置
"""

import json
import os
import sys
import time
import base64
import urllib.request
import urllib.error

# ── 配置 ──────────────────────────────────
VM_URL    = os.environ.get("VM_URL", "http://localhost:8428/api/v1/import")
HA_URL    = os.environ.get("HA_URL", "http://localhost:8123")
INTERVAL  = int(os.environ.get("COLLECT_INTERVAL", "10"))
TOKEN_FILE = os.path.expanduser("~/.hermes/.ha_token.b64")

# ── 传感器映射 ────────────────────────────
# 温湿度（数值型）
TEMP_HUM_SENSORS = {
    "sensor.xiaomi_cn_blt_3_1nubg558l0401_mini_temperature_p_2_1001":
        {"room": "客厅",     "metric": "ha_temp"},
    "sensor.xiaomi_cn_blt_3_1nubg558l0401_mini_relative_humidity_p_2_1002":
        {"room": "客厅",     "metric": "ha_hum"},
    "sensor.xiaomi_cn_blt_3_1nubjd3ogcc00_mini_temperature_p_2_1001":
        {"room": "主卧",     "metric": "ha_temp"},
    "sensor.xiaomi_cn_blt_3_1nubjd3ogcc00_mini_relative_humidity_p_2_1002":
        {"room": "主卧",     "metric": "ha_hum"},
    "sensor.miaomiaoc_cn_blt_3_1nubi71590g00_t9_temperature_p_3_1001":
        {"room": "书房",     "metric": "ha_temp"},
    "sensor.miaomiaoc_cn_blt_3_1nubi71590g00_t9_relative_humidity_p_3_1002":
        {"room": "书房",     "metric": "ha_hum"},
    "sensor.miaomiaoc_cn_blt_3_1b1nu924o5g02_t2_temperature_p_2_1":
        {"room": "阳台",     "metric": "ha_temp"},
    "sensor.miaomiaoc_cn_blt_3_1b1nu924o5g02_t2_relative_humidity_p_2_2":
        {"room": "阳台",     "metric": "ha_hum"},
    "sensor.miaomiaoc_cn_blt_3_1ati931bk5o00_t2_temperature_p_2_1":
        {"room": "主卫",     "metric": "ha_temp"},
    "sensor.miaomiaoc_cn_blt_3_1ati931bk5o00_t2_relative_humidity_p_2_2":
        {"room": "主卫",     "metric": "ha_hum"},
    "sensor.miaomiaoc_cn_blt_3_1b229tvvklo00_t2_temperature_p_2_1":
        {"room": "客卫",     "metric": "ha_temp"},
    "sensor.miaomiaoc_cn_blt_3_1b229tvvklo00_t2_relative_humidity_p_2_2":
        {"room": "客卫",     "metric": "ha_hum"},
    "sensor.yuemee_cn_blt_3_1ic2lmrrkkk00_mhfdv2_temperature_p_4_1001":
        {"room": "甲醛监测仪", "metric": "ha_temp"},
    "sensor.yuemee_cn_blt_3_1ic2lmrrkkk00_mhfdv2_relative_humidity_p_4_1008":
        {"room": "甲醛监测仪", "metric": "ha_hum"},
    "sensor.yuemee_cn_blt_3_1ic2lmrrkkk00_mhfdv2_hcho_density_p_4_1030":
        {"room": "甲醛监测仪", "metric": "ha_hcho"},
    # 电池电量（温湿度传感器）
    "sensor.xiaomi_cn_blt_3_1nubg558l0401_mini_battery_level_p_3_1003":
        {"room": "客厅温湿",  "metric": "ha_battery"},
    "sensor.xiaomi_cn_blt_3_1nubjd3ogcc00_mini_battery_level_p_3_1003":
        {"room": "主卧温湿",  "metric": "ha_battery"},
    "sensor.miaomiaoc_cn_blt_3_1nubi71590g00_t9_battery_level_p_2_1003":
        {"room": "书房温湿",  "metric": "ha_battery"},
    "sensor.miaomiaoc_cn_blt_3_1b1nu924o5g02_t2_battery_level_p_3_1":
        {"room": "阳台温湿",  "metric": "ha_battery"},
    "sensor.miaomiaoc_cn_blt_3_1ati931bk5o00_t2_battery_level_p_3_1":
        {"room": "主卫温湿",  "metric": "ha_battery"},
    "sensor.miaomiaoc_cn_blt_3_1b229tvvklo00_t2_battery_level_p_3_1":
        {"room": "客卫温湿",  "metric": "ha_battery"},
    "sensor.yuemee_cn_blt_3_1ic2lmrrkkk00_mhfdv2_battery_level_p_5_1003":
        {"room": "甲醛监测仪", "metric": "ha_battery"},
    # 门窗电池
    "sensor.isa_cn_blt_3_1lhenad1ok800_dw2hl_battery_level_p_3_1":
        {"room": "主卧门",    "metric": "ha_battery"},
    "sensor.isa_cn_blt_3_1lheqj1bkkk00_dw2hl_battery_level_p_3_1":
        {"room": "阳台推拉门", "metric": "ha_battery"},
    "sensor.isa_cn_blt_3_1khf4f2q8kc00_dw2hl_battery_level_p_3_1":
        {"room": "大门",      "metric": "ha_battery"},
    "sensor.isa_cn_blt_3_1ki7k8l4okk00_dw2hl_battery_level_p_3_1":
        {"room": "房间门",    "metric": "ha_battery"},
    # 人体传感器电池
    "sensor.linp_cn_blt_3_1k7pnv83sk400_es2_battery_level_p_4_1003":
        {"room": "主卫传感",  "metric": "ha_battery"},
    "sensor.linp_cn_blt_3_1k7qls8nkk401_es2_battery_level_p_4_1003":
        {"room": "厨房传感",  "metric": "ha_battery"},
    "sensor.linp_cn_blt_3_1k7pknomkkc00_es2_battery_level_p_4_1003":
        {"room": "客卫传感",  "metric": "ha_battery"},
    # 功耗
    "sensor.xiaomi_cn_888621197_2pro1_electric_power_p_4_2":
        {"room": "全屋新风",  "metric": "ha_power"},
    "sensor.xiaomi_cn_888645157_2pro2_electric_power_p_6_2":
        {"room": "玄关开关",  "metric": "ha_power"},
    # 扫地机电量
    "sensor.ijai_cn_1055400434_v14_battery_level_p_3_1":
        {"room": "扫地机",    "metric": "ha_battery"},
}

# 门窗传感器（on=开, off=关）
DOOR_SENSORS = {
    "binary_sensor.isa_cn_blt_3_1khf4f2q8kc00_dw2hl_contact_state_p_2_2":
        {"name": "大门"},
    "binary_sensor.isa_cn_blt_3_1lhenad1ok800_dw2hl_contact_state_p_2_2":
        {"name": "主卧门"},
    "binary_sensor.isa_cn_blt_3_1ki7k8l4okk00_dw2hl_contact_state_p_2_2":
        {"name": "房间门"},
    "binary_sensor.isa_cn_blt_3_1lheqj1bkkk00_dw2hl_contact_state_p_2_2":
        {"name": "阳台推拉门"},
}

# 人体存在传感器（on=有人, off=无人）
PRESENCE_SENSORS = {
    "binary_sensor.linp_cn_blt_3_1k7pnv83sk400_es2_occupancy_status_p_2_1078":
        {"name": "主卫"},
    "binary_sensor.linp_cn_blt_3_1k7qls8nkk401_es2_occupancy_status_p_2_1078":
        {"name": "厨房"},
    "binary_sensor.linp_cn_blt_3_1k7pknomkkc00_es2_occupancy_status_p_2_1078":
        {"name": "客卫"},
}

# 空调（climate 实体）
AC_SENSORS = {
    "climate.qdhkl_cn_proxy_750466344_0106_ac": {"name": "大客厅"},
    "climate.qdhkl_cn_proxy_750466344_0109_ac": {"name": "主卧"},
    "climate.qdhkl_cn_proxy_750466344_0108_ac": {"name": "书房"},
    "climate.qdhkl_cn_proxy_750466344_0110_ac": {"name": "儿童房"},
}
# state -> 数值(用于存VM) + 标签
AC_STATE_MAP = {"cool": 1, "heat": 2, "fan_only": 3, "dry": 4, "off": 0, "auto": 5}

# 窗帘位置（cover，position 在 attributes 里）
COVER_SENSORS = {
    "cover.xiaomi_cn_873991137_acn010_s_2_curtain":
        {"name": "窗帘"},
}

# 手机位置（person: home=1, not_home=0）
TRACKER_SENSORS = {
    "person.chen_gao_feng":
        {"name": "GPhone"},
}

# 扫地机状态（vacuum）
VACUUM_SENSORS = {
    "vacuum.ijai_cn_1055400434_v14":
        {"name": "扫地机"},
}
VACUUM_STATE_MAP = {
    "docked": 0, "idle": 1, "paused": 2,
    "cleaning": 3, "returning": 4, "error": 5,
}


def get_ha_token() -> str:
    with open(TOKEN_FILE) as f:
        return base64.b64decode(f.read().strip()).decode().strip()


def fetch_states(token: str) -> list[dict]:
    """获取所有实体完整状态（含 attributes）"""
    req = urllib.request.Request(
        f"{HA_URL}/api/states",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read())


def push_to_vm(lines: list[str]) -> bool:
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
        try:
            urllib.request.urlopen("http://localhost:8428/internal/force_flush", timeout=2)
        except Exception:
            pass
        return True
    except urllib.error.URLError as e:
        print(f"[ERROR] Push failed: {e.reason}", file=sys.stderr)
        return False


def collect(all_states: list[dict]) -> list[str]:
    now_ms = int(time.time() * 1000)
    lines = []
    state_map = {e["entity_id"]: e for e in all_states}

    def push(metric, val, labels=None):
        d = {"metric": {"__name__": metric, **(labels or {})},
             "values": [val], "timestamps": [now_ms]}
        lines.append(json.dumps(d, ensure_ascii=True))

    # 1) 温湿度 + 甲醛 + 电池（数值型）
    for eid, info in TEMP_HUM_SENSORS.items():
        e = state_map.get(eid)
        if not e or e["state"] in ("unavailable", "unknown"):
            continue
        try:
            val = float(e["state"])
        except (ValueError, TypeError):
            continue
        push(info["metric"], val, {"room": info["room"]})

    # 2) 门窗
    for eid, info in DOOR_SENSORS.items():
        e = state_map.get(eid)
        if not e or e["state"] in ("unavailable", "unknown"):
            continue
        val = 1.0 if e["state"] == "on" else 0.0
        push("ha_door", val, {"name": info["name"]})

    # 3) 人体存在
    for eid, info in PRESENCE_SENSORS.items():
        e = state_map.get(eid)
        if not e or e["state"] in ("unavailable", "unknown"):
            continue
        val = 1.0 if e["state"] == "on" else 0.0
        push("ha_presence", val, {"name": info["name"]})

    # 4) 空调
    for eid, info in AC_SENSORS.items():
        e = state_map.get(eid)
        if not e or e["state"] in ("unavailable", "unknown"):
            continue
        val = float(AC_STATE_MAP.get(e["state"], 0))
        mode = e["state"]
        push("ha_ac", val, {"name": info["name"], "mode": mode})

    # 5) 窗帘位置
    for eid, info in COVER_SENSORS.items():
        e = state_map.get(eid)
        if not e:
            continue
        pos = e.get("attributes", {}).get("current_position")
        if pos is None:
            pos = 100 if e["state"] == "open" else 0
        push("ha_cover", float(pos), {"name": info["name"]})

    # 6) 手机位置
    for eid, info in TRACKER_SENSORS.items():
        e = state_map.get(eid)
        if not e or e["state"] in ("unavailable", "unknown"):
            continue
        val = 1.0 if e["state"] == "home" else 0.0
        push("ha_tracker", val, {"name": info["name"]})

    # 7) 扫地机状态
    for eid, info in VACUUM_SENSORS.items():
        e = state_map.get(eid)
        if not e or e["state"] in ("unavailable", "unknown"):
            continue
        val = float(VACUUM_STATE_MAP.get(e["state"], 0))
        push("ha_vacuum", val, {
            "name": info["name"],
            "state": e["state"],
            "battery": str(e.get("attributes", {}).get("battery_level", 0)),
        })

    return lines


# ── 入口 ──────────────────────────────────
if __name__ == "__main__":
    loop = "--loop" in sys.argv

    if loop:
        print(f"[HA Collector] Starting loop (interval={INTERVAL}s)")
        while True:
            ts = time.strftime("%H:%M:%S")
            try:
                token = get_ha_token()
                all_states = fetch_states(token)
                lines = collect(all_states)
                ok = push_to_vm(lines)
                print(f"  [{ts}] {len(lines)} metrics → {'✓' if ok else '✗'}")
            except Exception as e:
                print(f"  [{ts}] ERROR: {e}", file=sys.stderr)
            time.sleep(INTERVAL)
    else:
        try:
            token = get_ha_token()
            all_states = fetch_states(token)
            lines = collect(all_states)
            ok = push_to_vm(lines)
            print(f"{len(lines)} metrics → {'✓' if ok else '✗'}")
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
