"""
指标引擎 — 按 config 调度查询、mock 回退、数据格式化
逻辑完全保留自旧版 core/metrics_engine.py：
  - VM 优先 → mock 回退 → 300 点 FIFO 历史缓冲区
  - 10s 间隔 × 300 点 = 50 分钟
支持通过 refresh_interval 配置
"""

import asyncio
import random
import logging
from datetime import datetime, timezone

from app.core.vm_client import VMClient, VMClientError

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

    MAX_HISTORY = 300  # 10s 间隔 × 300 点 = 50 分钟

    def __init__(self, vm_client: VMClient, metrics_config: list[dict]):
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
        vm_labels = {}

        # 1) 查 VM
        if cfg.get("query"):
            try:
                results = await self.vm.query(cfg["query"])
                if results:
                    try:
                        value = float(results[0]["value"])
                        source = "vm"
                        # 保留 labels 用于 stat 卡片（如 IP 地址）
                        vm_labels = results[0].get("labels", {})
                    except (ValueError, KeyError, IndexError):
                        pass
            except VMClientError as e:
                logger.debug("VM query failed for %s: %s", metric_id, e)
                # VM 异常不阻断，继续尝试 mock

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
        # 只保留最近 300 个点 (10s 间隔 × 300 = 50min)
        if len(self._history[metric_id]) > self.MAX_HISTORY:
            self._history[metric_id] = self._history[metric_id][-self.MAX_HISTORY:]

        return {
            "id": metric_id,
            "name": cfg.get("name", metric_id),
            "unit": cfg.get("unit", ""),
            "chart_type": cfg.get("chart_type", "line"),
            "color": cfg.get("color", "#36a2eb"),
            "value": value,
            "source": source,
            "timestamp": now,
            "labels": vm_labels if source == "vm" else {},
            "history": [
                {"t": int(ts * 1000), "v": v}
                for ts, v in self._history[metric_id]
            ],
        }

    async def fetch_all(self) -> list[dict]:
        """获取所有指标数据（并行查询）"""
        tasks = [self._fetch_or_multi(mid) for mid in self.metrics]
        results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]

    async def _fetch_or_multi(self, metric_id: str) -> dict | None:
        """根据 chart_type 选择对应的查询方式"""
        cfg = self.metrics.get(metric_id)
        if not cfg:
            return None
        ct = cfg.get("chart_type", "line")
        if ct == "multi_line":
            return await self.fetch_multi(metric_id)
        if ct == "heatmap":
            return await self.fetch_heatmap(metric_id)
        if ct in ("bar_vertical", "bar_horizontal"):
            return await self.fetch_bar(metric_id)
        if ct in ("gauge", "gauge_bool"):
            return await self.fetch_gauge_extra(metric_id)
        return await self.fetch(metric_id)

    async def fetch_multi(self, metric_id: str) -> dict | None:
        """
        多系列查询：范围查询获取历史 + 即时查询获取当前值
        用于 multi_line 图表（如按 room 拆分的温湿度）
        """
        cfg = self.metrics.get(metric_id)
        if not cfg:
            return None

        now = datetime.now(timezone.utc).timestamp()
        split_by = cfg.get("split_by", "room")
        source = "mock"
        lookback = self.MAX_HISTORY * 10  # 300点 * 10s ≈ 50分钟

        series_data = []
        if cfg.get("query"):
            try:
                # 1) 范围查询获取历史
                range_results = await self.vm.query_range(
                    cfg["query"],
                    start=now - lookback,
                    end=now,
                    step=10,
                )
                if range_results:
                    source = "vm"
                    for res in range_results:
                        label_val = res["labels"].get(split_by, "unknown")
                        values = res.get("values", [])
                        if not values:
                            continue

                        hist_key = f"{metric_id}__{label_val}"
                        # 填充历史缓冲区
                        self._history[hist_key] = [
                            (float(ts), float(v))
                            for ts, v in values
                            if v not in ("", "null", None)
                        ][-self.MAX_HISTORY:]

                        cur_val = float(values[-1][1]) if values else 0
                        series_data.append({
                            "name": label_val,
                            "value": cur_val,
                            "timestamp": now,
                            "labels": res.get("labels", {}),
                            "history": [
                                {"t": int(ts * 1000), "v": v}
                                for ts, v in self._history[hist_key]
                            ],
                        })
            except VMClientError as e:
                logger.debug("VM multi query failed for %s: %s", metric_id, e)

        if not series_data:
            return None

        return {
            "id": metric_id,
            "name": cfg.get("name", metric_id),
            "unit": cfg.get("unit", ""),
            "chart_type": "multi_line",
            "color": cfg.get("color", "#36a2eb"),
            "value": None,
            "source": source,
            "timestamp": now,
            "labels": {},
            "history": None,
            "series": series_data,
        }

    async def fetch_heatmap(self, metric_id: str) -> dict | None:
        """
        门窗&人体状态矩阵：查 ha_door 和 ha_presence 两个指标，
        返回 cells=[{row, col, value, label}]
        """
        cfg = self.metrics.get(metric_id)
        if not cfg:
            return None

        now = datetime.now(timezone.utc).timestamp()
        cells = []

        async def _instant(query):
            try:
                return await self.vm.query(query)
            except Exception:
                return []

        door_results = await _instant(cfg.get("query_door", "ha_door"))
        presence_results = await _instant(cfg.get("query_presence", "ha_presence"))

        DOOR_LABEL = {"off": "关", "on": "开", "0": "关", "1": "开"}
        PRESENCE_LABEL = {"off": "无人", "on": "有人", "0": "无人", "1": "有人"}

        for r in door_results:
            name = r["labels"].get("name", "?")
            v = float(r["value"]) if r["value"] not in (None, "") else 0
            cells.append({"row": "门窗", "col": name, "value": v,
                          "label": DOOR_LABEL.get(str(int(v)), "?")})

        for r in presence_results:
            name = r["labels"].get("name", "?")
            v = float(r["value"]) if r["value"] not in (None, "") else 0
            cells.append({"row": "人体", "col": name, "value": v,
                          "label": PRESENCE_LABEL.get(str(int(v)), "?")})

        if not cells:
            return None

        return {
            "id": metric_id,
            "name": cfg.get("name", metric_id),
            "unit": cfg.get("unit", ""),
            "chart_type": "heatmap",
            "color": cfg.get("color", "#00e396"),
            "value": None,
            "source": "vm",
            "timestamp": now,
            "labels": {},
            "history": None,
            "cells": cells,
        }

    async def fetch_bar(self, metric_id: str) -> dict | None:
        """
        柱状图：即时查询按 split_by 标签分组，返回 bars=[{name, value, color, label}]
        用于空调状态(bar_horizontal)和电池电量(bar_vertical)
        """
        cfg = self.metrics.get(metric_id)
        if not cfg:
            return None

        now = datetime.now(timezone.utc).timestamp()
        try:
            results = await self.vm.query(cfg["query"])
        except Exception:
            return None

        if not results:
            return None

        split_by = cfg.get("split_by", "name")
        chart_type = cfg.get("chart_type", "bar_vertical")

        AC_LABELS = {0: "关闭", 1: "制冷", 2: "制热", 3: "送风", 4: "除湿", 5: "自动"}
        bars = []
        for r in results:
            name = r["labels"].get(split_by, "?")
            v = float(r["value"]) if r["value"] not in (None, "") else 0

            if chart_type == "bar_horizontal":
                # 空调：用状态值决定颜色和标签
                label = r["labels"].get("mode", AC_LABELS.get(int(v), "?"))
                color = "#36a2eb" if v > 0 else "#3a3d50"
                bars.append({"name": name, "value": v, "color": color, "label": label})
            else:
                # 电池：低于20%变红
                color = "#ff4560" if v < 20 else ("#feb019" if v < 50 else "#00e396")
                bars.append({"name": name, "value": v, "color": color, "label": f"{int(v)}%"})

        bars.sort(key=lambda x: x["name"])

        return {
            "id": metric_id,
            "name": cfg.get("name", metric_id),
            "unit": cfg.get("unit", ""),
            "chart_type": chart_type,
            "color": cfg.get("color", "#36a2eb"),
            "value": None,
            "source": "vm",
            "timestamp": now,
            "labels": {},
            "history": None,
            "bars": bars,
        }

    async def fetch_gauge_extra(self, metric_id: str) -> dict | None:
        """
        扩展 gauge：支持 gauge_bool（在家/外出）和带状态标签的 gauge
        """
        cfg = self.metrics.get(metric_id)
        if not cfg:
            return None

        now = datetime.now(timezone.utc).timestamp()
        chart_type = cfg.get("chart_type", "gauge")

        try:
            results = await self.vm.query(cfg["query"])
        except Exception:
            return None

        if not results:
            return None

        v = float(results[0]["value"]) if results[0]["value"] not in (None, "") else 0

        extra_label = ""
        if chart_type == "gauge_bool":
            extra_label = cfg.get("gauge_true_label", "是") if v >= 1 else cfg.get("gauge_false_label", "否")
        elif cfg.get("gauge_states"):
            states = cfg["gauge_states"]
            extra_label = states.get(str(int(v)), "")

        # gauge_label_query 用于扫地机：取状态值的文字（数值型 state）
        if cfg.get("gauge_label_query"):
            try:
                lr = await self.vm.query(cfg["gauge_label_query"])
                if lr:
                    lv = float(lr[0]["value"]) if lr[0]["value"] not in (None, "") else 0
                    states = cfg.get("gauge_states", {})
                    extra_label = states.get(str(int(lv)), extra_label)
            except Exception:
                pass

        # gauge_state_query 用于扫地机：从标签读取状态文字
        if cfg.get("gauge_state_query"):
            try:
                sr = await self.vm.query(cfg["gauge_state_query"])
                if sr:
                    state_label_key = cfg.get("gauge_state_label", "state")
                    raw_state = sr[0]["labels"].get(state_label_key, "")
                    state_map = cfg.get("gauge_state_map", {})
                    extra_label = state_map.get(raw_state, raw_state) or extra_label
            except Exception:
                pass

        return {
            "id": metric_id,
            "name": cfg.get("name", metric_id),
            "unit": cfg.get("unit", ""),
            "chart_type": chart_type,
            "color": cfg.get("color", "#36a2eb"),
            "value": v,
            "extra_label": extra_label,
            "source": "vm",
            "timestamp": now,
            "labels": {},
            "history": [{"t": int(now * 1000), "v": v}],
        }
