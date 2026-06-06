from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunConfig:
    platform: str
    action: str
    bilibili_mode: str
    douyin_fetch_mode: str
    douyin_backend: str
    douyin_browser_name: str
    douyin_full_fetch_retry_on_mismatch: bool
    uid_limit_enabled: bool
    uid_limit: int
    high_like_threshold: int
    unfollow_list_path: Path
    bilibili_runtime_settings: dict
    douyin_runtime_settings: dict
    fetch_order_settings: dict
