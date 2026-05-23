"""
指标引擎 — 按 config 调度查询、mock 回退、数据格式化
"""

import asyncio
import random
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class MockMetricGenerator:
    """当 VM 无数据时，生成逼真的模拟数据用于开发"""

    def __init__(self, mock_config: dict):
        self.min_v = mock_config.get("min", 0)
        self.max_v = mock_config.get("max", 100)
        self.drift = mock_config.get("drift", 0.5)
        self._value = (self.min_v + self.max_v) / 2
        self._trend = 0

    def next(self) -> float:
        self._trend += random.uniform(-self.drift, self.drift)
        self._trend = max(-self.drift * 5, min(self.drift * 5, self._trend))
        self._value += self._trend + random.uniform(-self.drift, self.drift)
        self._value = max(self.min_v, min(self.max_v, self._value))
        return round(self._value, 2)


class MetricsEngine:
    """
    按配置调度指标查询
    优先查 VM，VM 无数据回退 mock
    """

    def __init__(self, vm_client, metrics_config: list[dict]):
        self.vm = vm_client
        self.metrics = {m["id"]: m for m in metrics_config}
        self._mocks: dict[str, MockMetricGenerator] = {}
        self._history: dict[str, list[tuple[float, float]]] = {}  # id -> [(timestamp, value)]

    def _get_mock(self, metric_id: str) -> MockMetricGenerator:
        if metric_id not in self._mocks:
            cfg = self.metrics[metric_id].get("mock", {})
            self._mocks[metric_id] = MockMetricGenerator(cfg)
        return self._mocks[metric_id]

    async def fetch(self, metric_id: str) -> dict | None:
        """获取单个指标当前值"""
        cfg = self.metrics.get(metric_id)
        if not cfg:
            return None

        now = datetime.now(timezone.utc).timestamp()
        value = None
        source = "mock"

        # 1) 查 VM
        if cfg.get("query"):
            results = await self.vm.query(cfg["query"])
            if results:
                try:
                    value = float(results[0]["value"][1])
                    source = "vm"
                except (ValueError, KeyError, IndexError):
                    pass

        # 2) VM 无数据 → mock
        if value is None and cfg.get("mock"):
            value = self._get_mock(metric_id).next()
            source = "mock"

        if value is None:
            return None

        # 记入历史
        if metric_id not in self._history:
            self._history[metric_id] = []
        self._history[metric_id].append((now, value))
        # 只保留最近 300 个点 (5s 间隔 × 300 = 25min)
        if len(self._history[metric_id]) > 300:
            self._history[metric_id] = self._history[metric_id][-300:]

        return {
            "id": metric_id,
            "name": cfg.get("name", metric_id),
            "unit": cfg.get("unit", ""),
            "chart_type": cfg.get("chart_type", "line"),
            "color": cfg.get("color", "#36a2eb"),
            "value": value,
            "source": source,
            "timestamp": now,
            "history": [
                {"t": int(ts * 1000), "v": v}
                for ts, v in self._history[metric_id]
            ],
        }

    async def fetch_all(self) -> list[dict]:
        """获取所有指标数据"""
        tasks = [self.fetch(mid) for mid in self.metrics]
        results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]
