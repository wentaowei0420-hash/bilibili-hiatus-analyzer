from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from config import ConfigLoader


def resolve_database_path(config: ConfigLoader, *, project_root: Path) -> Path:
    db_path = config.get("database_path", "dy_downloader.db") or "dy_downloader.db"
    path = Path(str(db_path)).expanduser()
    return path if path.is_absolute() else project_root / path


def resolve_rating_database_path(config: ConfigLoader, *, project_root: Path) -> Path:
    raw_config = config.config if isinstance(config.config, dict) else {}
    for key in ("rating_database_path", "rating_store_db", "rating_db_path"):
        value = raw_config.get(key)
        if value:
            path = Path(str(value)).expanduser()
            return path if path.is_absolute() else project_root / path

    export_db = resolve_database_path(config, project_root=project_root)
    candidates = []
    if export_db.name == "douyin_export_store.db":
        candidates.append(export_db.with_name("douyin_rating_store.db"))
    candidates.append(export_db.parent / "douyin_rating_store.db")
    candidates.append(
        project_root.parent / "data" / "douyin" / "state" / "douyin_rating_store.db"
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def load_sql_video_rows(config: ConfigLoader, *, project_root: Path) -> list[dict[str, Any]]:
    db_path = resolve_rating_database_path(config, project_root=project_root)
    if not db_path.exists():
        raise FileNotFoundError(f"未找到评分数据库：{db_path}")

    with sqlite3.connect(str(db_path), timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='video_score_current'"
        ).fetchone()
        if not exists:
            raise RuntimeError(f"评分数据库缺少 video_score_current 表：{db_path}")

        columns = [
            row[1]
            for row in conn.execute('PRAGMA table_info("video_score_current")').fetchall()
        ]
        id_column = _pick_video_id_column(columns)
        if not id_column:
            raise RuntimeError("video_score_current 缺少视频ID列")

        selected = _selected_columns(columns, id_column)
        order_sql = _order_sql(columns)
        quoted = ", ".join(f'"{name}"' for name in selected)
        raw_rows = conn.execute(
            f'SELECT {quoted} FROM "video_score_current" {order_sql}'
        ).fetchall()

    return [_normalize_row(dict(row), id_column) for row in raw_rows if row[id_column]]


def delete_sql_video_records(
    config: ConfigLoader,
    *,
    project_root: Path,
    aweme_id: str,
    video_url: str = "",
) -> int:
    aweme_id = str(aweme_id or "").strip()
    video_url = str(video_url or "").strip()
    if not aweme_id and not video_url:
        return 0

    db_path = resolve_rating_database_path(config, project_root=project_root)
    if not db_path.exists():
        return 0

    deleted = 0
    with sqlite3.connect(str(db_path), timeout=30) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "video_score_current" in tables:
            columns = [
                row[1]
                for row in conn.execute(
                    'PRAGMA table_info("video_score_current")'
                ).fetchall()
            ]
            id_column = _pick_video_id_column(columns)
            url_column = _pick_column(columns, "video_url", "url", "链接")
            conditions = []
            params: list[str] = []
            if id_column and aweme_id:
                conditions.append(f'"{id_column}" = ?')
                params.append(aweme_id)
            if url_column and aweme_id:
                conditions.append(f'"{url_column}" LIKE ?')
                params.append(f"%{aweme_id}%")
            if url_column and video_url:
                conditions.append(f'"{url_column}" = ?')
                params.append(video_url)
            if conditions:
                cursor = conn.execute(
                    f'DELETE FROM "video_score_current" WHERE {" OR ".join(conditions)}',
                    params,
                )
                deleted += max(cursor.rowcount or 0, 0)

        if aweme_id and "douyin_video_manual_rating" in tables:
            cursor = conn.execute(
                'DELETE FROM "douyin_video_manual_rating" WHERE "video_id" = ?',
                (aweme_id,),
            )
            deleted += max(cursor.rowcount or 0, 0)
        conn.commit()
    return deleted


def _selected_columns(columns: list[str], id_column: str) -> list[str]:
    preferred = [
        _pick_column(columns, "uploader_name", "author", "UP主", "作者"),
        _pick_column(columns, "uploader_id", "uid", "UP主UID"),
        _pick_column(columns, "video_title", "title", "标题"),
        id_column,
        _pick_column(columns, "video_url", "url", "链接"),
        _pick_column(columns, "date", "publish", "发布"),
        _pick_column(columns, "create_time"),
        _pick_column(columns, "duration_seconds", "duration", "时长"),
        _pick_column(columns, "duration_category", "时长分类"),
        _pick_column(columns, "like_count", "digg_count", "点赞"),
        _pick_grade_column(columns),
        _pick_score_column(columns),
        _pick_column(columns, "download_status"),
        _pick_column(columns, "download_time"),
        _pick_column(columns, "download_path"),
    ]
    return list(dict.fromkeys([name for name in preferred if name]))


def _order_sql(columns: list[str]) -> str:
    order_parts = []
    score_column = _pick_score_column(columns)
    like_column = _pick_column(columns, "like_count", "digg_count", "点赞")
    if score_column:
        order_parts.append(f'CAST(COALESCE("{score_column}", 0) AS REAL) DESC')
    if like_column and like_column != score_column:
        order_parts.append(f'CAST(COALESCE("{like_column}", 0) AS REAL) DESC')
    return "ORDER BY " + ", ".join(order_parts) if order_parts else ""


def _normalize_row(row: dict[str, Any], id_column: str) -> dict[str, Any]:
    aweme_id = str(row.get(id_column) or "").strip()
    video_url = str(_first_value(row, "video_url", "url", "链接") or "").strip()
    if not video_url:
        video_url = f"https://www.douyin.com/video/{aweme_id}"

    grade = _first_grade(row)
    like_count = _first_value(row, "like_count", "digg_count", "点赞")
    duration = _first_value(row, "duration_seconds", "duration", "时长")
    uploader_name = _first_value(row, "uploader_name", "author", "UP主", "作者")
    video_title = _first_value(row, "video_title", "title", "标题")
    publish_time = _first_value(row, "date", "publish", "发布")

    return {
        **row,
        "aweme_id": aweme_id,
        "video_id": aweme_id,
        "视频ID": aweme_id,
        "video_url": video_url,
        "视频链接": video_url,
        "video_grade": grade,
        "final_grade": grade,
        "等级": grade,
        "like_count": like_count,
        "digg_count": like_count,
        "点赞数": like_count,
        "duration_seconds": duration,
        "视频时长(秒)": duration,
        "uploader_name": uploader_name,
        "UP主": uploader_name,
        "author": uploader_name,
        "video_title": video_title,
        "视频标题": video_title,
        "发布时间": publish_time,
        "发布日期": publish_time,
        "date": str(publish_time or "").split(" ", 1)[0],
        "create_time": _first_value(row, "create_time") or "",
    }


def _pick_column(columns: list[str], *needles: str) -> str:
    lowered_columns = {column.lower(): column for column in columns}
    for needle in needles:
        if not needle:
            continue
        exact = lowered_columns.get(needle.lower())
        if exact:
            return exact

    lowered_needles = [needle.lower() for needle in needles if needle]
    for column in columns:
        lower = column.lower()
        if any(needle in lower for needle in lowered_needles):
            return column
    return ""


def _pick_grade_column(columns: list[str]) -> str:
    for exact in ("视频最终等级", "视频等级", "final_grade", "video_grade"):
        column = _pick_column(columns, exact)
        if column:
            return column
    return _pick_column(columns, "final_grade", "video_grade")


def _pick_score_column(columns: list[str]) -> str:
    lowered_columns = {column.lower(): column for column in columns}
    for exact in ("final_score", "video_final_score", "视频最终分", "最终分"):
        column = lowered_columns.get(exact.lower())
        if column:
            return column

    for column in columns:
        lower = column.lower()
        if "score" in lower and "status" not in lower:
            return column
        if "最终" in column and "分" in column and "分类" not in column:
            return column
    return ""


def _pick_video_id_column(columns: list[str]) -> str:
    for needle in ("aweme_id", "video_id", "视频ID", "作品ID"):
        column = _pick_column(columns, needle)
        if column:
            return column
    for column in columns:
        lower = column.lower()
        if "id" in lower and "uid" not in lower and "uploader" not in lower:
            return column
    return ""


def _first_value(row: dict[str, Any], *needles: str) -> Any:
    for key, value in row.items():
        lower = str(key).lower()
        if any(needle.lower() in lower for needle in needles):
            if str(value or "").strip():
                return value
    return ""


def _first_grade(row: dict[str, Any]) -> str:
    explicit = _first_value(
        row,
        "视频最终等级",
        "视频等级",
        "final_grade",
        "video_grade",
    )
    grade = _normalize_grade(explicit)
    if grade:
        return grade
    for value in row.values():
        grade = _normalize_grade(value)
        if grade and str(value).strip().upper() in {"S", "A", "B", "C", "D"}:
            return grade
    return ""


def _normalize_grade(value: Any) -> str:
    text = str(value or "").strip().upper().replace("级", "").replace("绾?", "")
    return text[:1] if text[:1] in {"S", "A", "B", "C", "D"} else ""
