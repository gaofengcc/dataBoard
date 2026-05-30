"""
VictoriaMetrics HTTP API 客户端
封装查询、探活、数据格式转换
超时 5 秒，连接错误异常处理
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)


class VMClientError(Exception):
    """VM 客户端异常基类"""


class VMConnectionError(VMClientError):
    """连接 VM 失败"""


class VMTimeoutError(VMClientError):
    """查询 VM 超时"""


class VMClient:
    """VictoriaMetrics 查询客户端"""

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or os.environ.get("VM_URL", "http://localhost:8428")).rstrip("/")
        # 清除 Docker 注入的代理变量，httpx 会受其影响
        for k in ['http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','all_proxy','ALL_PROXY']:
            os.environ.pop(k, None)
        self._client = httpx.AsyncClient(timeout=5)

    async def health(self) -> bool:
        """探活"""
        try:
            r = await self._client.get(f"{self.base_url}/health", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    async def query(self, promql: str) -> list[dict]:
        """
        执行即时查询，返回 time series 列表（含 labels）
        返回格式: [{"labels": {...}, "value": "123.4"}, ...]
        异常时抛出 VMConnectionError 或 VMTimeoutError
        """
        if not promql:
            return []
        try:
            r = await self._client.get(
                f"{self.base_url}/api/v1/query",
                params={"query": promql},
                timeout=5,
            )
            r.raise_for_status()
            data = r.json()
            results = data.get("data", {}).get("result", [])
            # 标准化：每个 result 包含 metric+labels 和 value
            return [
                {
                    "labels": res.get("metric", {}),
                    "value": res.get("value", [None, None])[1],
                }
                for res in results
            ]
        except httpx.TimeoutException as e:
            raise VMTimeoutError(f"VM query timeout: {e}") from e
        except httpx.ConnectError as e:
            raise VMConnectionError(f"VM connection failed: {e}") from e
        except httpx.HTTPStatusError as e:
            raise VMConnectionError(f"VM HTTP error: {e}") from e
        except Exception as e:
            raise VMClientError(f"VM query failed: {e}") from e

    async def close(self):
        await self._client.aclose()

    async def query_range(self, promql: str, start: float, end: float, step: int = 10) -> list[dict]:
        """
        执行范围查询，返回每个 series 的完整时间序列
        返回格式: [{"labels": {...}, "values": [[ts, val], ...]}, ...]
        """
        if not promql:
            return []
        try:
            r = await self._client.get(
                f"{self.base_url}/api/v1/query_range",
                params={"query": promql, "start": int(start), "end": int(end), "step": step},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            results = data.get("data", {}).get("result", [])
            return [
                {
                    "labels": res.get("metric", {}),
                    "values": res.get("values", []),
                }
                for res in results
            ]
        except httpx.TimeoutException as e:
            raise VMTimeoutError(f"VM range query timeout: {e}") from e
        except httpx.ConnectError as e:
            raise VMConnectionError(f"VM range query failed: {e}") from e
        except httpx.HTTPStatusError as e:
            raise VMConnectionError(f"VM HTTP error: {e}") from e
        except Exception as e:
            raise VMClientError(f"VM range query failed: {e}") from e
