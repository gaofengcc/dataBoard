#!/bin/bash
# ==========================================================
# OpenWrt (R66S) 系统指标采集
# 从 RPi 上 SSH 到 R66S 采集数据，推送 VictoriaMetrics
# ==========================================================

set -e

VM_URL="${VM_URL:-http://localhost:8428/api/v1/import}"
R66S="${R66S:-root@192.168.100.1}"
INTERVAL="${COLLECT_INTERVAL:-30}"

# ── SSH 采集 (返回 raw key=value 行) ──────
collect_r66s_raw() {
    ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "$R66S" '

# CPU 温度
t=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo 0)
echo "temp=$t"

# CPU 负载
l=$(cat /proc/loadavg)
echo "load1=$(echo "$l" | cut -d" " -f1)"
echo "load5=$(echo "$l" | cut -d" " -f2)"
echo "load15=$(echo "$l" | cut -d" " -f3)"

# 内存 (KB)
mt=$(grep MemTotal /proc/meminfo | grep -o "[0-9]*")
mf=$(grep MemFree /proc/meminfo | grep -o "[0-9]*")
mb=$(grep "^Buffers:" /proc/meminfo | grep -o "[0-9]*")
mc=$(grep "^Cached:" /proc/meminfo | grep -o "[0-9]*")
ma=$(( mf + mb + mc ))
echo "mem_total=$mt"
echo "mem_avail=$ma"
echo "mem_used_pct=$(awk -v t="$mt" -v a="$ma" "BEGIN {printf \"%.1f\", (1-a/t)*100}")"

# 网络
wan_rx=$(awk "/pppoe-wan:/ {print \$2}" /proc/net/dev)
wan_tx=$(awk "/pppoe-wan:/ {print \$10}" /proc/net/dev)
lan_rx=$(awk "/br-lan:/ {print \$2}" /proc/net/dev)
lan_tx=$(awk "/br-lan:/ {print \$10}" /proc/net/dev)
echo "wan_rx=$wan_rx"
echo "wan_tx=$wan_tx"
echo "lan_rx=$lan_rx"
echo "lan_tx=$lan_tx"

# 运行时间
echo "uptime=$(cat /proc/uptime | cut -d" " -f1)"
'
}


# ── 解析 raw 数据 → NDJSON ────────────────
to_ndjson() {
    local line key val
    now_ms=$(($(date +%s) * 1000))

    while IFS='=' read -r key val; do
        [ -z "$key" ] && continue
        case "$key" in
            temp)          echo '{"metric":{"__name__":"owrt_cpu_temp","host":"r66s","unit":"celsius"},"values":['"$(awk "BEGIN {printf \"%.1f\", $val/1000}")"'],"timestamps":['$now_ms']}'
                ;;
            load1)         echo '{"metric":{"__name__":"owrt_load1","host":"r66s","type":"load"},"values":['$val'],"timestamps":['$now_ms']}'
                ;;
            mem_used_pct)  echo '{"metric":{"__name__":"owrt_mem_usage_pct","host":"r66s","unit":"percent"},"values":['$val'],"timestamps":['$now_ms']}'
                ;;
            wan_rx)        echo '{"metric":{"__name__":"owrt_wan_rx_bytes","host":"r66s","interface":"pppoe-wan","unit":"bytes"},"values":['$val'],"timestamps":['$now_ms']}'
                ;;
            wan_tx)        echo '{"metric":{"__name__":"owrt_wan_tx_bytes","host":"r66s","interface":"pppoe-wan","unit":"bytes"},"values":['$val'],"timestamps":['$now_ms']}'
                ;;
            lan_rx)        echo '{"metric":{"__name__":"owrt_lan_rx_bytes","host":"r66s","interface":"br-lan","unit":"bytes"},"values":['$val'],"timestamps":['$now_ms']}'
                ;;
            lan_tx)        echo '{"metric":{"__name__":"owrt_lan_tx_bytes","host":"r66s","interface":"br-lan","unit":"bytes"},"values":['$val'],"timestamps":['$now_ms']}'
                ;;
        esac
    done
}


# ── 推送 ──────────────────────────────────
push() {
    local data="$1"
    if [ -z "$data" ]; then
        echo "  → no data"
        return 1
    fi

    echo "$data" | curl -s -X POST "$VM_URL" \
        -H "Content-Type: application/json" \
        --data-binary @- > /dev/null 2>&1

    if [ $? -eq 0 ]; then
        echo "  → ✓ pushed to VM"
    else
        echo "  → ✗ push failed"
    fi
}


# ── 入口 ──────────────────────────────────
if [ "$1" = "--loop" ]; then
    echo "[OpenWrt Collector] Starting loop (interval=${INTERVAL}s)"
    while true; do
        ts=$(date "+%H:%M:%S")
        echo ""
        echo "[$ts] Collecting R66S..."
        raw=$(collect_r66s_raw)
        ndjson=$(echo "$raw" | to_ndjson)
        echo "$ndjson" | head -c 300
        echo ""
        push "$ndjson"
        sleep "$INTERVAL"
    done
else
    raw=$(collect_r66s_raw)
    ndjson=$(echo "$raw" | to_ndjson)
    echo "$ndjson"
    push "$ndjson"
fi
