/**
 * DataBoard — REST 轮询看板客户端
 *
 * 启动时 fetchConfig() → 渲染布局 + 初始化 ApexCharts
 * 定时器 → fetchData() 每 refresh_interval 秒 → 更新图表
 */

// =========================================
// 全局状态
// =========================================
const state = {
    charts: {},        // metricId -> ApexCharts instance
    panels: {},        // metricId -> panel DOM
    layout: null,
    metricDefs: {},    // metricId -> def (from server)
    refreshInterval: 10,
    pollTimer: null,
    adaptiveMode: {},  // metricId -> current unit mode ('kb'/'mb')
};

// 房间颜色映射（温湿度多曲线统一）
const ROOM_COLORS = {
    '客厅': '#ff6384',
    '主卧': '#36a2eb',
    '书房': '#00e396',
    '阳台': '#feb019',
    '主卫': '#9966ff',
    '客卫': '#ff9f40',
    '甲醛监测仪': '#4bc0c0',
};

// =========================================
// 初始化
// =========================================
async function initDashboard() {
    try {
        const config = await fetchConfig();
        state.layout = config;
        state.metricDefs = Object.fromEntries(
            (config.metrics || []).map(m => [m.id, m])
        );
        state.refreshInterval = config.refresh_interval || 10;

        renderDashboard();
        startPolling();
        updateConnStatus(true);
    } catch (err) {
        console.error('Failed to load config:', err);
        updateConnStatus(false);
        // 重试
        setTimeout(initDashboard, 3000);
    }
}

function startPolling() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    // 立即拉一次
    pollData();
    state.pollTimer = setInterval(pollData, state.refreshInterval * 1000);
}

async function pollData() {
    try {
        const data = await fetchData();
        updatePanels(data.metrics || []);
        updateTimestamp(data.timestamp);
        updateConnStatus(true);
    } catch (err) {
        console.warn('Poll failed:', err);
    }
}

function updateConnStatus(connected) {
    const el = document.getElementById('conn-status');
    if (el) {
        el.textContent = connected ? '● 已连接' : '○ 断开';
        el.style.color = connected ? '#00e396' : '#ff6384';
    }
}

// =========================================
// Dashboard 渲染
// =========================================
const STORAGE_KEY = 'databoard_collapsed';

function getCollapsedState(rowName, defaultCollapsed) {
    try {
        const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
        if (saved[rowName] !== undefined) return saved[rowName];
    } catch (_) {}
    return defaultCollapsed;
}

function saveCollapsedState(rowName, collapsed) {
    try {
        const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
        saved[rowName] = collapsed;
        localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
    } catch (_) {}
}

function renderDashboard() {
    const container = document.getElementById('dashboard');
    if (!container || !state.layout) return;

    const titleEl = document.getElementById('page-title');
    if (titleEl && state.layout.title) {
        titleEl.textContent = state.layout.title;
    }

    container.innerHTML = '';

    // 第 1 遍：构建所有 DOM 节点
    const chartPanels = []; // {def, chartEl} 延迟初始化

    for (const row of state.layout.layout) {
        const section = document.createElement('div');
        section.className = 'row-section';

        const rowName = row.row || '';
        const defaultCollapsed = row.collapsed === true;
        const isCollapsed = getCollapsedState(rowName, defaultCollapsed);

        const label = document.createElement('div');
        label.className = 'row-label';
        label.innerHTML = `<span class="toggle-icon ${isCollapsed ? 'collapsed' : ''}">▼</span> ${escHtml(rowName)}`;
        section.appendChild(label);

        const grid = document.createElement('div');
        grid.className = 'row-grid' + (isCollapsed ? ' collapsed' : '');

        for (const panelDef of (row.panels || [])) {
            const metricId = panelDef.metric;
            const def = state.metricDefs[metricId];
            if (!def) continue;

            const panel = createPanel(panelDef, def, chartPanels);
            grid.appendChild(panel);
            state.panels[metricId] = panel;
        }

        section.appendChild(grid);
        container.appendChild(section);

        label.addEventListener('click', () => {
            const nowCollapsed = !grid.classList.contains('collapsed');
            grid.classList.toggle('collapsed', nowCollapsed);
            const icon = label.querySelector('.toggle-icon');
            if (icon) icon.classList.toggle('collapsed', nowCollapsed);
            saveCollapsedState(rowName, nowCollapsed);
        });
    }

    // 第 2 遍：DOM 挂载完成后初始化图表
    for (const {def, chartEl} of chartPanels) {
        initChart(def, chartEl);
    }
}

function createPanel(panelDef, def, chartPanels) {
    const isStat = def.chart_type === 'stat';
    const isMulti = def.chart_type === 'multi_line';
    const div = document.createElement('div');
    div.className = isStat ? 'panel panel-stat' : 'panel';
    div.dataset.metricId = def.id;

    const w = panelDef.width || 1;
    if (w > 1) div.style.gridColumn = `span ${w}`;

    const h = panelDef.height || 1;
    if (h > 1) div.classList.add('h-2');

    const title = panelDef.title || def.name || def.id;

    if (isStat) {
        div.innerHTML = `
            <div class="panel-header">
                <span class="panel-title">${escHtml(title)}</span>
            </div>
            <div class="stat-body" id="stat-${def.id}">
                <div class="stat-value" id="stat-val-${def.id}">--</div>
                <div class="stat-sub" id="stat-sub-${def.id}"></div>
            </div>
        `;
    } else {
        div.innerHTML = `
            <div class="panel-header">
                <span class="panel-title">${escHtml(title)}</span>
                ${isMulti ? '' : `<span class="panel-value" id="val-${def.id}">-- <span class="unit">${escHtml(def.unit || '')}</span></span>`}
            </div>
            <div class="panel-chart" id="chart-${def.id}"></div>
        `;
        chartPanels.push({
            def: def,
            chartEl: div.querySelector('.panel-chart'),
        });
    }

    return div;
}

// =========================================
// ApexCharts 初始化
// =========================================
function initChart(def, chartEl) {
    if (!chartEl) {
        chartEl = document.getElementById(`chart-${def.id}`);
    }
    if (!chartEl) return;

    const isGauge = def.chart_type === 'gauge';
    const isMulti = def.chart_type === 'multi_line';
    const color = def.color || '#36a2eb';

    let opts;

    if (isGauge) {
        // ... gauge options (unchanged) ...
        opts = {
            chart: {
                type: 'radialBar',
                height: '100%',
                sparkline: { enabled: true },
            },
            plotOptions: {
                radialBar: {
                    startAngle: -135,
                    endAngle: 135,
                    hollow: { size: '55%' },
                    track: { background: '#2a2d3a' },
                    dataLabels: {
                        name: { show: false },
                        value: {
                            fontSize: '18px',
                            fontWeight: 600,
                            color: '#e1e4ed',
                            formatter: (v) => `${v.toFixed(1)}${def.unit || ''}`,
                        },
                    },
                },
            },
            fill: { colors: [color] },
            series: [0],
        };
    } else if (isMulti) {
        // 多系列折线图（温湿度按房间拆分）
        opts = {
            chart: {
                type: 'line',
                height: '100%',
                animations: {
                    enabled: true,
                    dynamicAnimation: { speed: 500 },
                },
                toolbar: { show: false },
                zoom: { enabled: false },
            },
            dataLabels: { enabled: false },
            stroke: {
                curve: 'smooth',
                width: 2,
            },
            legend: {
                show: true,
                position: 'top',
                horizontalAlign: 'left',
                labels: { colors: '#8b8fa5' },
                itemMargin: { horizontal: 8 },
            },
            series: [],
            xaxis: {
                type: 'datetime',
                labels: {
                    style: { colors: '#8b8fa5', fontSize: '10px' },
                    format: 'HH:mm',
                    datetimeUTC: false,
                },
                axisBorder: { show: true, color: '#2a2d3a' },
                axisTicks: { show: true, color: '#2a2d3a' },
            },
            yaxis: {
                forceNiceScale: true,
                decimalsInFloat: 1,
                labels: {
                    style: { colors: '#8b8fa5', fontSize: '10px' },
                    formatter: (v) => Number.isInteger(v) ? v.toString() : v.toFixed(1),
                },
            },
            grid: {
                show: true,
                borderColor: '#2a2d3a',
                strokeDashArray: 3,
                xaxis: { lines: { show: false } },
            },
            tooltip: {
                theme: 'dark',
                x: { format: 'HH:mm:ss' },
                y: {
                    formatter: (v) => `${v.toFixed(1)} ${def.unit || ''}`,
                },
            },
            colors: Object.values(ROOM_COLORS),
        };
    } else {
        // *** BUG FIX ***: 当 chart_type 为 'area' 时，chart.type 设为 'area' 而非 'line'
        const chartType = (def.chart_type === 'area') ? 'area' : 'line';
        opts = {
            chart: {
                type: chartType,
                height: '100%',
                animations: {
                    enabled: true,
                    dynamicAnimation: { speed: 500 },
                },
                toolbar: { show: false },
                zoom: { enabled: false },
            },
            dataLabels: { enabled: false },
            stroke: {
                curve: 'smooth',
                width: 2,
                colors: [color],
            },
            fill: def.chart_type === 'area' ? {
                type: 'gradient',
                gradient: {
                    shadeIntensity: 1,
                    opacityFrom: 0.3,
                    opacityTo: 0,
                    stops: [0, 100],
                },
            } : { opacity: 1 },
            series: [{ name: def.name || def.id, data: [] }],
            xaxis: {
                type: 'datetime',
                labels: {
                    style: { colors: '#8b8fa5', fontSize: '10px' },
                    format: 'HH:mm',
                    datetimeUTC: false,
                },
                axisBorder: { show: true, color: '#2a2d3a' },
                axisTicks: { show: true, color: '#2a2d3a' },
            },
            yaxis: {
                forceNiceScale: true,
                decimalsInFloat: 1,
                labels: {
                    style: { colors: '#8b8fa5', fontSize: '10px' },
                    formatter: (v) => Number.isInteger(v) ? v.toString() : v.toFixed(1),
                },
            },
            grid: {
                show: true,
                borderColor: '#2a2d3a',
                strokeDashArray: 3,
                xaxis: { lines: { show: false } },
            },
            tooltip: {
                theme: 'dark',
                x: { format: 'HH:mm:ss' },
                y: {
                    formatter: (v) => {
                        if (def.adaptive_unit && def.unit === 'MB/s') {
                            const mode = state.adaptiveMode[def.id] || 'mb';
                            if (mode === 'kb') return `${(v * 1024).toFixed(1)} KB/s`;
                            return `${v.toFixed(1)} MB/s`;
                        }
                        if (def.adaptive_unit && def.unit === 'GB') {
                            const mode = state.adaptiveMode[def.id] || 'gb';
                            if (mode === 'gb_mb') return `${(v * 1024).toFixed(1)} MB`;
                            return `${v.toFixed(1)} GB`;
                        }
                        return `${v.toFixed(1)} ${def.unit || ''}`;
                    },
                },
            },
            colors: [color],
        };
    }

    const chart = new ApexCharts(chartEl, opts);
    chart.render();
    state.charts[def.id] = chart;
}

// =========================================
// 数据更新
// =========================================
function updatePanels(metrics) {
    for (const m of metrics) {
        const chart = state.charts[m.id];
        const valEl = document.getElementById(`val-${m.id}`);
        const panel = state.panels[m.id];
        const def = state.metricDefs[m.id];
        const isStat = def && def.chart_type === 'stat';

        if (isStat) {
            // ── 状态卡片更新 ──
            const statVal = document.getElementById(`stat-val-${m.id}`);
            const statSub = document.getElementById(`stat-sub-${m.id}`);
            if (!statVal) continue;

            const fmt = (def.stat_format || '');

            if (fmt === 'uptime') {
                // 运行时间: 秒 → "1h 30m" (支持设备和启动时间子文本)
                const sec = parseFloat(m.value);
                if (!isNaN(sec)) {
                    statVal.textContent = formatUptime(Math.floor(sec));
                    if (statSub) {
                        const labels = m.labels || {};
                        if (labels.devices) {
                            statSub.textContent = `${labels.devices} 台设备`;
                        } else {
                            const boot = new Date(Date.now() - sec * 1000);
                            const pad = (n) => String(n).padStart(2, '0');
                            statSub.textContent = `启动 ${pad(boot.getHours())}:${pad(boot.getMinutes())}`;
                        }
                    }
                }
            } else if (fmt === 'fraction') {
                // Docker: X/Y + 磁盘占用
                const running = parseInt(m.value) || 0;
                let total = running;
                let diskBytes = 0;
                for (const other of metrics) {
                    if (other.id === 'rpi_docker_total' && other.value) {
                        total = parseInt(other.value) || running;
                    }
                    if (other.id === '__rpi_docker_disk' && other.value) {
                        diskBytes = parseFloat(other.value);
                    }
                }
                statVal.textContent = `${running}/${total}`;
                if (statSub) {
                    const status = running === total ? '全部运行' : `${total - running} 停止`;
                    const diskGb = diskBytes > 0 ? (diskBytes / (1024**3)).toFixed(1) : null;
                    statSub.textContent = diskGb ? `${status} · ${diskGb}GB` : status;
                }
            } else if (fmt === 'disk') {
                // 磁盘: used/total GB + 进度条 (支持多组 disk stat)
                const used = parseFloat(m.value);
                let total = 0;
                let pct = 0;
                const totalId = '__' + m.id.replace('_stat', '_total');
                for (const other of metrics) {
                    if (other.id === totalId && other.value) {
                        total = parseFloat(other.value);
                    }
                }
                if (!isNaN(used)) {
                    pct = total > 0 ? (used / total * 100) : 0;
                    statVal.textContent = total ? `${used.toFixed(0)}/${total.toFixed(0)}` : `${used.toFixed(1)}`;
                    if (statSub) {
                        statSub.innerHTML = `<div class="stat-bar"><div class="stat-bar-fill" style="width:${Math.min(100,pct)}%"></div></div> ${pct.toFixed(0)}%`;
                    }
                }
            } else if (fmt === 'iface') {
                // 网口状态: carrier=1 → "1000Mbps ↑", carrier=0 → "断开"
                const labels = m.labels || {};
                const carrier = parseInt(m.value);
                const speed = labels.speed || '?';
                if (carrier) {
                    statVal.textContent = `${speed}Mbps ↑`;
                    if (statSub) statSub.textContent = '已连接';
                } else {
                    statVal.textContent = '断开';
                    if (statSub) statSub.textContent = '⛔';
                }
            } else if (fmt === 'cpu_status') {
                // CPU: 主值=频率MHz, 子文本=进程数
                const labels = m.labels || {};
                statVal.textContent = `${parseFloat(m.value).toFixed(0)}MHz`;
                if (statSub) statSub.textContent = `${labels.processes || '?'} 进程`;
            } else if (fmt === 'port_pair') {
                // 网口: 主值="W:{speed}M L:{speed}M", 子文本=duplex
                const labels = m.labels || {};
                statVal.textContent = `W:${labels.wan_speed||'?'}M L:${labels.lan_speed||'?'}M`;
                if (statSub) statSub.textContent = labels.duplex || '';
            } else if (def.label_key === 'ip') {
                const labelKey = def.label_key || 'ip';
                const labels = m.labels || {};
                statVal.textContent = labels[labelKey] || '--';
                if (statSub) {
                    if (labels.version) {
                        statSub.textContent = labels.version;
                    } else {
                        statSub.textContent = 'wlan0';
                    }
                }
            } else {
                const num = parseFloat(m.value);
                statVal.textContent = isNaN(num) ? '--' : num.toFixed(0);
                if (statSub && def.unit) statSub.textContent = def.unit;
            }
            continue;
        }

        // ── 多系列折线图更新 ──
        if (def.chart_type === 'multi_line' && m.series) {
            const chart = state.charts[m.id];
            if (chart) {
                const seriesData = m.series.map(s => ({
                    name: s.name,
                    color: ROOM_COLORS[s.name] || def.color,
                    data: (s.history || []).map(p => ({ x: p.t, y: p.v })),
                }));
                chart.updateSeries(seriesData);
            }
            continue;
        }

        // ── 图表卡片更新 ──
        if (valEl && m.value !== undefined && m.value !== null) {
            const num = typeof m.value === 'number' ? m.value : parseFloat(m.value);
            if (!isNaN(num)) {
                let displayVal = num;
                let displayUnit = m.unit || '';
                // 自适应单位 + 滞回区间
                if (def.adaptive_unit && m.unit === 'MB/s') {
                    const T_LOW = 1.5, T_HIGH = 2.5;
                    let mode = state.adaptiveMode[m.id] || 'mb';
                    if (mode === 'kb' && num >= T_HIGH) mode = 'mb';
                    else if (mode === 'mb' && num < T_LOW) mode = 'kb';
                    state.adaptiveMode[m.id] = mode;
                    if (mode === 'kb') {
                        displayVal = num * 1024;
                        displayUnit = 'KB/s';
                    }
                }
                // 自适应单位 + 滞回区间 (GB)
                if (def.adaptive_unit && m.unit === 'GB') {
                    const T_LOW = 1.5, T_HIGH = 2.5;
                    let mode = state.adaptiveMode[m.id] || 'gb_mb';
                    if (mode === 'gb_mb' && num >= T_HIGH) mode = 'gb';
                    else if (mode === 'gb' && num < T_LOW) mode = 'gb_mb';
                    state.adaptiveMode[m.id] = mode;
                    if (mode === 'gb_mb') {
                        displayVal = num * 1024;
                        displayUnit = 'MB';
                    }
                }
                valEl.innerHTML = `${displayVal.toFixed(1)} <span class="unit">${escHtml(displayUnit)}</span>`;
            }
        }

        if (chart && m.history && m.history.length > 0) {
            const data = m.history.map(p => ({ x: p.t, y: p.v }));
            if (m.chart_type === 'gauge') {
                const lastVal = data[data.length - 1].y;
                chart.updateSeries([Math.min(100, Math.max(0, lastVal))]);
            } else {
                chart.updateSeries([{ data }]);
            }
        }

        // 数据源标记已移除
    }
}

function updateTimestamp(ts) {
    const el = document.getElementById('last-update');
    if (el && ts) {
        const d = new Date(ts * 1000);
        el.textContent = d.toLocaleTimeString('zh-CN', { hour12: false });
    }
}

// =========================================
// 启动
// =========================================
document.addEventListener('DOMContentLoaded', () => {
    initDashboard();
});
