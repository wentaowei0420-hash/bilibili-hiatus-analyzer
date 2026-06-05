from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .gui_data import (
    _export_db_path,
    _is_full_inventory_row,
    _is_truthy_text,
    _rating_db_paths,
    _safe_int,
    _table_exists,
)


def get_douyin_video_count_stats(min_video_count: int = 1000) -> dict[str, Any]:
    threshold = max(int(min_video_count or 0), 0)
    db_path = _export_db_path()
    result = {
        "db_path": str(db_path),
        "threshold": threshold,
        "total_followings": 0,
        "rows": [],
    }
    if not db_path.exists():
        return result

    rating_db_path, _source_db_path = _rating_db_paths()
    grade_map = _load_creator_grade_map(rating_db_path, db_path)

    with sqlite3.connect(str(db_path), timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, "cache_inventory_current"):
            return result
        inventory_rows = conn.execute('SELECT * FROM "cache_inventory_current"').fetchall()

    matched_rows = []
    total_followings = 0
    for raw_row in inventory_rows:
        row = dict(raw_row)
        uploader_id = str(row.get("UP主UID") or "").strip()
        if not uploader_id or not _is_truthy_text(row.get("有关注列表缓存")):
            continue
        total_followings += 1

        published_video_count = _safe_int(row.get("发布视频数量"))
        if published_video_count <= threshold:
            continue

        grade_info = grade_map.get(uploader_id, {})
        uploader_name = (
            str(row.get("UP主姓名") or "").strip()
            or str(grade_info.get("uploader_name") or "").strip()
            or uploader_id
        )
        homepage_url = (
            str(row.get("UP主主页链接") or "").strip()
            or str(grade_info.get("homepage_url") or "").strip()
        )
        final_grade = str(grade_info.get("final_grade") or "").strip() or "无"

        matched_rows.append(
            {
                "uploader_id": uploader_id,
                "uploader_name": uploader_name,
                "published_video_count": published_video_count,
                "has_full_fetch": bool(_is_full_inventory_row(row)),
                "final_grade": final_grade,
                "homepage_url": homepage_url,
            }
        )

    matched_rows.sort(key=lambda item: (-item["published_video_count"], item["uploader_name"]))
    result["total_followings"] = total_followings
    result["rows"] = matched_rows
    return result


def _load_creator_grade_map(rating_db_path: Path, export_db_path: Path) -> dict[str, dict[str, str]]:
    seen_paths: set[str] = set()
    for db_path in (rating_db_path, export_db_path):
        if not db_path.exists():
            continue
        db_key = str(db_path.resolve())
        if db_key in seen_paths:
            continue
        seen_paths.add(db_key)
        grade_map = _read_creator_grade_map(db_path)
        if grade_map:
            return grade_map
    return {}


def _read_creator_grade_map(db_path: Path) -> dict[str, dict[str, str]]:
    with sqlite3.connect(str(db_path), timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, "creator_score_current"):
            return {}
        columns = {row[1] for row in conn.execute('PRAGMA table_info("creator_score_current")')}
        required_columns = {"UP主UID", "UP主姓名", "UP最终等级"}
        if not required_columns.issubset(columns):
            return {}
        homepage_expr = '"UP主主页链接"' if "UP主主页链接" in columns else "''"
        rows = conn.execute(
            f"""
            SELECT "UP主UID" AS uploader_id,
                   "UP主姓名" AS uploader_name,
                   "UP最终等级" AS final_grade,
                   {homepage_expr} AS homepage_url
            FROM creator_score_current
            """
        ).fetchall()
    return {
        str(row["uploader_id"] or "").strip(): {
            "uploader_name": str(row["uploader_name"] or "").strip(),
            "final_grade": str(row["final_grade"] or "").strip(),
            "homepage_url": str(row["homepage_url"] or "").strip(),
        }
        for row in rows
        if str(row["uploader_id"] or "").strip()
    }
