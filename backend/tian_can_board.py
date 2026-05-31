from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any


TIAN_CAN_HIDDEN_TABLE = "douyin_tian_can_hidden"
TIAN_CAN_LIMIT = 33
UNFOLLOW_PAYLOAD_SEPARATOR = "||"


def _shared():
    from . import gui_data as shared

    return shared


def _load_douyin_config():
    from douyin_analyzer.config import load_analyzer_config

    return load_analyzer_config(fetch_mode_override="counts")


def _ensure_tian_can_hidden_table(conn) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{TIAN_CAN_HIDDEN_TABLE}" (
            uploader_id TEXT PRIMARY KEY,
            uploader_name TEXT,
            homepage_url TEXT,
            hide_status TEXT NOT NULL DEFAULT 'active',
            hidden_at TEXT NOT NULL,
            hide_reason TEXT,
            action_type TEXT
        )
        """
    )


def active_tian_can_hidden_count(conn) -> int:
    _ensure_tian_can_hidden_table(conn)
    row = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM "{TIAN_CAN_HIDDEN_TABLE}"
        WHERE hide_status = 'active'
        """
    ).fetchone()
    return int(row[0] or 0) if row else 0


def build_unfollow_payload(uploader_id: str, homepage_url: str) -> str:
    return f"{str(uploader_id or '').strip()}{UNFOLLOW_PAYLOAD_SEPARATOR}{str(homepage_url or '').strip()}"


def get_tian_can_rows(conn, *, search_uid: str = ""):
    shared = _shared()
    _ensure_tian_can_hidden_table(conn)
    if not shared._table_exists(conn, shared.CREATOR_TABLE):
        return []
    creator_columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{shared.CREATOR_TABLE}")')}
    homepage_expr = 'c."UP主主页链接"' if "UP主主页链接" in creator_columns else "''"
    low_ratio_expr = 'c."低等级视频比例"' if "低等级视频比例" in creator_columns else "0"
    inactive_days_expr = 'c."未更新天数"' if "未更新天数" in creator_columns else "0"

    creator_join = (
        f'JOIN "{shared.ELIGIBLE_UID_TABLE}" AS e ON c."UP主UID" = e.uid'
        if shared._table_exists(conn, shared.ELIGIBLE_UID_TABLE) and not search_uid
        else ""
    )
    conditions = ['tx.uploader_id IS NULL']
    params: list[Any] = []
    if search_uid:
        conditions.insert(0, 'c."UP主UID" = ?')
        params.append(str(search_uid or "").strip())

    return shared._query_rows(
        conn,
        f"""
        SELECT
            c."UP主姓名",
            'F' AS display_grade,
            c."UP最终分",
            c."评级置信度",
            {inactive_days_expr},
            {low_ratio_expr},
            {homepage_expr},
            c."UP主UID",
            c."UP主UID" || '{UNFOLLOW_PAYLOAD_SEPARATOR}' || COALESCE({homepage_expr}, '')
        FROM "{shared.CREATOR_TABLE}" AS c
        {creator_join}
        LEFT JOIN "{TIAN_CAN_HIDDEN_TABLE}" AS tx
          ON c."UP主UID" = tx.uploader_id AND tx.hide_status = 'active'
        WHERE {' AND '.join(conditions)}
        ORDER BY CAST(COALESCE(c."UP最终分", 0) AS REAL) ASC,
                 c."UP主UID" ASC
        LIMIT ?
        """,
        limit=TIAN_CAN_LIMIT,
        params=params,
    )


def dismiss_tian_can_creator(
    uploader_id: str,
    reason: str = "天参榜取消提示",
) -> dict[str, Any]:
    shared = _shared()
    uploader_id = str(uploader_id or "").strip()
    if not uploader_id:
        raise ValueError("Missing uploader_id")

    shared.save_creator_manual_grade(uploader_id, "C", reason)
    db_path, _source_db_path = shared._rating_db_paths()
    with sqlite3.connect(str(db_path)) as conn:
        _upsert_hidden_creator(
            conn,
            uploader_id,
            reason=reason,
            action_type="dismiss",
        )
        conn.commit()
    return {"ok": True, "uploader_id": uploader_id, "manual_grade": "C"}


def unfollow_tian_can_creator(
    uploader_id: str,
    homepage_url: str,
    reason: str = "天参榜取消关注",
) -> dict[str, Any]:
    shared = _shared()
    uploader_id = str(uploader_id or "").strip()
    homepage_url = str(homepage_url or "").strip()
    if not uploader_id:
        raise ValueError("Missing uploader_id")

    db_path, _source_db_path = shared._rating_db_paths()
    with sqlite3.connect(str(db_path)) as conn:
        _upsert_hidden_creator(
            conn,
            uploader_id,
            homepage_url=homepage_url,
            reason=reason,
            action_type="unfollow",
        )
        conn.commit()
    return {
        "ok": True,
        "uploader_id": uploader_id,
        "homepage_url": homepage_url,
        "status": "hidden",
    }


def _upsert_hidden_creator(
    conn,
    uploader_id: str,
    *,
    homepage_url: str = "",
    reason: str,
    action_type: str,
) -> None:
    shared = _shared()
    _ensure_tian_can_hidden_table(conn)
    creator = _load_creator_snapshot(conn, uploader_id)
    uploader_name = creator.get("uploader_name", "")
    resolved_homepage = homepage_url or creator.get("homepage_url", "")
    conn.execute(
        f"""
        INSERT INTO "{TIAN_CAN_HIDDEN_TABLE}" (
            uploader_id,
            uploader_name,
            homepage_url,
            hide_status,
            hidden_at,
            hide_reason,
            action_type
        )
        VALUES (?, ?, ?, 'active', ?, ?, ?)
        ON CONFLICT(uploader_id) DO UPDATE SET
            uploader_name=excluded.uploader_name,
            homepage_url=excluded.homepage_url,
            hide_status='active',
            hidden_at=excluded.hidden_at,
            hide_reason=excluded.hide_reason,
            action_type=excluded.action_type
        """,
        (
            uploader_id,
            uploader_name,
            resolved_homepage,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            str(reason or "").strip(),
            str(action_type or "").strip(),
        ),
    )


def _load_creator_snapshot(conn, uploader_id: str) -> dict[str, str]:
    shared = _shared()
    if not shared._table_exists(conn, shared.CREATOR_TABLE):
        return {"uploader_name": "", "homepage_url": ""}
    row = conn.execute(
        f"""
        SELECT "UP主姓名", "UP主主页链接"
        FROM "{shared.CREATOR_TABLE}"
        WHERE "UP主UID" = ?
        LIMIT 1
        """,
        (uploader_id,),
    ).fetchone()
    if not row:
        return {"uploader_name": "", "homepage_url": ""}
    return {
        "uploader_name": str(row[0] or "").strip(),
        "homepage_url": str(row[1] or "").strip(),
    }

