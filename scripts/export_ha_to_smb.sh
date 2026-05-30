#!/bin/bash
# ============================================================
# HA 温湿度数据导出 — VM → SMB
# 每周执行，导出过去 7 天的数据到 SMB 备份目录
# ============================================================

set -e

SMB_DIR="/mnt/smb"
BACKUP_DIR="$SMB_DIR/Backups/ha_data"
VM_URL="http://localhost:8428"
NOW=$(date +%s)
SEVEN_DAYS_AGO=$((NOW - 604800))  # 7天前

# 检查 SMB 挂载
if ! mountpoint -q "$SMB_DIR"; then
    echo "[ERROR] SMB 未挂载: $SMB_DIR"
    exit 1
fi

mkdir -p "$BACKUP_DIR"

# 要导出的指标名
METRICS=("ha_temp" "ha_hum" "ha_hcho")

for metric in "${METRICS[@]}"; do
    echo "导出 $metric ..."
    ts=$(date "+%Y%m%d_%H%M%S")
    outfile="$BACKUP_DIR/${metric}_${ts}.jsonl"

    # 用 query_range 拉 7 天数据，step=60s 约 10080 点/系列
    curl -s -G "$VM_URL/api/v1/query_range" \
        --data-urlencode "query=$metric" \
        --data-urlencode "start=$SEVEN_DAYS_AGO" \
        --data-urlencode "end=$NOW" \
        --data-urlencode "step=60" \
        -o "/tmp/ha_export_${metric}.json"

    # 解析并输出为 NDJSON（每行一个数据点）
    python3 -c "
import json, sys, os

with open('/tmp/ha_export_${metric}.json') as f:
    data = json.load(f)

results = data.get('data', {}).get('result', [])
count = 0
lines = []
for res in results:
    labels = res.get('metric', {})
    name = labels.get('__name__', '$metric')
    room = labels.get('room', 'unknown')
    cat = labels.get('cat', '')
    values = res.get('values', [])
    for ts, val in values:
        lines.append(json.dumps({
            'metric': {'__name__': name, 'room': room, 'cat': cat},
            'values': [float(val)],
            'timestamps': [int(ts) * 1000],
        }))
        count += 1

with open('$outfile', 'w') as f:
    f.write('\\n'.join(lines) + '\\n')

size = os.path.getsize('$outfile')
print(f'  → {count} 条记录, {size/1024:.0f} KB')
" 2>&1

    rm -f "/tmp/ha_export_${metric}.json"
done

# 不限时间，一直保存
echo "✅ 备份完成: $BACKUP_DIR"
echo "当前备份文件:"
ls -lh "$BACKUP_DIR" 2>/dev/null | tail -10
