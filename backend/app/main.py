"""
DataBoard — FastAPI 应用入口

3 个 REST 端点:
  - GET /api/health → {status, vm_connected, uptime_seconds}
  - GET /api/config → {layout, metrics}（从 YAML 加载）
  - GET /api/data → 所有指标的最新值 + 历史
"""

import logging
import time
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import load_layout, load_metrics, clean_metric_for_frontend
from app.core.vm_client import VMClient
from app.core.metrics_engine import MetricsEngine

# ---- 日志 ----
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("databoard")

# ---- 启动时间 ----
_start_time = time.time()

# ---- 配置加载 ----
layout_cfg = load_layout()
metrics_cfg = load_metrics()
metrics_list = metrics_cfg.get("metrics", [])

# ---- FastAPI ----
app = FastAPI(title="DataBoard")

# CORS: 允许来自前端
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- VictoriaMetrics 初始化 ----
vm = VMClient()
engine = MetricsEngine(vm, metrics_list)

REFRESH_INTERVAL = layout_cfg.get("refresh_interval", 10)


# ========================
# REST 端点
# ========================
@app.get("/api/health")
async def api_health():
    """探活"""
    vm_ok = await vm.health()
    uptime = time.time() - _start_time
    return {
        "status": "ok",
        "vm_connected": vm_ok,
        "uptime_seconds": round(uptime, 1),
    }


@app.get("/api/config")
async def api_config():
    """获取看板布局 + 指标定义"""
    refresh = layout_cfg.get("refresh_interval", 10)
    cleaned_metrics = [clean_metric_for_frontend(m) for m in metrics_list]
    return {
        "title": layout_cfg.get("title", "系统监控看板"),
        "refresh_interval": refresh,
        "layout": layout_cfg.get("layout", []),
        "metrics": cleaned_metrics,
    }


@app.get("/api/data")
async def api_data():
    """获取所有指标的最新数据 + 历史"""
    from datetime import datetime, timezone
    metrics_data = await engine.fetch_all()
    return {
        "timestamp": datetime.now(timezone.utc).timestamp(),
        "metrics": metrics_data,
    }
