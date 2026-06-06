from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class PlatformQuickSettings:
    uid_limit_enabled: bool = False
    uid_limit: int = 100
    auto_mode_enabled: bool = False
    auto_interval_minutes: int = 100


def _safe_int(value: Any, default: int) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _first_present(data: Mapping[str, Any], keys: Iterable[str], default: Any) -> Any:
    for key in keys:
        if key in data:
            return data.get(key)
    return default


def load_platform_quick_settings(
    data: Mapping[str, Any] | None,
    prefix: str,
    *,
    default_uid_limit: int,
    default_interval_minutes: int,
    legacy_uid_enabled_keys: Iterable[str] = (),
    legacy_uid_value_keys: Iterable[str] = (),
    legacy_auto_enabled_keys: Iterable[str] = (),
    legacy_auto_interval_keys: Iterable[str] = (),
) -> PlatformQuickSettings:
    payload = data or {}
    uid_enabled = bool(
        _first_present(
            payload,
            [f"{prefix}_uid_limit_enabled", *legacy_uid_enabled_keys],
            False,
        )
    )
    uid_limit = max(
        1,
        _safe_int(
            _first_present(
                payload,
                [f"{prefix}_uid_limit", *legacy_uid_value_keys],
                default_uid_limit,
            ),
            default_uid_limit,
        ),
    )
    auto_enabled = bool(
        _first_present(
            payload,
            [f"{prefix}_auto_full_enabled", *legacy_auto_enabled_keys],
            False,
        )
    )
    auto_interval = max(
        1,
        _safe_int(
            _first_present(
                payload,
                [f"{prefix}_auto_full_interval_minutes", *legacy_auto_interval_keys],
                default_interval_minutes,
            ),
            default_interval_minutes,
        ),
    )
    return PlatformQuickSettings(
        uid_limit_enabled=uid_enabled,
        uid_limit=uid_limit,
        auto_mode_enabled=auto_enabled,
        auto_interval_minutes=auto_interval,
    )


def snapshot_platform_quick_settings(
    prefix: str,
    *,
    uid_limit_enabled: bool,
    uid_limit: int,
    auto_mode_enabled: bool,
    auto_interval_minutes: int,
) -> dict[str, Any]:
    return {
        f"{prefix}_uid_limit_enabled": bool(uid_limit_enabled),
        f"{prefix}_uid_limit": max(1, _safe_int(uid_limit, 100)),
        f"{prefix}_auto_full_enabled": bool(auto_mode_enabled),
        f"{prefix}_auto_full_interval_minutes": max(1, _safe_int(auto_interval_minutes, 100)),
    }


def format_interval_text(minutes: Any, default_minutes: int = 100) -> str:
    normalized = max(1, _safe_int(minutes, default_minutes))
    if normalized % 60 == 0:
        return f"{normalized // 60} 小时"
    if normalized > 60:
        hours = normalized // 60
        rest = normalized % 60
        return f"{hours} 小时 {rest} 分钟"
    return f"{normalized} 分钟"
