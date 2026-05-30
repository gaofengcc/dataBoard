#!/bin/bash
# ============================================================
# OpenWrt (R66S) 系统指标采集 — HTTP GET 版
#
# 从 R66S 的 CGI 接口获取数据，推送到 VictoriaMetrics
# 数据流：Pi curl → R66S uhttpd/cgi-bin/metrics → Pi 解析 → VM
# ============================================================

set -e

VM_URL="${VM_URL:-http://localhost:8428/api/v1/import}"
R66S_URL="${R66S_URL:-http://192.168.100.1/cgi-bin/metrics}"
INTERVAL="${COLLECT_INTERVAL:-30}"

# ── 单次采集+推送 ────────────────────────────
collect_and_push() {
    # 1) 从 R66S HTTP 接口取 JSON
    json=$(curl -s --max-time 5 "$R66S_URL" 2>/dev/null) || {
        echo "  → ✗ HTTP fetch failed"
        return 1
    }

    [ -z "$json" ] && {
        echo "  → ✗ empty response"
        return 1
    }

    # 2) 用 Python 解析 JSON 并推 VM
    echo "$json" | python3 -c "
import json, sys, time, urllib.request

data = json.load(sys.stdin)
now_ms = int(time.time() * 1000)

# 指标定义: (metric_name, value, extra_labels)
metrics = [
    ('owrt_cpu_temp',   data.get('cpu_temp', 0),       {'host': 'r66s', 'unit': 'celsius'}),
    ('owrt_load1',      data.get('load1', 0),           {'host': 'r66s', 'type': 'load'}),
    ('owrt_mem_usage_pct', data.get('mem_used_pct', 0), {'host': 'r66s', 'unit': 'percent'}),
    ('owrt_cpu_freq',   data.get('cpu_freq_mhz', 0),    {'host': 'r66s', 'unit': 'mhz'}),
    ('owrt_process_count', data.get('process_count', 0),{'host': 'r66s', 'type': 'process'}),
    ('owrt_conntrack_count', data.get('conntrack_count', 0),{'host': 'r66s', 'type': 'conntrack'}),
    ('owrt_overlay_used_gb', data.get('overlay_used_gb', 0),{'host': 'r66s', 'unit': 'gb'}),
    ('owrt_overlay_total_gb', data.get('overlay_total_gb', 0),{'host': 'r66s', 'unit': 'gb'}),
    ('owrt_usb_used_gb', data.get('usb_used_gb', 0),   {'host': 'r66s', 'unit': 'gb'}),
    ('owrt_usb_total_gb', data.get('usb_total_gb', 0), {'host': 'r66s', 'unit': 'gb'}),
    ('owrt_iface_eth0', data.get('eth0_carrier', 0),  {'host': 'r66s', 'speed': str(data.get('eth0_speed', '?')), 'duplex': 'full'}),
    ('owrt_iface_eth1', data.get('eth1_carrier', 0),  {'host': 'r66s', 'speed': str(data.get('eth1_speed', '?')), 'duplex': 'full'}),
    ('owrt_uptime_seconds', data.get('uptime_seconds', 0), {'host': 'r66s', 'unit': 'seconds'}),
    ('owrt_device_count', data.get('dhcp_leases', 0), {'host': 'r66s', 'unit': 'devices'}),
    ('owrt_ip_info', 1, {'host': 'r66s', 'ip': data.get('lan_ip', '?')}),
    ('owrt_version', 1, {'host': 'r66s', 'version': str(data.get('system_version', '?'))}),
    # 组合指标
    ('owrt_system_info', 1, {'host': 'r66s', 'ip': data.get('lan_ip', '?'), 'version': str(data.get('system_version', '?'))}),
    ('owrt_system_status', data.get('uptime_seconds', 0), {'host': 'r66s', 'devices': str(data.get('dhcp_leases', 0))}),
    ('owrt_cpu_status', data.get('cpu_freq_mhz', 0), {'host': 'r66s', 'processes': str(data.get('process_count', 0))}),
    ('owrt_net_ports', 1, {'host': 'r66s', 'wan_speed': str(data.get('eth0_speed', '?')), 'lan_speed': str(data.get('eth1_speed', '?')), 'duplex': 'full'}),
    ('owrt_wan_rx_bytes', data.get('wan_rx_bytes', 0),  {'host': 'r66s', 'interface': 'pppoe-wan', 'unit': 'bytes'}),
    ('owrt_wan_tx_bytes', data.get('wan_tx_bytes', 0),  {'host': 'r66s', 'interface': 'pppoe-wan', 'unit': 'bytes'}),
    ('owrt_lan_rx_bytes', data.get('lan_rx_bytes', 0),  {'host': 'r66s', 'interface': 'br-lan', 'unit': 'bytes'}),
    ('owrt_lan_tx_bytes', data.get('lan_tx_bytes', 0),  {'host': 'r66s', 'interface': 'br-lan', 'unit': 'bytes'}),
]

lines = []
for name, val, labels in metrics:
    try:
        v = float(val)
    except (TypeError, ValueError):
        v = 0
    lines.append(json.dumps({
        'metric': {'__name__': name, **labels},
        'values': [v],
        'timestamps': [now_ms],
    }))

payload = '\n'.join(lines) + '\n'
req = urllib.request.Request('${VM_URL}',
    data=payload.encode(),
    headers={'Content-Type': 'application/json'})
try:
    urllib.request.urlopen(req, timeout=5)
    print('  → ✓ pushed to VM')
except Exception as e:
    print(f'  → ✗ VM push failed: {e}')
" 2>&1 || echo "  → ✗ parse/push error"
}

# ── 打印当前值（供调试） ───────────────────────
print_values() {
    json=$(curl -s --max-time 5 "$R66S_URL" 2>/dev/null)
    [ -n "$json" ] && echo "$json" | python3 -m json.tool
}

# ── 入口 ────────────────────────────────────
if [ "$1" = "--loop" ]; then
    echo "[OpenWrt Collector] Starting loop (interval=${INTERVAL}s, source=R66S HTTP)"
    while true; do
        ts=$(date "+%H:%M:%S")
        echo ""
        echo "[$ts] Collecting R66S..."
        collect_and_push
        sleep "$INTERVAL"
    done
elif [ "$1" = "--print" ]; then
    print_values
else
    collect_and_push
fi
