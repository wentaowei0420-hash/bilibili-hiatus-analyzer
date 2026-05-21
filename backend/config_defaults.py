from __future__ import annotations

from typing import Any

from .task_runner import BILIBILI_RUNTIME_FIELDS, DOUYIN_RUNTIME_FIELDS


def _settings_to_dict(config, fields: dict[str, tuple[str, str]]) -> dict[str, Any]:
    values = {}
    for name in fields:
        values[name] = getattr(config, name)
    return values


def get_config_defaults() -> dict[str, object]:
    from bilibili_analyzer.config import load_analyzer_config as load_bilibili_config
    from douyin_analyzer.config import load_analyzer_config as load_douyin_config

    douyin_config = load_douyin_config()
    return {
        "bilibili_runtime_settings": _settings_to_dict(
            load_bilibili_config(),
            BILIBILI_RUNTIME_FIELDS,
        ),
        "douyin_runtime_settings": _settings_to_dict(
            douyin_config,
            DOUYIN_RUNTIME_FIELDS,
        ),
        "douyin_full_fetch_retry_on_mismatch": bool(
            getattr(douyin_config, "full_fetch_retry_on_mismatch", True)
        ),
        "fetch_order_settings": {
            "bilibili": {"field": "follower_count", "direction": "desc"},
            "douyin": {"field": "follower_count", "direction": "desc"},
        },
    }
