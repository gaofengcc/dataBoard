/**
 * DataBoard — WebSocket 实时看板客户端
 *
 * 从 WebSocket 接收 JSON 数据流，渲染 ApexCharts 仪表板。
 * API 布局信息在页面首次加载时由服务端注入。
 */

// =========================================
// 全局状态
// =========================================
const state = {
    ws: null,
    reconnectTimer: null,
    charts: {},        // metricId -> ApexCharts instance
    panels: {},        // metricId -> panel DOM
    layout: null,
    metricDefs: {},    // metricId -> def (from server)
    historyLen: 180,   // 5s × 180 = 15min
};

// =========================================
// WebSocket 连接管理
// =========================================
const wsBase = `ws://${location.host}/ws`;

function connectWS() {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(wsBase);
    state.ws = ws;

    ws.onopen = () => {
        setConnStatus(true);
        // 请求布局 + 各指标配置
        ws.send(JSON.stringify({ type: 'init' }));
    };

    ws.onmessage = (e) => {
        try {
            const msg = JSON.parse(e.data);
            handleMessage(msg);
        } catch (err) {
            console.warn('WS parse error:', err);
        }
    };

    ws.onclose = () => {
        setConnStatus(false);
        scheduleReconnect();
    };

    ws.onerror = () => {
        ws.close();
    };
}

function scheduleReconnect() {
    if (state.reconnectTimer) return;
    state.reconnectTimer = setTimeout(() => {
        state.reconnectTimer = null;
        connectWS();
    }, 3000);
}

function setConnStatus(connected) {
    const el = document.getElementById('conn-status');
    if (el) {
        el.textContent = connected ? '● 已连接' : '○ 断开';
        el.style.color = connected ? '#00e396' : '#ff6384';
    }
}

// =========================================
// 消息处理
// =========================================
function handleMessage(msg) {
    switch (msg.type) {
        case 'layout':
            // 首次加载：接收布局 + 指标定义
            state.layout = msg.layout;
            state.metricDefs = Object.fromEntries(
                (msg.metrics || []).map(m => [m.id, m])
            );
            renderDashboard();
            break;

        case 'data':
            // 实时数据推送
            updatePanels(msg.metrics || []);
            updateTimestamp(msg.timestamp);
            break;
    }
}

// =========================================
// Dashboard 渲染
// =========================================
function renderDashboard() {
    const container = document.getElementById('dashboard');
    if (!container || !state.layout) return;

    // 设置标题
    const titleEl = document.getElementById('page-title');
    if (titleEl && state.layout.title) {
        titleEl.textContent = state.layout.title;
    }

    container.innerHTML = '';

    for (const row of state.layout.layout) {
        const section = document.createElement('div');
        section.className = 'row-section';

        const label = document.createElement('div');
        label.className = 'row-label';
        label.textContent = row.row || '';
        section.appendChild(label);

        const grid = document.createElement('div');
        grid.className = 'row-grid';

        for (const panelDef of (row.panels || [])) {
            const metricId = panelDef.metric;
            const def = state.metricDefs[metricId];
            if (!def) continue;

            const panel = createPanel(panelDef, def);
            grid.appendChild(panel);
            state.panels[metricId] = panel;
        }

        section.appendChild(grid);
        container.appendChild(section);
    }
}

function createPanel(panelDef, def) {
    const div = document.createElement('div');
    div.className = 'panel';
    div.dataset.metricId = def.id;

    // 宽度
    const w = panelDef.width || 1;
    if (w > 1) div.style.gridColumn = `span ${w}`;

    // 高度
    const h = panelDef.height || 1;
    if (h > 1) div.classList.add('h-2');

    const title = panelDef.title || def.name || def.id;

    div.innerHTML = `
        <div class="panel-header">
            <span class="panel-title">${escHtml(title)}</span>
            <span class="panel-value" id="val-${def.id}">
                -- <span class="unit">${escHtml(def.unit || '')}</span>
            </span>
        </div>
        <div class="panel-chart" id="chart-${def.id}"></div>
    `;

    // 初始化图表
    initChart(def);

    return div;
}

// =========================================
// ApexCharts 初始化
// =========================================
function initChart(def) {
    const el = document.getElementById(`chart-${def.id}`);
    if (!el) return;

    const isGauge = def.chart_type === 'gauge';
    const color = def.color || '#36a2eb';

    let opts;

    if (isGauge) {
        // 径向仪表盘
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
    } else {
        // 折线/面积图
        opts = {
            chart: {
                type: 'line',
                height: '100%',
                sparkline: { enabled: true },
                animations: {
                    enabled: true,
                    dynamicAnimation: { speed: 500 },
                },
                toolbar: { show: false },
            },
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
                labels: { show: false },
                axisBorder: { show: false },
                axisTicks: { show: false },
            },
            yaxis: {
                labels: {
                    style: { colors: '#8b8fa5', fontSize: '10px' },
                    formatter: (v) => `${v.toFixed(1)}`,
                },
            },
            grid: {
                show: true,
                borderColor: '#2a2d3a',
                strokeDashArray: 4,
                xaxis: { lines: { show: false } },
            },
            tooltip: {
                theme: 'dark',
                x: { format: 'HH:mm:ss' },
                y: {
                    formatter: (v) => `${v.toFixed(1)} ${def.unit || ''}`,
                },
            },
            colors: [color],
        };
    }

    const chart = new ApexCharts(el, opts);
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

        // 更新数值
        if (valEl && m.value !== undefined && m.value !== null) {
            const num = typeof m.value === 'number' ? m.value : parseFloat(m.value);
            if (!isNaN(num)) {
                valEl.innerHTML = `${num.toFixed(1)} <span class="unit">${escHtml(m.unit || '')}</span>`;
            }
        }

        // 更新图表
        if (chart && m.history && m.history.length > 0) {
            const data = m.history.map(p => ({ x: p.t, y: p.v }));
            if (m.chart_type === 'gauge') {
                const lastVal = data[data.length - 1].y;
                chart.updateSeries([Math.min(100, Math.max(0, lastVal))]);
            } else {
                chart.updateSeries([{ data }]);
            }
        }

        // 数据源标记
        if (panel) {
            let tag = panel.querySelector('.source-tag');
            if (!tag) {
                tag = document.createElement('span');
                tag.className = 'source-tag';
                panel.appendChild(tag);
            }
            if (m.source === 'mock') {
                tag.textContent = '模拟';
                tag.className = 'source-tag mock';
            } else {
                tag.textContent = '实时';
                tag.className = 'source-tag';
            }
        }
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
// 工具
// =========================================
function escHtml(s) {
    if (!s) return '';
    const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    return String(s).replace(/[&<>"']/g, c => map[c]);
}

// =========================================
// 启动
// =========================================
document.addEventListener('DOMContentLoaded', () => {
    connectWS();
});
