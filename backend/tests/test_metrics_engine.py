"""
MetricsEngine 单元测试
"""

import pytest

from app.core.vm_client import VMClient
from app.core.metrics_engine import MetricsEngine


@pytest.mark.asyncio
async def test_fetch_returns_correct_structure(mock_vm, sample_metrics_config):
    """fetch() 返回正确结构"""
    client = VMClient(base_url="http://localhost:8428")
    engine = MetricsEngine(client, sample_metrics_config)

    result = await engine.fetch("rpi_cpu_temp")
    assert result is not None
    assert result["id"] == "rpi_cpu_temp"
    assert result["name"] == "CPU 温度"
    assert result["unit"] == "°C"
    assert result["chart_type"] == "line"
    assert result["color"] == "#ff6384"
    assert isinstance(result["value"], (int, float))
    assert result["source"] in ("vm", "mock")
    assert isinstance(result["timestamp"], float)
    assert isinstance(result["labels"], dict)
    assert isinstance(result["history"], list)


@pytest.mark.asyncio
async def test_fetch_nonexistent_metric(mock_vm, sample_metrics_config):
    """不存在的指标 → 返回 None"""
    client = VMClient(base_url="http://localhost:8428")
    engine = MetricsEngine(client, sample_metrics_config)
    result = await engine.fetch("nonexistent_metric")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_all_parallel(mock_vm, sample_metrics_config):
    """fetch_all() 并行查询所有指标"""
    client = VMClient(base_url="http://localhost:8428")
    engine = MetricsEngine(client, sample_metrics_config)
    results = await engine.fetch_all()
    assert len(results) == len(sample_metrics_config)


@pytest.mark.asyncio
async def test_source_vm_when_vm_has_data(mock_vm, sample_metrics_config):
    """VM 有数据 → source=vm"""
    client = VMClient(base_url="http://localhost:8428")
    engine = MetricsEngine(client, sample_metrics_config)
    result = await engine.fetch("rpi_cpu_temp")
    assert result is not None
    assert result["source"] == "vm"
    assert float(result["value"]) == 52.3


@pytest.mark.asyncio
async def test_mock_fallback_when_vm_empty(mock_vm_empty, sample_metrics_config):
    """VM 无数据 → mock 回退"""
    client = VMClient(base_url="http://localhost:8428")
    engine = MetricsEngine(client, sample_metrics_config)
    result = await engine.fetch("rpi_cpu_temp")
    assert result is not None
    assert result["source"] == "mock"
    # mock 值应该在范围内
    assert 40 <= float(result["value"]) <= 75


@pytest.mark.asyncio
async def test_history_buffer_max(mock_vm, sample_metrics_config):
    """历史缓冲上限 300 — 通过 fetch 多次触发自动截断"""
    client = VMClient(base_url="http://localhost:8428")
    engine = MetricsEngine(client, sample_metrics_config)
    assert engine.MAX_HISTORY == 300

    # 通过 fetch 插入超过 300 个点的历史数据
    # 先手动填满 350 个点，然后调用 fetch 触发截断
    engine._history["rpi_cpu_temp"] = [(i * 10.0, float(i % 100)) for i in range(350)]
    # fetch 一次触发截断
    await engine.fetch("rpi_cpu_temp")
    assert len(engine._history["rpi_cpu_temp"]) == 300


@pytest.mark.asyncio
async def test_fetch_all_returns_all_metrics(mock_vm, sample_metrics_config):
    """fetch_all 返回所有配置的指标"""
    client = VMClient(base_url="http://localhost:8428")
    engine = MetricsEngine(client, sample_metrics_config)
    results = await engine.fetch_all()
    result_ids = {r["id"] for r in results}
    expected_ids = {m["id"] for m in sample_metrics_config}
    assert result_ids == expected_ids
