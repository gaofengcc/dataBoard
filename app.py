"""
DataBoard — 实时系统监控看板

FastAPI + WebSocket 服务，从 VictoriaMetrics 拉数据推给前端。

使用:
    cd ~/dataBoard
    .venv/bin/python app.py

或:
    .venv/bin/uvicorn app:app --host 0.0.0.0 --port 8766
"""

import asyncio
import logging
import os
import json
import yaml
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from core.vm_client import VMClient
from core.metrics_engine import MetricsEngine

# ---- 日志 ----
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("databoard")

# ---- 配置加载 ----
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_yaml(name: str) -> dict:
    path = os.path.join(BASE_DIR, "config", name)
    with open(path) as f:
        return yaml.safe_load(f)


layout_cfg = load_yaml("layout.yaml")
metrics_cfg = load_yaml("metrics.yaml")
metrics_list = metrics_cfg.get("metrics", [])

# ---- FastAPI ----
app = FastAPI(title="DataBoard")

# 挂载静态文件
static_dir = os.path.join(BASE_DIR, "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# ---- 客户端管理 ----
connected_clients: set[WebSocket] = set()

# ---- VictoriaMetrics 初始化 ----
vm = VMClient()
engine = MetricsEngine(vm, metrics_list)

# 默认刷新间隔（秒）
REFRESH_INTERVAL = layout_cfg.get("refresh_interval", 5)


# ========================
# HTTP 端点
# ========================
@app.get("/")
async def index():
    """返回首页"""
    html_path = os.path.join(BASE_DIR, "static", "index.html")
    with open(html_path) as f:
        return HTMLResponse(f.read())


@app.get("/api/health")
async def api_health():
    """探活"""
    vm_ok = await vm.health()
    return {"status": "ok", "vm_connected": vm_ok}


@app.get("/api/metrics")
async def api_metrics():
    """获取所有指标定义"""
    return {"metrics": metrics_list, "layout": layout_cfg}


# ========================
# WebSocket：实时推送
# ========================
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.add(ws)
    logger.info("Client connected (%d active)", len(connected_clients))

    # 每个客户端的独立推送任务
    push_task: asyncio.Task | None = None

    try:
        while True:
            msg = await ws.receive_text()

            try:
                data = json.loads(msg)
            except json.JSONDecodeError:
                continue

            if data.get("type") == "init":
                # 发送布局和指标定义
                await ws.send_json({
                    "type": "layout",
                    "layout": layout_cfg,
                    "metrics": metrics_list,
                })

                # 启动推送循环（如果没有正在运行的）
                if push_task is None or push_task.done():
                    push_task = asyncio.create_task(push_loop(ws))

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    finally:
        connected_clients.discard(ws)
        if push_task and not push_task.done():
            push_task.cancel()


async def push_loop(ws: WebSocket):
    """向单个客户端持续推送数据"""
    try:
        while True:
            metrics_data = await engine.fetch_all()
            payload = {
                "type": "data",
                "timestamp": datetime.now(timezone.utc).timestamp(),
                "metrics": metrics_data,
            }
            try:
                await ws.send_json(payload)
            except Exception:
                break
            await asyncio.sleep(REFRESH_INTERVAL)
    except asyncio.CancelledError:
        pass


# ========================
# 启动
# ========================
if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("DATABOARD_HOST", "0.0.0.0")
    port = int(os.environ.get("DATABOARD_PORT", "8766"))

    logger.info("DataBoard starting on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")
