# DataBoard 架构重设计文档

> 项目: 树莓派 5 (gaofengpi) 实时系统监控看板
> 日期: 2026-05-24
> 状态: 设计阶段

---

## 1. 需求分析

### 1.1 现状
dataBoard 是一个跑在树莓派上的实时系统监控看板，当前架构是前后端耦合的单体应用：

- FastAPI 同时承担 REST API、WebSocket 推送、静态文件服务三职责
- 前端 HTML+JS 通过 WebSocket 接收数据，无构建工具
- 后端查询 VictoriaMetrics（localhost:8428）获取指标数据
- 采集脚本通过 systemd 服务独立运行（collect_rpi.service）

### 1.2 现有功能清单（重构时全部保留，不增不减）

**数据采集层（不变）**
| 脚本 | 作用 | 运行方式 |
|---|---|---|
| scripts/collect_rpi.py | 采集树莓派系统指标 → 推 VictoriaMetrics | systemd |
| scripts/collect_openwrt.sh | 采集 OpenWrt (R66S) 指标 → 推 VictoriaMetrics | systemd |

**看板面板（4 行，3 种 chart_type）**
| 行 | 面板 | chart_type |
|---|---|---|
| 树莓派系统（默认展开） | IP 地址、运行时间、磁盘使用、Docker 状态 | stat |
| | CPU 温度（宽2）、内存使用（area）、CPU 负载 | line/area |
| | 风扇转速（宽2）、网速（宽2） | line |
| OpenWrt 系统（折叠） | CPU 负载、CPU 温度、OpenWrt 内存 | line/area |
| WAN 流量（折叠） | WAN 下载、WAN 上传 | line |
| LAN 流量（折叠） | LAN 接收、LAN 发送 | line |

**技术参数**
- 刷新间隔: 10 秒（可配）
- 历史缓冲: 300 点（约 50 分钟 @ 10s）
- 数据源: VictoriaMetrics（优先），Mock 回退（开发/调试）
- 前端图表: ApexCharts v4.7.0
- 配置格式: YAML（layout.yaml + metrics.yaml）

### 1.3 重构目标
1. **前后端彻底分离** — 前端独立项目，通过 REST API 通信
2. **单元测试覆盖** — pytest 覆盖 engine、client 层
3. **Docker 化部署** — Docker Compose 多服务编排
4. **零功能变更** — 任何新功能都不加，只改架构
5. **保持简洁** — 单用户，无权限系统，无重型前端框架

### 1.4 非功能需求
| 维度 | 要求 |
|---|---|
| 可维护性 | 模块化、可测试、有文档 |
| 部署便捷 | 一条 docker compose up 启动 |
| 性能 | 单用户 10s 轮询，后端响应 < 200ms |
| 向后兼容 | 采集脚本、VM 数据格式不变 |

---

## 2. 整体架构

### 2.1 架构图（文字描述）

```
┌──────────────────────────────────────────────────────────────────┐
│                        树莓派 5 (gaofengpi)                        │
│                                                                   │
│  ┌──────────────┐    ┌──────────────────┐    ┌───────────────┐  │
│  │ 采集层 (Host)  │    │   Docker 容器网络   │    │  数据存储      │  │
│  │              │    │                  │    │               │  │
│  │ collect_rpi  │───▶│  ┌────────────┐  │───▶│ VictoriaMetrics│  │
│  │ (systemd)    │    │  │  Backend    │  │    │ localhost:8428 │  │
│  │              │    │  │ FastAPI     │◀─│───│ (Host)         │  │
│  │ collect_owrt │───▶│  │ :8000       │  │    │               │  │
│  │ (systemd)    │    │  └─────┬──────┘  │    └───────────────┘  │
│  └──────────────┘    │        │ REST    │                        │
│                       │  ┌─────▼──────┐  │                        │
│                       │  │  Frontend   │  │                        │
│                       │  │ Nginx       │  │                        │
│                       │  │ :8766       │  │                        │
│                       │  └────────────┘  │                        │
│                       └──────────────────┘                        │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 数据流

```
[采集层]                    [存储层]                  [后端]                    [前端]
systemd 脚本 ──(push)──▶ VictoriaMetrics ◀──(query)── FastAPI ◀──(poll)── Nginx → 浏览器
              NDJSON/HTTP        HTTP API         REST API          HTTP
              10s 间隔                            响应 < 200ms      10s 间隔
```

### 2.3 核心设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 通信方式 | REST 轮询（替代 WebSocket） | 单用户场景下，10s 轮询比 WebSocket 更简单、更可靠；省去连接管理、重连逻辑、心跳等复杂度 |
| 前端框架 | 纯 HTML+JS（无框架） | 高峰哥偏好简洁；现有前端代码已是纯 JS，ApexCharts 自带渲染无需框架 |
| 前端服务 | Nginx（静态文件 + API 反向代理） | 成熟稳定，解决 CORS 问题，可作为前端 Docker 入口 |
| 后端包管理 | pip (requirements.txt) | 树莓派性能有限，poetry/PDM 等重型工具增加构建时间 |
| 配置加载 | 启动时读取 YAML（不支持热加载） | 保持简单，热加载可通过后续 watch 机制实现，不在此次范围内 |
| 数据采集 | 保持 systemd 服务（不进 Docker） | 采集需要访问宿主机系统资源（/sys、/proc等），放容器里反而不方便 |
| 历史缓冲 | 后端维护（300 点），前端不关心 | 前端只消费 `history` 数组，后端控制数据量 |

---

## 3. 模块划分与职责

### 3.1 项目目录结构

```
/home/gaofeng/dataBoard/
├── ARCHITECTURE.md              # 本设计文档
├── docker-compose.yml           # 多服务编排
├── README.md                    # 项目说明
│
├── backend/                     # Python 后端（独立 Docker 服务）
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── pyproject.toml           # 可选，用于 pytest 配置
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI 应用入口 + 路由注册
│   │   ├── config.py            # YAML 配置加载 + 校验
│   │   ├── models.py            # Pydantic 响应模型
│   │   └── core/
│   │       ├── __init__.py
│   │       ├── vm_client.py     # VictoriaMetrics HTTP 客户端
│   │       └── metrics_engine.py# 指标引擎（查询调度 + mock + 历史）
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py          # pytest fixtures（mock VM 等）
│       ├── test_vm_client.py    # VMClient 单元测试
│       └── test_metrics_engine.py # MetricsEngine 单元测试
│
├── frontend/                    # 前端静态文件（独立 Docker 服务）
│   ├── Dockerfile               # Nginx 构建
│   ├── nginx.conf               # 反向代理 /api/ → backend
│   ├── index.html
│   ├── css/
│   │   └── style.css
│   ├── js/
│   │   ├── dashboard.js         # 主逻辑（加载布局 + 轮询 + 渲染）
│   │   ├── api.js               # REST API 调用封装
│   │   └── utils.js             # 工具函数（格式化、转义等）
│   └── lib/
│       └── apexcharts.min.js    # ApexCharts v4.7.0
│
├── config/                      # 共享配置（挂载到 backend 容器）
│   ├── layout.yaml              # 看板布局（不变）
│   └── metrics.yaml             # 指标定义（不变）
│
└── scripts/                     # 采集脚本（宿主机 systemd，不变）
    ├── collect_rpi.py
    ├── collect_rpi.service
    ├── collect_openwrt.sh
    └── collect_openwrt.service
```

### 3.2 模块职责矩阵

| 模块 | 职责 | 技术栈 |
|---|---|---|
| **backend/app/main.py** | FastAPI 应用入口、路由注册（health/config/data）、启动生命周期 | FastAPI, uvicorn |
| **backend/app/config.py** | 加载 layout.yaml + metrics.yaml，校验字段完整性，提供单例配置对象 | PyYAML, pydantic |
| **backend/app/models.py** | 请求/响应 Pydantic 模型（MetricData, ConfigResponse, HealthResponse 等） | pydantic |
| **backend/app/core/vm_client.py** | VictoriaMetrics HTTP 查询封装（query, query_range, health），超时/重试/异常处理 | httpx |
| **backend/app/core/metrics_engine.py** | 按配置调度查询、mock 回退逻辑、历史缓冲维护（300 点 FIFO）、并发 gather | asyncio |
| **frontend/js/api.js** | REST API 调用封装：fetchConfig()、fetchData()，统一错误处理 | fetch API |
| **frontend/js/dashboard.js** | 布局渲染、图表初始化、数据更新（ApexCharts 操作）、stat 卡片格式化 | ApexCharts |
| **frontend/js/utils.js** | 工具函数：格式化运行时间、转义 HTML、节流等 | Vanilla JS |
| **scripts/*** | 系统指标采集 → 推 VM（不在本次重构范围内） | Python / Shell |

### 3.3 后端分层

```
┌──────────────────────────────────────────────────────────┐
│  Router 层 (routers/*)                                    │
│  ┌─────────────┐  ┌──────────┐  ┌──────────────┐        │
│  │ GET /api/    │  │ GET /api │  │ GET /api/     │        │
│  │ health       │  │ /config  │  │ data          │        │
│  └──────┬───────┘  └────┬─────┘  └──────┬────────┘        │
│         │               │               │                  │
├─────────┼───────────────┼───────────────┼──────────────────┤
│ Service 层 (core/)      │               │                  │
│         │               │               │                  │
│         ▼               ▼               ▼                  │
│  ┌─────────────────────────────────────────────┐          │
│  │         MetricsEngine                        │          │
│  │  - fetch(metric_id) → 单指标                  │          │
│  │  - fetch_all() → 全指标（并行）                │          │
│  │  - mock 回退                                  │          │
│  │  - 历史缓冲 FIFO (300点)                       │          │
│  └─────────────────────┬───────────────────────┘          │
│                        │                                   │
│                        ▼                                   │
│  ┌─────────────────────────────────────────────┐          │
│  │           VMClient                            │          │
│  │  - health() → bool                           │          │
│  │  - query(promql) → [{labels, value}]         │          │
│  │  - query_range(promql, start, end, step)     │          │
│  └─────────────────────┬───────────────────────┘          │
│                        │                                   │
└────────────────────────┼───────────────────────────────────┘
                         │ HTTP
                         ▼
               VictoriaMetrics (localhost:8428)
```

---

## 4. 前后端 API 接口定义

### 4.1 接口总览

| 方法 | 路径 | 说明 | 频率 |
|---|---|---|---|
| GET | `/api/health` | 健康检查探活 | 前端启动/重连时 |
| GET | `/api/config` | 获取看板布局 + 指标定义 | 页面加载时一次 |
| GET | `/api/data` | 获取所有指标最新数据 + 历史 | 每 refresh_interval 秒 |

### 4.2 详细定义

#### `GET /api/health`

**响应：**
```json
{
    "status": "ok",
    "vm_connected": true,
    "uptime_seconds": 123456
}
```

`vm_connected` 表示后端能否正常连接到 VictoriaMetrics。

---

#### `GET /api/config`

**说明：** 返回完整的布局配置和指标定义，前端据此渲染看板。

**响应：**
```json
{
    "title": "系统监控看板",
    "refresh_interval": 10,
    "layout": [
        {
            "row": "树莓派系统",
            "collapsed": false,
            "panels": [
                {"metric": "rpi_ip_info", "width": 1, "height": 1},
                {"metric": "rpi_uptime", "width": 1, "height": 1},
                ...
            ]
        }
    ],
    "metrics": [
        {
            "id": "rpi_cpu_temp",
            "name": "CPU 温度",
            "unit": "°C",
            "chart_type": "line",
            "color": "#ff6384",
            "refresh_interval": 10
        }
    ]
}
```

**注意：** 响应中的 metrics 只包含前端需要的信息（不含 `query`、`mock` 等后端敏感字段），由 `config.py` 负责清洗。

---

#### `GET /api/data`

**说明：** 获取所有指标的最新值 + 历史数据。这是前端轮询的核心端点。

**响应：**
```json
{
    "timestamp": 1712345678.123,
    "metrics": [
        {
            "id": "rpi_cpu_temp",
            "name": "CPU 温度",
            "unit": "°C",
            "chart_type": "line",
            "color": "#ff6384",
            "value": 52.3,
            "source": "vm",
            "timestamp": 1712345678.123,
            "labels": {},
            "history": [
                {"t": 1712345678000, "v": 52.1},
                {"t": 1712345668000, "v": 51.8}
            ]
        },
        {
            "id": "rpi_disk_stat",
            "name": "磁盘使用",
            "unit": "GB",
            "chart_type": "stat",
            "color": "#ff9f40",
            "value": 23.5,
            "source": "vm",
            "timestamp": 1712345678.123,
            "labels": {},
            "history": null
        }
    ]
}
```

**字段说明：**

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 指标唯一标识 |
| `name` | string | 显示名称 |
| `unit` | string | 单位（°C, %, RPM, KB/s 等） |
| `chart_type` | string | `line` / `area` / `stat` |
| `color` | string | 十六进制颜色值 |
| `value` | number/string|null | 当前值（stat 卡片可能是字符串） |
| `source` | string | `"vm"` 或 `"mock"` |
| `timestamp` | float | Unix 时间戳（秒） |
| `labels` | object | VM 返回的 labels（用于 IP 等 stat 卡片） |
| `history` | array|null | 时序历史（stat 类型为 null） |

**stat 类型的特殊处理：**
- `rpi_ip_info`: `labels.ip` 显示 IP 地址
- `rpi_uptime`: `value` 为秒数，前端 formatUptime() 格式化
- `rpi_disk_stat` + `__rpi_disk_total`: 前端合并显示 `used/total GB`
- `rpi_docker_status` + `__rpi_docker_total` + `__rpi_docker_disk`: 前端合并显示 `running/total · X.XGB`

---

### 4.3 前端 API 调用封装

```javascript
// frontend/js/api.js

const API_BASE = '/api';  // Nginx 反向代理，同源

async function fetchConfig() {
    const res = await fetch(`${API_BASE}/config`);
    if (!res.ok) throw new Error(`Config fetch failed: ${res.status}`);
    return res.json();
}

async function fetchData() {
    const res = await fetch(`${API_BASE}/data`);
    if (!res.ok) throw new Error(`Data fetch failed: ${res.status}`);
    return res.json();
}
```

---

## 5. 技术选型及理由

### 5.1 后端

| 组件 | 选型 | 版本 | 理由 |
|---|---|---|---|
| Web 框架 | FastAPI | ≥0.110 | 高性能异步、原生 Pydantic 支持、自动 OpenAPI 文档 |
| ASGI 服务器 | uvicorn | ≥0.30 | FastAPI 标准搭档，轻量 |
| HTTP 客户端 | httpx | ≥0.27 | 异步 HTTP 请求，支持连接池 |
| 数据校验 | pydantic | ≥2.0 | FastAPI 原生集成，响应模型定义 |
| YAML 解析 | PyYAML | ≥6.0 | 读取 layout/metrics 配置 |
| 测试框架 | pytest | ≥8.0 | 最广泛使用的 Python 测试框架 |
| 异步测试 | pytest-asyncio | ≥0.23 | 支持 async 测试函数 |
| HTTP mock | respx | ≥0.21 | 模拟 httpx 请求，专为 pytest 设计 |

### 5.2 前端

| 组件 | 选型 | 版本 | 理由 |
|---|---|---|---|
| 框架 | 无（纯 HTML+JS） | — | 高峰哥偏好简洁；当前功能不需要框架 |
| 图表库 | ApexCharts | v4.7.0 | 已在使用，满足所有图表需求（line/area/stat） |
| HTTP | fetch API | — | 浏览器原生，无需额外依赖 |
| CSS | 手写 | — | 单文件，暗色主题，响应式栅格 |
| HTTP 服务器 | Nginx | latest | 静态文件 + API 反向代理，稳定高效 |

### 5.3 Docker

| 组件 | 选型 | 理由 |
|---|---|---|
| 容器引擎 | Docker + Docker Compose | 标准容器编排 |
| Backend 基础镜像 | python:3.12-slim | 小体积（~120MB），阿里云/中科大镜像加速 |
| Frontend 基础镜像 | nginx:alpine | ~25MB，仅静态文件 |
| 网络模式 | bridge (default) | 容器间通过 service name 通信 |

### 5.4 为什么不用 ...

| 技术 | 不用的理由 |
|---|---|
| WebSocket | 单用户 10s 轮询足够，省去连接管理、心跳、重连等复杂度 |
| Vue / React | 当前页面无复杂交互，纯 JS 更轻量（零构建步骤） |
| Poetry / PDM | 树莓派性能有限，pip 已足够；部署用 requirements.txt |
| Grafana | 高峰哥已有自建看板，且 ApexCharts 满足需求，迁移成本高 |
| gin / go | Python 生态更熟悉，代码量小，性能瓶颈在 VM 不在 Python |
| 热加载配置 | 属于新功能，本次不引入；可后续通过文件 watch + 缓存过期实现 |

---

## 6. Docker 部署方案

### 6.1 docker-compose.yml

```yaml
version: "3.8"

services:
  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: databoard-backend
    ports:
      - "127.0.0.1:8000:8000"    # 仅本地监听，通过 Nginx 暴露
    volumes:
      - ./config:/app/config:ro   # 配置只读挂载
    environment:
      - VM_URL=http://host.docker.internal:8428
      - DATABOARD_HOST=0.0.0.0
      - DATABOARD_PORT=8000
    restart: unless-stopped
    extra_hosts:
      - "host.docker.internal:host-gateway"  # macOS/Windows 兼容
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/health"]
      interval: 30s
      timeout: 5s
      retries: 3

  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    container_name: databoard-frontend
    ports:
      - "0.0.0.0:8766:80"    # 对外暴露
    depends_on:
      - backend
    restart: unless-stopped
```

**网络拓扑说明：**

```
宿主机:8766  ──▶ frontend:80  ──(/api/*)──▶ backend:8000  ──▶ host.docker.internal:8428 (VM)
```

### 6.2 Backend Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝源码
COPY app/ ./app/

# 健康检查用 curl
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 6.3 Frontend Dockerfile

```dockerfile
FROM nginx:alpine

# 删除默认配置
RUN rm /etc/nginx/conf.d/default.conf

# 自定义配置（含反向代理）
COPY nginx.conf /etc/nginx/conf.d/

# 静态文件
COPY index.html /usr/share/nginx/html/
COPY css/ /usr/share/nginx/html/css/
COPY js/ /usr/share/nginx/html/js/
COPY lib/ /usr/share/nginx/html/lib/

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
```

### 6.4 nginx.conf（关键配置）

```nginx
server {
    listen 80;
    server_name _;

    root /usr/share/nginx/html;
    index index.html;

    # 首选压缩
    gzip on;
    gzip_types text/html text/css application/json application/javascript;

    # API 反向代理到 backend
    location /api/ {
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 10s;
    }

    # 静态文件
    location / {
        try_files $uri $uri/ /index.html;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }
}
```

### 6.5 部署步骤

```bash
# 1. 确认 VictoriaMetrics 正在运行（宿主机）
curl http://localhost:8428/health

# 2. 克隆/进入项目
cd /home/gaofeng/dataBoard

# 3. 启动（后台）
docker compose up -d

# 4. 检查状态
docker compose ps
docker compose logs backend
docker compose logs frontend

# 5. 访问
# 浏览器打开 http://192.168.100.6:8766

# 6. 更新
docker compose pull
docker compose up -d --build

# 7. 停止
docker compose down
```

### 6.6 数据采集层（不变）

采集脚本继续通过 systemd 在宿主机上运行，不进入 Docker：

```bash
# 当前状态
systemctl status collect_rpi.service
systemctl status collect_openwrt.service

# 采集脚本推送指标到 VictoriaMetrics（宿主机的 localhost:8428）
# Docker backend 通过 host.docker.internal:8428 访问同一个 VM
```

**为什么采集脚本不进 Docker：**
1. 需要访问宿主机 `/sys`、`/proc` 等系统接口
2. 已有稳定的 systemd 配置，无需改动
3. 采集脚本与看板解耦，独立升级/重启

---

## 7. 测试策略

### 7.1 测试范围

| 模块 | 测试级别 | 测试文件 | 覆盖率目标 |
|---|---|---|---|
| VMClient | 单元测试 | `test_vm_client.py` | ≥90% |
| MetricsEngine | 单元测试 | `test_metrics_engine.py` | ≥85% |
| Config | 单元测试 | `test_config.py` | ≥90% |
| API Router | 集成测试（后续） | — | — |

本次重点覆盖 **core 层**（vm_client + metrics_engine），这是业务逻辑最密集的部分。

### 7.2 测试技术

| 工具 | 用途 |
|---|---|
| pytest | 测试框架 |
| pytest-asyncio | 异步测试支持（mark.asyncio） |
| respx | 模拟 httpx HTTP 请求 |
| pytest-cov | 覆盖率报告（可选） |

### 7.3 测试用例设计

#### test_vm_client.py

```python
"""test_vm_client.py — VictoriaMetrics 客户端测试"""

# test_health_ok
#   mock: GET /health → 200
#   assert: result == True

# test_health_fail
#   mock: GET /health → 500
#   assert: result == False

# test_health_connection_error
#   mock: 连接超时
#   assert: result == False

# test_query_valid
#   mock: GET /api/v1/query?query=xxx → 标准响应
#   assert: 返回标准化 [{labels, value}]

# test_query_no_results
#   mock: 返回空 result
#   assert: 返回 []

# test_query_empty_promql
#   assert: 返回 []（不发起 HTTP 请求）

# test_query_vm_error
#   mock: 返回 503
#   assert: 返回 []（不抛异常）

# test_query_range_valid
#   mock: GET /api/v1/query_range → 标准响应
#   assert: 返回原始 result 列表

# test_query_range_empty_promql
#   assert: 返回 []
```

#### test_metrics_engine.py

```python
"""test_metrics_engine.py — 指标引擎测试"""

# test_fetch_metric_from_vm
#   mock: VMClient.query → [{value: 52.3, labels: {}}]
#   assert: 返回完整 MetricData，source="vm"

# test_fetch_fallback_to_mock
#   mock: VMClient.query → []（无数据）
#   assert: 返回 MetricData，value 在 mock 区间内，source="mock"

# test_fetch_no_query_no_mock
#   mock: 配置无 query 无 mock
#   assert: 返回 None

# test_fetch_all
#   mock: 3 个指标全部从 VM 返回
#   assert: 返回 3 条记录的 list

# test_fetch_all_mixed
#   mock: 2 个 VM + 1 个 mock + 1 个 None
#   assert: 返回 3 条（过滤掉 None）

# test_history_accumulation
#   mock: 连续 fetch 5 次
#   assert: history 数组长度 == 5

# test_history_max_300
#   mock: 连续 fetch 350 次
#   assert: history 数组长度 == 300（FIFO 淘汰）

# test_fetch_unknown_metric_id
#   assert: 返回 None
```

### 7.4 conftest.py 关键 fixtures

```python
import pytest
import respx
from httpx import Response
from app.core.vm_client import VMClient
from app.core.metrics_engine import MetricsEngine

@pytest.fixture
def mock_vm():
    """Mock 的 VM HTTP 客户端"""
    with respx.mock(base_url="http://localhost:8428") as respx_mock:
        yield respx_mock

@pytest.fixture
def vm_client():
    """真实的 VMClient 实例（但 HTTP 被 mock）"""
    return VMClient(base_url="http://localhost:8428")

@pytest.fixture
def sample_metrics_config():
    return [
        {"id": "rpi_cpu_temp", "name": "CPU温度", "query": 'rpi_cpu_temp{host="gaofengpi"}', 
         "chart_type": "line", "color": "#ff6384", "unit": "°C",
         "mock": {"min": 40, "max": 75, "drift": 0.3}},
        {"id": "rpi_mem_usage", "name": "内存使用", "query": 'rpi_mem_usage_pct{host="gaofengpi"}',
         "chart_type": "area", "color": "#fdcb6e", "unit": "%",
         "mock": {"min": 30, "max": 85, "drift": 0.5}},
        {"id": "rpi_ip_info", "name": "IP地址", "chart_type": "stat", "color": "#36a2eb",
         "unit": "", "query": 'rpi_ip_info{host="gaofengpi"}', "label_key": "ip"},
    ]

@pytest.fixture
def engine(vm_client, sample_metrics_config):
    return MetricsEngine(vm_client, sample_metrics_config)
```

### 7.5 测试运行

```bash
# 后端容器内运行
cd /home/gaofeng/dataBoard/backend

# 安装测试依赖
pip install pytest pytest-asyncio respx pytest-cov

# 运行测试
pytest tests/ -v

# 带覆盖率
pytest tests/ -v --cov=app.core --cov-report=term-missing

# Docker 内运行
docker compose exec backend pytest tests/ -v
```

### 7.6 测试断言示例

```python
@pytest.mark.asyncio
async def test_fetch_metric_from_vm(mock_vm, engine, vm_client):
    """从 VM 成功获取指标"""
    mock_vm.get("/api/v1/query").respond(
        json={
            "status": "success",
            "data": {
                "result": [
                    {"metric": {"host": "gaofengpi"}, "value": [1712345678, "52.3"]}
                ]
            }
        }
    )
    
    result = await engine.fetch("rpi_cpu_temp")
    
    assert result is not None
    assert result["id"] == "rpi_cpu_temp"
    assert result["value"] == 52.3
    assert result["source"] == "vm"
    assert len(result["history"]) == 1
```

---

## 8. 迁移步骤

从当前单体架构迁移到新架构的步骤：

### 8.1 第一阶段：代码重组（后端）

```bash
# 1. 创建 backend/app/ 结构
mkdir -p backend/app/core backend/tests

# 2. 从 app.py 抽取：
#   - main.py → FastAPI app（仅 REST 路由）
#   - config.py → YAML 配置加载
#   - models.py → Pydantic 模型
#   - app.py 不再存在

# 3. 从大文件 app.py 中剥离：
#   - /api/health → HealthRouter
#   - /api/config → ConfigRouter
#   - /api/data → DataRouter
#   - 原 push_loop / WebSocket 代码 → 移除
```

### 8.2 第二阶段：前端分离

```bash
# 1. 创建 frontend/ 目录
mkdir -p frontend/css frontend/js frontend/lib

# 2. 从 static/ 迁移文件
#   - index.html → 修改 API 调用（WS → REST）
#   - dashboard.js → 拆分 api.js + utils.js + 重写轮询逻辑
#   - style.css → 直接复制
#   - apexcharts.min.js → 复制

# 3. 移除 WebSocket 相关代码，替换为 REST 轮询
```

### 8.3 第三阶段：Docker + 测试

```bash
# 1. 编写 Dockerfile（backend + frontend）
# 2. 编写 docker-compose.yml
# 3. 编写 nginx.conf
# 4. 编写测试文件
# 5. 验证部署
```

### 8.4 回滚方案

```bash
# 旧版仍可用（原地保留）
cd /home/gaofeng/dataBoard
.venv/bin/python app.py

# 快速切换
docker compose down          # 停新版
.venv/bin/python app.py &    # 启旧版
```

---

## 9. 关键设计考量

### 9.1 为什么用 REST 轮询替代 WebSocket

| 维度 | WebSocket（当前） | REST 轮询（重构） |
|---|---|---|
| 连接管理 | 需要心跳、重连、并发控制 | 无状态，天然简单 |
| 资源消耗 | 每个客户端一个长连接 + 独立 push_task | 无状态请求，10s 间隔 |
| 调试 | 需要 WebSocket 客户端工具 | curl 即可调试 |
| 浏览器兼容 | 所有现代浏览器支持 | 所有浏览器支持 |
| 单用户场景 | 复杂能力过剩 | 恰好够用 |
| 前端代码量 | ~150 行 WS 逻辑（连接/重连/消息路由） | ~20 行 fetch 调用 |

### 9.2 stat 卡片的组合数据方案

当前 stat 卡片（如磁盘使用、Docker 状态）需要从多个指标合并数据：

- `rpi_disk_stat` + `__rpi_disk_total` → `used/total GB`
- `rpi_docker_status` + `__rpi_docker_total` + `__rpi_docker_disk` → `running/total · X.XGB`

重构方案：后端在 `/api/data` 响应中保留这些隐藏指标（`_hidden` chart_type），前端按原逻辑合并。或者也可以让后端做合并处理，但为保持 "只重构、不改变行为"，建议保留现有前端合并逻辑。

### 9.3 配置管理

- layout.yaml 和 metrics.yaml 保持现有格式不变
- 通过 Docker volume 挂载到 backend 容器
- 启动时加载，如需更新配置：`docker compose restart backend`
- **不在此次范围：** 配置热加载、API 动态更新

### 9.4 安全考虑

- 单用户场景，无鉴权
- Backend 端口（8000）仅监听 127.0.0.1，不暴露到外部
- Frontend 端口（8766）监听 0.0.0.0，但仅在局域网内可访问
- 使用 `host.docker.internal` 访问宿主机 VictoriaMetrics

---

## 10. 附录

### A. 依赖清单

#### backend/requirements.txt
```
fastapi>=0.110.0
uvicorn[standard]>=0.30.0
httpx>=0.27.0
pyyaml>=6.0
pydantic>=2.0

# 测试（dev）
pytest>=8.0
pytest-asyncio>=0.23.0
respx>=0.21.0
pytest-cov>=5.0
```

#### 前端无构建依赖
```
前端依赖均为静态文件，托管于 frontend/lib/ 下：
- apexcharts.min.js (v4.7.0)
```

### B. 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `VM_URL` | `http://host.docker.internal:8428` | VictoriaMetrics 地址 |
| `DATABOARD_HOST` | `0.0.0.0` | 后端监听地址 |
| `DATABOARD_PORT` | `8000` | 后端监听端口 |
| `CONFIG_DIR` | `/app/config` | YAML 配置路径 |

### C. 与当前代码的差异总结

| 维度 | 当前 | 重构后 |
|---|---|---|
| 通信协议 | WebSocket (实时推送) | REST (10s 轮询) |
| 前端服务 | FastAPI 静态挂载 | Nginx 独立服务 |
| 后端端口 | 8766（统一） | 8000（内部） |
| 前端端口 | — | 8766（对外） |
| 代码结构 | 单体 app.py | 模块化 app/ 包 |
| 测试 | 无 | pytest (core 层) |
| 部署 | systemd + python | Docker Compose |
| 配置加载 | 启动时 | 启动时（挂载 volume） |

---

*文档结束*
