"""
pytest fixtures — mock VM 响应和配置加载
"""

import pytest
import respx
from httpx import Response, TimeoutException


@pytest.fixture
def mock_vm():
    """Mock VictoriaMetrics HTTP API 响应"""
    with respx.mock(base_url="http://localhost:8428", assert_all_called=False) as respx_mock:
        # 健康检查
        respx_mock.get("/health").respond(200, text="VictoriaMetrics")

        # 正常查询
        respx_mock.get("/api/v1/query").respond(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {
                            "metric": {"host": "gaofengpi"},
                            "value": [1712345678.123, "52.3"],
                        }
                    ],
                },
            },
        )

        yield respx_mock


@pytest.fixture
def mock_vm_empty():
    """Mock VM 返回空结果"""
    with respx.mock(base_url="http://localhost:8428", assert_all_called=False) as respx_mock:
        respx_mock.get("/health").respond(200, text="VictoriaMetrics")
        respx_mock.get("/api/v1/query").respond(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [],
                },
            },
        )
        yield respx_mock


@pytest.fixture
def mock_vm_unavailable():
    """Mock VM 后端不可用"""
    with respx.mock(base_url="http://localhost:8428", assert_all_called=False) as respx_mock:
        respx_mock.get("/health").respond(503)
        respx_mock.get("/api/v1/query").respond(503)
        yield respx_mock


@pytest.fixture
def mock_vm_timeout():
    """Mock VM 超时 — 模拟连接超时"""
    with respx.mock(base_url="http://localhost:8428", assert_all_called=False) as respx_mock:
        respx_mock.get("/health").respond(200, text="VictoriaMetrics")
        # query 路由模拟 TimeoutException
        respx_mock.get("/api/v1/query").mock(side_effect=TimeoutException("Connection timeout"))
        yield respx_mock


@pytest.fixture
def sample_metrics_config():
    """示例指标配置"""
    return [
        {
            "id": "rpi_cpu_temp",
            "name": "CPU 温度",
            "unit": "°C",
            "chart_type": "line",
            "color": "#ff6384",
            "refresh_interval": 10,
            "query": 'rpi_cpu_temp{host="gaofengpi"}',
            "mock": {"min": 40, "max": 75, "drift": 0.3},
        },
        {
            "id": "rpi_mem_usage",
            "name": "内存使用",
            "unit": "%",
            "chart_type": "area",
            "color": "#fdcb6e",
            "refresh_interval": 10,
            "query": 'rpi_mem_usage_pct{host="gaofengpi"}',
            "mock": {"min": 30, "max": 85, "drift": 0.5},
        },
        {
            "id": "rpi_ip_info",
            "name": "IP 地址",
            "unit": "",
            "chart_type": "stat",
            "color": "#36a2eb",
            "refresh_interval": 10,
            "query": 'rpi_ip_info{host="gaofengpi"}',
            "label_key": "ip",
        },
    ]
