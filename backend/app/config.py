"""
DataBoard — YAML 配置加载
从 config/ 目录读取 layout.yaml 和 metrics.yaml
路径通过环境变量 CONFIG_DIR 可配置（默认 /app/config）
"""

import os
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


def _get_config_dir() -> str:
    """获取配置目录路径"""
    return os.environ.get("CONFIG_DIR", "/app/config")


def load_layout() -> dict:
    """加载 layout.yaml"""
    config_dir = _get_config_dir()
    path = Path(config_dir) / "layout.yaml"
    try:
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
            if not isinstance(cfg, dict):
                raise ValueError("layout.yaml 格式错误：期待一个字典")
            return cfg
    except FileNotFoundError:
        logger.warning("layout.yaml not found at %s, using fallback", path)
        return {"title": "系统监控看板", "refresh_interval": 10, "layout": []}
    except Exception as e:
        logger.error("Failed to load layout.yaml: %s", e)
        return {"title": "系统监控看板", "refresh_interval": 10, "layout": []}


def load_metrics() -> dict:
    """加载 metrics.yaml"""
    config_dir = _get_config_dir()
    path = Path(config_dir) / "metrics.yaml"
    try:
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
            if not isinstance(cfg, dict):
                raise ValueError("metrics.yaml 格式错误：期待一个字典")
            return cfg
    except FileNotFoundError:
        logger.warning("metrics.yaml not found at %s, using fallback", path)
        return {"metrics": []}
    except Exception as e:
        logger.error("Failed to load metrics.yaml: %s", e)
        return {"metrics": []}


def clean_metric_for_frontend(metric: dict) -> dict:
    """
    清洗指标定义：移除后端专用字段，只返回前端需要的信息
    """
    allowed = {"id", "name", "unit", "chart_type", "color", "refresh_interval",
               "stat_format", "label_key"}
    return {k: v for k, v in metric.items() if k in allowed}
