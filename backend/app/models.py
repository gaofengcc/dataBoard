"""
DataBoard — Pydantic 响应模型
"""

from typing import Any, Optional
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    vm_connected: bool
    uptime_seconds: float


class MetricHistoryPoint(BaseModel):
    t: int  # 毫秒时间戳
    v: float


class MetricData(BaseModel):
    id: str
    name: str
    unit: str
    chart_type: str
    color: str
    value: Any  # float, str, or None
    source: str  # "vm" or "mock"
    timestamp: float
    labels: dict[str, Any]
    history: Optional[list[MetricHistoryPoint]] = None
    refresh_interval: Optional[int] = None


class DataResponse(BaseModel):
    timestamp: float
    metrics: list[MetricData]


class PanelDef(BaseModel):
    metric: str
    width: int = 1
    height: int = 1


class LayoutRow(BaseModel):
    row: str
    collapsed: bool = False
    panels: list[PanelDef] = []


class ConfigResponse(BaseModel):
    title: str
    refresh_interval: int
    layout: list[LayoutRow]
    metrics: list[dict]
