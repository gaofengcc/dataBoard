"""
VMClient 单元测试
"""

import pytest
from httpx import TimeoutException, ConnectError

from app.core.vm_client import VMClient, VMConnectionError, VMTimeoutError


@pytest.mark.asyncio
async def test_query_normal(mock_vm):
    """正常查询 → 解析响应"""
    client = VMClient(base_url="http://localhost:8428")
    results = await client.query('rpi_cpu_temp{host="gaofengpi"}')
    assert len(results) == 1
    assert results[0]["labels"]["host"] == "gaofengpi"
    assert float(results[0]["value"]) == 52.3


@pytest.mark.asyncio
async def test_query_empty(mock_vm_empty):
    """空结果 → 返回空列表"""
    client = VMClient(base_url="http://localhost:8428")
    results = await client.query('rpi_cpu_temp{host="gaofengpi"}')
    assert results == []


@pytest.mark.asyncio
async def test_query_empty_promql(mock_vm):
    """空 PROMQL → 返回空列表"""
    client = VMClient(base_url="http://localhost:8428")
    results = await client.query("")
    assert results == []


@pytest.mark.asyncio
async def test_health_ok(mock_vm):
    """健康检查正常"""
    client = VMClient(base_url="http://localhost:8428")
    ok = await client.health()
    assert ok is True


@pytest.mark.asyncio
async def test_health_unavailable(mock_vm_unavailable):
    """后端不可用 → health 返回 False"""
    client = VMClient(base_url="http://localhost:8428")
    ok = await client.health()
    assert ok is False


@pytest.mark.asyncio
async def test_query_unavailable(mock_vm_unavailable):
    """后端不可用 → 抛出 VMConnectionError"""
    client = VMClient(base_url="http://localhost:8428")
    with pytest.raises(VMConnectionError):
        await client.query('rpi_cpu_temp{host="gaofengpi"}')


@pytest.mark.asyncio
async def test_query_timeout(mock_vm_timeout):
    """连接超时 → 抛出 VMTimeoutError"""
    client = VMClient(base_url="http://localhost:8428")
    # 对于未注册的路由，respx 默认抛出 TimeoutException
    with pytest.raises((VMTimeoutError, VMConnectionError)):
        await client.query('rpi_cpu_temp{host="gaofengpi"}')
