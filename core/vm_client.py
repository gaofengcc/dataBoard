"""
VictoriaMetrics HTTP API 客户端
封装查询、探活、数据格式转换
"""

import logging

import httpx

logger = logging.getLogger(__name__)


class VMClient:
    """VictoriaMetrics 查询客户端"""

    def __init__(self, base_url: str = "http://localhost:8428"):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=10)

    async def health(self) -> bool:
        """探活"""
        try:
            r = await self._client.get(f"{self.base_url}/health")
            return r.status_code == 200
        except Exception:
            return False

    async def query(self, promql: str) -> list[dict]:
        """执行即时查询，返回 time series 列表"""
        if not promql:
            return []
        try:
            r = await self._client.get(
                f"{self.base_url}/api/v1/query",
                params={"query": promql},
            )
            r.raise_for_status()
            data = r.json()
            return data.get("data", {}).get("result", [])
        except Exception as e:
            logger.warning("VM query failed: %s", e)
            return []

    async def query_range(
        self, promql: str, start: int, end: int, step: int = 60
    ) -> list[dict]:
        """范围查询，取历史数据"""
        if not promql:
            return []
        try:
            r = await self._client.get(
                f"{self.base_url}/api/v1/query_range",
                params={"query": promql, "start": start, "end": end, "step": step},
            )
            r.raise_for_status()
            data = r.json()
            return data.get("data", {}).get("result", [])
        except Exception as e:
            logger.warning("VM query_range failed: %s", e)
            return []

    async def close(self):
        await self._client.aclose()
