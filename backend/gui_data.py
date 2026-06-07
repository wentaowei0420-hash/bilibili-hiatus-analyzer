from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from . import tian_can_board


CREATOR_TABLE = "creator_score_current"
VIDEO_TABLE = "video_score_current"
ELIGIBLE_UID_TABLE = "_rating_eligible_uids"
LADDER_EXCLUSION_TABLE = "douyin_creator_ladder_exclusion"
LADDER_LIMIT = 66
GRADE_ORDER = ("S", "A", "B", "C", "D")


def _load_douyin_config():
    from douyin_analyzer.config import load_analyzer_config

    return load_analyzer_config()


def _load_bilibili_config():
    from bilibili_analyzer.config import load_analyzer_config

    return load_analyzer_config()


def _rating_db_paths() -> tuple[Path, Path]:
    from douyin_analyzer.rating.store import rating_store_db_path, source_store_db_path

    config = _load_douyin_config()
    return rating_store_db_path(config), source_store_db_path(config)


def _export_db_path() -> Path:
    config = _load_douyin_config()
    return Path(config.export_store_db)


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name=?
        UNION ALL
        SELECT 1 FROM sqlite_temp_master WHERE type IN ('table', 'view') AND name=?
        LIMIT 1
        """,
        (table_name, table_name),
    ).fetchone()
    return row is not None


def _attach_source_views(conn, source_db_path: Path, db_path: Path) -> None:
    if not source_db_path.exists() or source_db_path == db_path:
        return
    conn.execute("ATTACH DATABASE ? AS source_store", (str(source_db_path),))
    for table_name in (
        "cache_inventory_current",
        "douyin_archived_creators",
        "douyin_video_state",
        "aweme",
    ):
        if _table_exists(conn, table_name):
            continue
        source_row = conn.execute(
            "SELECT 1 FROM source_store.sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if source_row:
            conn.execute(
                f'CREATE TEMP VIEW IF NOT EXISTS "{table_name}" AS '
                f'SELECT * FROM source_store."{table_name}"'
            )


def _safe_int(value, default=0) -> int:
    try:
        text = str(value or "").replace(",", "").strip()
        return int(float(text)) if text else default
    except (TypeError, ValueError):
        return default


def _safe_number(value) -> int:
    return _safe_int(value, 0)


def _is_truthy_text(value) -> bool:
    text = str(value or "").strip().lower()
    return text in {"是", "yes", "true", "1", "y"}


def _extract_year(publish_ts, publish_date) -> str:
    timestamp = _safe_number(publish_ts)
    if timestamp > 0:
        try:
            return datetime.fromtimestamp(timestamp).strftime("%Y")
        except (OSError, OverflowError, ValueError):
            pass
    text = str(publish_date or "").strip()
    match = re.search(r"(19|20)\d{2}", text)
    return match.group(0) if match else "未知年份"


def _rows_to_lists(rows) -> list[list[Any]]:
    return [[_json_safe(value) for value in row] for row in rows]


def _json_safe(value):
    if isinstance(value, sqlite3.Row):
        return dict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _build_douyin_mode_stats(active_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    total_followings = len(active_rows)
    modes: dict[str, dict[str, Any]] = {}
    for mode in ("counts", "full"):
        column = f"has_{mode}_cache"
        count = sum(1 for row in active_rows if _is_truthy_text(row.get(column)))
        modes[mode] = {
            "count": count,
            "percent": (count / total_followings * 100) if total_followings else 0,
        }

    full_count = int((modes.get("full") or {}).get("count") or 0)
    expired_count = sum(
        1
        for row in active_rows
        if _is_truthy_text(row.get("has_full_cache"))
        and _is_truthy_text(row.get("progress_cache_due"))
    )
    expired_count = min(expired_count, full_count)
    modes["full"].update(
        {
            "valid_count": max(full_count - expired_count, 0),
            "expired_count": expired_count,
            "unfetched_count": max(total_followings - full_count, 0),
        }
    )
    return modes


def _get_douyin_stats_from_store(config, high_like_threshold: int) -> dict[str, Any] | None:
    db_path = Path(config.export_store_db)
    if not db_path.exists():
        return None

    with sqlite3.connect(str(db_path), timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, "cache_inventory_current"):
            return None

        inventory_columns = {
            row[1] for row in conn.execute('PRAGMA table_info("cache_inventory_current")')
        }
        required_columns = {
            "UP主UID",
            "发布视频数量",
            "有关注列表缓存",
            "关注列表缓存时间",
            "有进度缓存",
            "有counts缓存",
            "有full缓存",
        }
        if not required_columns.issubset(inventory_columns):
            return None

        progress_due_column = "\u662f\u5426\u5df2\u5230\u671f"
        progress_due_expr = f'"{progress_due_column}"' if progress_due_column in inventory_columns else "''"
        rows = conn.execute(
            f"""
            SELECT
                "UP主UID" AS uploader_id,
                "发布视频数量" AS published_video_count,
                "有关注列表缓存" AS has_followings_cache,
                "关注列表缓存时间" AS followings_cached_at,
                "有进度缓存" AS has_progress_cache,
                "有counts缓存" AS has_counts_cache,
                "有full缓存" AS has_full_cache,
                {progress_due_expr} AS progress_cache_due
            FROM cache_inventory_current
            """
        ).fetchall()

        active_rows = [
            dict(row)
            for row in rows
            if _is_truthy_text(row["has_followings_cache"])
            and str(row["uploader_id"] or "").strip()
        ]
        total_followings = len(active_rows)
        modes = _build_douyin_mode_stats(active_rows)

        creator_buckets = [
            ("0~50", 0, 50),
            ("51~300", 51, 300),
            ("301~500", 301, 500),
            ("501~1000", 501, 1000),
            ("1001以上", 1001, None),
        ]
        creator_bucket_counts = {label: 0 for label, _, _ in creator_buckets}
        for row in active_rows:
            count = _safe_int(row.get("published_video_count"), 0)
            for label, lower, upper in creator_buckets:
                if count >= lower and (upper is None or count <= upper):
                    creator_bucket_counts[label] += 1
                    break

        duration_buckets = [("0~20s", 0, 20), ("21~60s", 21, 60), ("61s以上", 61, None)]
        duration_bucket_counts = {label: 0 for label, _, _ in duration_buckets}
        cached_video_count = 0
        high_like_video_count = 0

        if _table_exists(conn, "douyin_video_state"):
            video_columns = {
                row[1] for row in conn.execute('PRAGMA table_info("douyin_video_state")')
            }
            if {"uploader_id", "like_count", "duration_seconds"}.issubset(video_columns):
                conn.execute('DROP TABLE IF EXISTS "_stats_active_uids"')
                conn.execute('CREATE TEMP TABLE "_stats_active_uids" (uid TEXT PRIMARY KEY)')
                conn.executemany(
                    'INSERT OR IGNORE INTO "_stats_active_uids" (uid) VALUES (?)',
                    [(str(row["uploader_id"]).strip(),) for row in active_rows],
                )
                availability_filter = (
                    "AND COALESCE(v.is_available, 1) = 1"
                    if "is_available" in video_columns
                    else ""
                )
                row = conn.execute(
                    f"""
                    SELECT
                        COUNT(*) AS total_count,
                        SUM(CASE WHEN COALESCE(v.like_count, 0) > ? THEN 1 ELSE 0 END) AS high_like_count,
                        SUM(CASE WHEN COALESCE(v.duration_seconds, 0) BETWEEN 0 AND 20 THEN 1 ELSE 0 END) AS duration_0_20,
                        SUM(CASE WHEN COALESCE(v.duration_seconds, 0) BETWEEN 21 AND 60 THEN 1 ELSE 0 END) AS duration_21_60,
                        SUM(CASE WHEN COALESCE(v.duration_seconds, 0) >= 61 THEN 1 ELSE 0 END) AS duration_61_plus
                    FROM douyin_video_state AS v
                    JOIN "_stats_active_uids" AS a ON v.uploader_id = a.uid
                    WHERE 1=1 {availability_filter}
                    """,
                    (int(high_like_threshold or 10000),),
                ).fetchone()
                cached_video_count = int((row or {})["total_count"] or 0) if row else 0
                high_like_video_count = int((row or {})["high_like_count"] or 0) if row else 0
                if row:
                    duration_bucket_counts["0~20s"] = int(row["duration_0_20"] or 0)
                    duration_bucket_counts["21~60s"] = int(row["duration_21_60"] or 0)
                    duration_bucket_counts["61s以上"] = int(row["duration_61_plus"] or 0)

        followings_cached_at = next(
            (
                str(row.get("followings_cached_at") or "").strip()
                for row in active_rows
                if str(row.get("followings_cached_at") or "").strip()
            ),
            "",
        )
        return {
            "total_followings": total_followings,
            "followings_cached_at": followings_cached_at,
            "progress_count": sum(
                1 for row in active_rows if _is_truthy_text(row.get("has_progress_cache"))
            ),
            "modes": modes,
            "cached_video_count": cached_video_count,
            "high_like_video_count": high_like_video_count,
            "high_like_ratio": (
                high_like_video_count / cached_video_count * 100
                if cached_video_count
                else 0
            ),
            "creator_buckets": creator_bucket_counts,
            "duration_buckets": duration_bucket_counts,
        }


def _get_douyin_stats_from_cache(high_like_threshold: int = 10000) -> dict[str, Any]:
    from douyin_analyzer.analyzer import DouyinHiatusAnalyzer
    from douyin_analyzer.cache import CacheStore

    config = _load_douyin_config()
    cache_store = CacheStore(config)
    analyzer = DouyinHiatusAnalyzer(config, browser_client=None, cache_store=cache_store)
    followings_payload = cache_store.load_followings_cache_payload()
    progress = cache_store.load_progress()
    cache_rows = analyzer.build_cache_inventory_rows(followings_payload, progress)
    active_rows = [
        row for row in cache_rows
        if str((row or {}).get("has_followings_cache", "")).strip() == "是"
    ]
    total_followings = len(active_rows)
    followings_cached_at = analyzer._format_cached_at(
        (followings_payload or {}).get("cached_at")
        if isinstance(followings_payload, dict)
        else ""
    )
    modes = _build_douyin_mode_stats(active_rows)

    creator_buckets = [
        ("0~50", 0, 50),
        ("51~300", 51, 300),
        ("301~500", 301, 500),
        ("501~1000", 501, 1000),
        ("1001以上", 1001, None),
    ]
    creator_bucket_counts = {label: 0 for label, _, _ in creator_buckets}
    for row in active_rows:
        count = _safe_int((row or {}).get("published_video_count"), 0)
        for label, lower, upper in creator_buckets:
            if count >= lower and (upper is None or count <= upper):
                creator_bucket_counts[label] += 1
                break

    active_uids = {
        str((row or {}).get("uploader_id") or "").strip()
        for row in active_rows
        if str((row or {}).get("uploader_id") or "").strip()
    }
    duration_buckets = [("0~20s", 0, 20), ("21~60s", 21, 60), ("61s以上", 61, None)]
    duration_bucket_counts = {label: 0 for label, _, _ in duration_buckets}
    cached_video_count = 0
    high_like_video_count = 0
    seen_video_ids = set()
    for uid, entry in (progress or {}).items():
        if str(uid).strip() not in active_uids or not isinstance(entry, dict):
            continue
        for video in entry.get("videos", []) or []:
            if not isinstance(video, dict):
                continue
            video_id = str(video.get("aweme_id") or video.get("video_id") or "").strip()
            dedupe_key = video_id or f"{uid}:{len(seen_video_ids)}"
            if dedupe_key in seen_video_ids:
                continue
            seen_video_ids.add(dedupe_key)
            cached_video_count += 1
            if _safe_int(video.get("like_count"), 0) > int(high_like_threshold or 10000):
                high_like_video_count += 1
            duration = _safe_int(video.get("duration_seconds") or video.get("duration"), 0)
            if duration > 1000:
                duration = int(round(duration / 1000))
            for label, lower, upper in duration_buckets:
                if duration >= lower and (upper is None or duration <= upper):
                    duration_bucket_counts[label] += 1
                    break

    return {
        "total_followings": total_followings,
        "followings_cached_at": followings_cached_at,
        "progress_count": len(progress or {}),
        "modes": modes,
        "cached_video_count": cached_video_count,
        "high_like_video_count": high_like_video_count,
        "high_like_ratio": (
            high_like_video_count / cached_video_count * 100
            if cached_video_count
            else 0
        ),
        "creator_buckets": creator_bucket_counts,
        "duration_buckets": duration_bucket_counts,
    }


def get_douyin_stats(high_like_threshold: int = 10000) -> dict[str, Any]:
    config = _load_douyin_config()
    try:
        stats = _get_douyin_stats_from_store(config, high_like_threshold)
    except sqlite3.Error:
        stats = None
    if stats is not None:
        return stats
    return _get_douyin_stats_from_cache(high_like_threshold)


def _grade_counts(conn, table, column, eligible_only=False):
    counts = {grade: 0 for grade in GRADE_ORDER}
    total = 0
    source = f'"{table}" AS s'
    join = (
        f' JOIN "{ELIGIBLE_UID_TABLE}" AS e ON s."UP主UID" = e.uid'
        if eligible_only
        else ""
    )
    for grade, count in conn.execute(
        f'SELECT s."{column}", COUNT(*) FROM {source}{join} GROUP BY s."{column}"'
    ):
        grade_text = str(grade or "").strip().upper()
        if grade_text in counts:
            counts[grade_text] += int(count or 0)
        total += int(count or 0)
    return total, counts


def _confidence_count(conn, table, column, values, eligible_only=False):
    placeholders = ",".join("?" for _ in values)
    source = f'"{table}" AS s'
    join = (
        f' JOIN "{ELIGIBLE_UID_TABLE}" AS e ON s."UP主UID" = e.uid'
        if eligible_only
        else ""
    )
    row = conn.execute(
        f'SELECT COUNT(*) FROM {source}{join} WHERE s."{column}" IN ({placeholders})',
        tuple(values),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _prepare_eligible_uid_filter(conn):
    conn.execute(f'DROP TABLE IF EXISTS "{ELIGIBLE_UID_TABLE}"')
    conn.execute(f'CREATE TEMP TABLE "{ELIGIBLE_UID_TABLE}" (uid TEXT PRIMARY KEY)')
    if not _table_exists(conn, "cache_inventory_current"):
        return False, 0, 0
    columns = {row[1] for row in conn.execute('PRAGMA table_info("cache_inventory_current")')}
    if "UP主UID" not in columns:
        return False, 0, 0
    active_archived_uids = set()
    if _table_exists(conn, "douyin_archived_creators"):
        active_archived_uids = {
            str(row[0] or "").strip()
            for row in conn.execute(
                """
                SELECT uploader_id
                FROM douyin_archived_creators
                WHERE archive_status = 'active'
                """
            ).fetchall()
            if str(row[0] or "").strip()
        }
    rows = conn.execute(
        """
        SELECT "UP主UID"
        FROM cache_inventory_current
        WHERE TRIM(COALESCE("UP主UID", "")) != ""
          AND (
              LOWER(TRIM(COALESCE("有full缓存", ""))) IN ('是', 'yes', 'true', '1', 'y')
              OR LOWER(COALESCE("已缓存模式", "")) LIKE '%full%'
              OR LOWER(TRIM(COALESCE("最近抓取模式", ""))) = 'full'
              OR LOWER(TRIM(COALESCE("统计范围", ""))) = 'full'
          )
        """
    ).fetchall()
    uids = [
        (uid,)
        for uid in (str(row[0] or "").strip() for row in rows)
        if uid and uid not in active_archived_uids
    ]
    if uids:
        conn.executemany(
            f'INSERT OR IGNORE INTO "{ELIGIBLE_UID_TABLE}" (uid) VALUES (?)',
            uids,
        )
    return True, len(uids), len(active_archived_uids)


def _stale_uid_count(conn, table):
    if not _table_exists(conn, table):
        return 0
    columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    if "UP主UID" not in columns:
        return 0
    row = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM "{table}" AS s
        LEFT JOIN "{ELIGIBLE_UID_TABLE}" AS e ON s."UP主UID" = e.uid
        WHERE e.uid IS NULL
        """
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _missing_eligible_score_count(conn, table):
    if not _table_exists(conn, table):
        return 0
    columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
    if "UP主UID" not in columns:
        return 0
    row = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM "{ELIGIBLE_UID_TABLE}" AS e
        LEFT JOIN "{table}" AS s ON s."UP主UID" = e.uid
        WHERE s."UP主UID" IS NULL
        """
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _query_rows(conn, sql, limit=30, params=()):
    if limit is None:
        return conn.execute(sql, tuple(params)).fetchall()
    return conn.execute(sql, (*tuple(params), limit)).fetchall()


def _load_archived_creator_rows(conn, limit=500, uploader_id=""):
    if not _table_exists(conn, "douyin_archived_creators"):
        return []
    uid_filter = "AND uploader_id = ?" if uploader_id else ""
    params = (uploader_id, limit) if uploader_id else (limit,)
    return conn.execute(
        f"""
        SELECT uploader_name, final_grade, final_score, confidence,
               inactive_days, follower_count, published_video_count,
               archived_at, archive_reason, uploader_id
        FROM douyin_archived_creators
        WHERE archive_status = 'active'
          {uid_filter}
        ORDER BY CAST(COALESCE(inactive_days, 0) AS REAL) DESC,
                 CAST(COALESCE(final_score, 0) AS REAL) ASC
        LIMIT ?
        """,
        params,
    ).fetchall()


def _ensure_ladder_exclusion_table(conn) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{LADDER_EXCLUSION_TABLE}" (
            uploader_id TEXT PRIMARY KEY,
            uploader_name TEXT,
            exclude_status TEXT NOT NULL DEFAULT 'active',
            excluded_at TEXT NOT NULL,
            exclude_reason TEXT
        )
        """
    )


def _active_ladder_exclusion_count(conn) -> int:
    if not _table_exists(conn, LADDER_EXCLUSION_TABLE):
        return 0
    row = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM "{LADDER_EXCLUSION_TABLE}"
        WHERE exclude_status = 'active'
        """
    ).fetchone()
    return int(row[0] or 0) if row else 0


def get_rating_overview(search_uid: str = "") -> dict[str, Any]:
    db_path, source_db_path = _rating_db_paths()
    result = {
        "db_path": str(db_path),
        "exists": db_path.exists(),
        "summary": {},
        "tables": {
            "creator_top": [],
            "creator_ladder": [],
            "creator_low": [],
            "archived_creator": [],
        },
        "message": "",
        "warning_parts": [],
    }
    if not db_path.exists():
        result["message"] = f"未找到评分数据库：{db_path}"
        return result

    search_uid = str(search_uid or "").strip()
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        _attach_source_views(conn, source_db_path, db_path)
        has_creator = _table_exists(conn, CREATOR_TABLE)
        has_video = _table_exists(conn, VIDEO_TABLE)
        has_eligible_filter, eligible_count, archived_count = _prepare_eligible_uid_filter(conn)
        stale_creator_count = (
            _stale_uid_count(conn, CREATOR_TABLE)
            if has_eligible_filter and has_creator
            else 0
        )
        missing_creator_score_count = (
            _missing_eligible_score_count(conn, CREATOR_TABLE)
            if has_eligible_filter and has_creator
            else 0
        )
        if not has_creator and not has_video:
            result["message"] = "未找到评分表，请先运行抖音评分。"
            return result

        if has_creator:
            _ensure_ladder_exclusion_table(conn)
            creator_columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{CREATOR_TABLE}")')}
            creator_homepage_expr = 'c."UP主主页链接"' if "UP主主页链接" in creator_columns else "''"
            total, counts = _grade_counts(conn, CREATOR_TABLE, "UP最终等级", has_eligible_filter)
            low_confidence = _confidence_count(
                conn, CREATOR_TABLE, "评级置信度", ["低", "中"], has_eligible_filter
            )
            result["summary"]["creator"] = {
                "total": total,
                "counts": counts,
                "low_confidence": low_confidence,
            }
            creator_join = (
                f'JOIN "{ELIGIBLE_UID_TABLE}" AS e ON c."UP主UID" = e.uid'
                if has_eligible_filter and not search_uid
                else ""
            )
            creator_where = 'WHERE c."UP主UID" = ?' if search_uid else ""
            creator_params = (search_uid,) if search_uid else ()
            result["tables"]["creator_top"] = _rows_to_lists(
                _query_rows(
                    conn,
                    f"""
                    SELECT c."UP主姓名", c."UP最终等级", c."UP最终分", c."评级置信度",
                           c."粉丝数", c."视频数量", c."UP主UID"
                    FROM creator_score_current AS c
                    {creator_join}
                    {creator_where}
                    ORDER BY CAST(c."UP最终分" AS REAL) DESC
                    """,
                    limit=None,
                    params=creator_params,
                )
            )
            ladder_eligible_join = (
                f'JOIN "{ELIGIBLE_UID_TABLE}" AS e ON c."UP主UID" = e.uid'
                if has_eligible_filter and not search_uid
                else ""
            )
            ladder_manual_join = (
                'LEFT JOIN "douyin_creator_manual_rating" AS m ON c."UP主UID" = m.uploader_id'
                if _table_exists(conn, "douyin_creator_manual_rating")
                else ""
            )
            ladder_manual_condition = (
                "AND (m.uploader_id IS NULL OR UPPER(TRIM(COALESCE(m.manual_grade, ''))) != 'S')"
                if ladder_manual_join
                else ""
            )
            ladder_conditions = [
                "UPPER(TRIM(COALESCE(c.\"UP最终等级\", ''))) != 'S'",
                "lx.uploader_id IS NULL",
            ]
            ladder_params = []
            if search_uid:
                ladder_conditions.insert(0, 'c."UP主UID" = ?')
                ladder_params.append(search_uid)
            result["tables"]["creator_ladder"] = _rows_to_lists(
                _query_rows(
                    conn,
                    f"""
                    SELECT c."UP主姓名", c."UP最终等级", c."UP最终分", c."评级置信度",
                           c."粉丝数", c."视频数量", c."UP主UID", c."UP主UID", {creator_homepage_expr}
                    FROM creator_score_current AS c
                    {ladder_eligible_join}
                    {ladder_manual_join}
                    LEFT JOIN "{LADDER_EXCLUSION_TABLE}" AS lx
                      ON c."UP主UID" = lx.uploader_id AND lx.exclude_status = 'active'
                    WHERE {' AND '.join(ladder_conditions)}
                    {ladder_manual_condition}
                    ORDER BY CAST(c."UP最终分" AS REAL) DESC
                    LIMIT ?
                    """,
                    limit=LADDER_LIMIT,
                    params=ladder_params,
                )
            )
            result["tables"]["creator_low"] = _rows_to_lists(
                tian_can_board.get_tian_can_rows(
                    conn,
                    search_uid=search_uid,
                )
            )
        else:
            result["summary"]["creator"] = {"total": 0, "counts": {}, "low_confidence": 0}

        result["tables"]["archived_creator"] = _rows_to_lists(
            _load_archived_creator_rows(conn, uploader_id=search_uid)
        )

        if has_video:
            total, counts = _grade_counts(conn, VIDEO_TABLE, "视频最终等级", False)
            low_confidence = _confidence_count(
                conn, VIDEO_TABLE, "评分置信度", ["很低", "低", "中"], False
            )
            result["summary"]["video"] = {
                "total": total,
                "counts": counts,
                "low_confidence": low_confidence,
            }
        else:
            result["summary"]["video"] = {"total": 0, "counts": {}, "low_confidence": 0}

    warning_parts = []
    if search_uid:
        warning_parts.append(f"筛选UP {search_uid}")
    if has_eligible_filter:
        warning_parts.append(f"UP榜仅展示 full 缓存且未归档UP {eligible_count} 位")
    if archived_count:
        warning_parts.append(f"已排除 active 归档UP {archived_count} 位")
    ladder_excluded_count = 0
    if db_path.exists():
        with sqlite3.connect(str(db_path)) as conn:
            ladder_excluded_count = _active_ladder_exclusion_count(conn)
    if ladder_excluded_count:
        warning_parts.append(f"天梯榜已取消资格UP {ladder_excluded_count} 位")
    tian_can_hidden_count = 0
    if db_path.exists():
        with sqlite3.connect(str(db_path)) as conn:
            tian_can_hidden_count = tian_can_board.active_tian_can_hidden_count(conn)
    if tian_can_hidden_count:
        warning_parts.append(f"天参榜已处理UP {tian_can_hidden_count} 位")
    if stale_creator_count:
        warning_parts.append(f"非 full/已归档UP评分 {stale_creator_count} 位未展示")
    if missing_creator_score_count:
        warning_parts.append(f"full UP缺少评分 {missing_creator_score_count} 位，请重新运行UP主评分或评分刷新")
    result["warning_parts"] = warning_parts
    result["message"] = (
        f"评分数据已加载：{db_path}\n"
        "UP主S级只来自手动等级；喜欢页S级视频可让已关注UP主进入详情数据，"
        "自动评分最高为A级。数据来自后端 SQLite API。"
    )
    return result


def _load_archived_creator_detail(conn, uploader_id):
    if not _table_exists(conn, "douyin_archived_creators"):
        return None
    row = conn.execute(
        """
        SELECT *
        FROM douyin_archived_creators
        WHERE uploader_id = ? AND archive_status = 'active'
        LIMIT 1
        """,
        (uploader_id,),
    ).fetchone()
    if not row:
        return None
    data = dict(row)
    return {
        "UP主姓名": data.get("uploader_name", ""),
        "UP主UID": data.get("uploader_id", ""),
        "UP主主页链接": data.get("homepage_url", ""),
        "UP手动等级": data.get("manual_grade", ""),
        "UP最终等级": data.get("final_grade", ""),
        "UP最终分": data.get("final_score", ""),
        "评级置信度": data.get("confidence", ""),
        "评分来源": "archived_snapshot",
        "粉丝数": data.get("follower_count", ""),
        "获赞总数": data.get("total_like_count", ""),
        "最近更新时间": data.get("latest_publish_time", ""),
        "未更新天数": data.get("inactive_days", ""),
        "平均几天一更": data.get("avg_update_days", ""),
        "视频数量": data.get("published_video_count", ""),
        "评分原因": data.get("archive_reason", ""),
        "缺失指标": "",
    }


def _load_cached_video_count(conn, uploader_id):
    counts = []
    if _table_exists(conn, "cache_inventory_current"):
        row = conn.execute(
            'SELECT "缓存视频数" FROM "cache_inventory_current" WHERE "UP主UID" = ? LIMIT 1',
            (uploader_id,),
        ).fetchone()
        if row:
            counts.append(_safe_int(row[0], 0))
    if _table_exists(conn, "douyin_video_state"):
        row = conn.execute(
            'SELECT COUNT(*) FROM "douyin_video_state" WHERE "uploader_id" = ?',
            (uploader_id,),
        ).fetchone()
        if row:
            counts.append(int(row[0] or 0))
    return max(counts) if counts else 0


def _count_videos_for_creator(conn, uploader_id):
    if not _table_exists(conn, VIDEO_TABLE):
        return 0
    row = conn.execute(
        f'SELECT COUNT(*) FROM "{VIDEO_TABLE}" WHERE "UP主UID" = ?',
        (uploader_id,),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _count_downloaded_snapshot_for_creator(conn, uploader_id):
    if not _table_exists(conn, VIDEO_TABLE):
        return 0
    columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{VIDEO_TABLE}")')}
    if "下载状态" not in columns and "下载路径" not in columns:
        return 0
    status_expr = 'TRIM(COALESCE("下载状态", "")) != ""' if "下载状态" in columns else "0"
    path_expr = 'TRIM(COALESCE("下载路径", "")) != ""' if "下载路径" in columns else "0"
    row = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM "{VIDEO_TABLE}"
        WHERE "UP主UID" = ?
          AND ({status_expr} OR {path_expr})
        """,
        (uploader_id,),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _count_aweme_download_records_for_creator(conn, uploader_id):
    if not _table_exists(conn, "aweme"):
        return 0
    columns = {row[1] for row in conn.execute('PRAGMA table_info("aweme")')}
    if "author_id" not in columns:
        return 0
    file_filter = 'AND TRIM(COALESCE(file_path, "")) != ""' if "file_path" in columns else ""
    row = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM aweme
        WHERE TRIM(COALESCE(author_id, "")) = ?
        {file_filter}
        """,
        (uploader_id,),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _group_video_values(conn, uploader_id, column, grade_order=False):
    if not _table_exists(conn, VIDEO_TABLE):
        return []
    columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{VIDEO_TABLE}")')}
    if column not in columns:
        return []
    rows = conn.execute(
        f"""
        SELECT COALESCE(NULLIF(TRIM("{column}"), ''), '未分类') AS name, COUNT(*) AS count
        FROM "{VIDEO_TABLE}"
        WHERE "UP主UID" = ?
        GROUP BY COALESCE(NULLIF(TRIM("{column}"), ''), '未分类')
        """,
        (uploader_id,),
    ).fetchall()
    values = [(str(row["name"] or "未分类"), int(row["count"] or 0)) for row in rows]
    if grade_order:
        order = {grade: index for index, grade in enumerate(GRADE_ORDER)}
        values.sort(key=lambda item: order.get(item[0], len(order)))
    else:
        values.sort(key=lambda item: item[1], reverse=True)
    return values


def _group_like_values(conn, uploader_id):
    if not _table_exists(conn, VIDEO_TABLE):
        return []
    columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{VIDEO_TABLE}")')}
    if "点赞数" not in columns:
        return []
    rows = conn.execute(
        f'SELECT "点赞数" FROM "{VIDEO_TABLE}" WHERE "UP主UID" = ?',
        (uploader_id,),
    ).fetchall()
    buckets = [
        ("0-999", 0, 999),
        ("1千-9999", 1000, 9999),
        ("1万-9.9万", 10000, 99999),
        ("10万+", 100000, None),
    ]
    counts = {label: 0 for label, _, _ in buckets}
    missing = 0
    for (value,) in rows:
        try:
            like_count = int(float(str(value or "").replace(",", "")))
        except (TypeError, ValueError):
            missing += 1
            continue
        matched = False
        for label, lower, upper in buckets:
            if like_count >= lower and (upper is None or like_count <= upper):
                counts[label] += 1
                matched = True
                break
        if not matched:
            missing += 1
    values = [(label, count) for label, count in counts.items() if count > 0]
    if missing:
        values.append(("无点赞数据", missing))
    return values


def _group_year_values(conn, uploader_id):
    if not _table_exists(conn, VIDEO_TABLE):
        return []
    columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{VIDEO_TABLE}")')}
    if "发布时间戳" not in columns and "发布日期" not in columns:
        return []
    publish_expr = '"发布时间戳"' if "发布时间戳" in columns else "''"
    date_expr = '"发布日期"' if "发布日期" in columns else "''"
    like_expr = '"点赞数"' if "点赞数" in columns else "0"
    rows = conn.execute(
        f"""
        SELECT {publish_expr} AS publish_ts, {date_expr} AS publish_date, {like_expr} AS like_count
        FROM "{VIDEO_TABLE}"
        WHERE "UP主UID" = ?
        """,
        (uploader_id,),
    ).fetchall()
    grouped = {}
    for publish_ts, publish_date, like_count in rows:
        year = _extract_year(publish_ts, publish_date)
        item = grouped.setdefault(year, {"count": 0, "likes": 0})
        item["count"] += 1
        item["likes"] += _safe_number(like_count)
    sortable = sorted(grouped.items(), key=lambda item: (item[0] == "未知年份", item[0]))
    known_years = [item for item in sortable if item[0] != "未知年份"]
    unknown_years = [item for item in sortable if item[0] == "未知年份"]
    return [(year, data["count"], data["likes"]) for year, data in list(reversed(known_years)) + unknown_years]


def _load_like_series(conn, uploader_id):
    if not _table_exists(conn, VIDEO_TABLE):
        return []
    columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{VIDEO_TABLE}")')}
    if "点赞数" not in columns:
        return []
    title_expr = '"视频标题"' if "视频标题" in columns else "''"
    publish_expr = '"发布时间戳"' if "发布时间戳" in columns else "0"
    date_expr = '"发布日期"' if "发布日期" in columns else "''"
    grade_expr = '"视频最终等级"' if "视频最终等级" in columns else "''"
    rows = conn.execute(
        f"""
        SELECT {title_expr} AS title,
               {publish_expr} AS publish_ts,
               {date_expr} AS publish_date,
               "点赞数" AS like_count,
               {grade_expr} AS grade
        FROM "{VIDEO_TABLE}"
        WHERE "UP主UID" = ?
        """,
        (uploader_id,),
    ).fetchall()
    values = []
    for row in rows:
        values.append(
            {
                "title": str(row["title"] or "").strip(),
                "publish_ts": _safe_number(row["publish_ts"]),
                "publish_date": str(row["publish_date"] or "").strip(),
                "like_count": _safe_number(row["like_count"]),
                "grade": str(row["grade"] or "").strip(),
            }
        )
    values.sort(
        key=lambda item: (
            item.get("publish_ts") or 0,
            item.get("publish_date") or "",
            item.get("title") or "",
        )
    )
    return values


def get_creator_detail(uploader_id: str) -> dict[str, Any]:
    db_path, source_db_path = _rating_db_paths()
    uploader_id = str(uploader_id or "").strip()
    if not uploader_id or not db_path.exists():
        return {}
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        _attach_source_views(conn, source_db_path, db_path)
        creator = conn.execute(
            f'SELECT * FROM "{CREATOR_TABLE}" WHERE "UP主UID" = ? LIMIT 1',
            (uploader_id,),
        ).fetchone()
        if not creator:
            creator = _load_archived_creator_detail(conn, uploader_id)
        if not creator:
            return {}
        cached_video_count = _load_cached_video_count(conn, uploader_id)
        scored_video_count = _count_videos_for_creator(conn, uploader_id)
        downloaded_snapshot = _count_downloaded_snapshot_for_creator(conn, uploader_id)
        downloaded_records = _count_aweme_download_records_for_creator(conn, uploader_id)
        return {
            "creator": dict(creator),
            "cached_video_count": cached_video_count,
            "scored_video_count": scored_video_count,
            "downloaded_count": max(downloaded_snapshot, downloaded_records),
            "duration_rows": _group_video_values(conn, uploader_id, "时长分类"),
            "grade_rows": _group_video_values(conn, uploader_id, "视频最终等级", True),
            "like_rows": _group_like_values(conn, uploader_id),
            "year_rows": _group_year_values(conn, uploader_id),
            "like_series": _load_like_series(conn, uploader_id),
        }


def save_creator_manual_grade(uploader_id: str, grade: str, note: str = "") -> dict[str, Any]:
    db_path, _source_db_path = _rating_db_paths()
    uploader_id = str(uploader_id or "").strip()
    grade = str(grade or "").strip().upper()
    if not uploader_id:
        raise ValueError("Missing uploader_id")
    if grade and grade not in set(GRADE_ORDER):
        raise ValueError(f"Unsupported grade: {grade}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS douyin_creator_manual_rating (
                uploader_id TEXT PRIMARY KEY,
                manual_grade TEXT NOT NULL,
                note TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        if grade:
            conn.execute(
                """
                INSERT INTO douyin_creator_manual_rating
                    (uploader_id, manual_grade, note, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(uploader_id) DO UPDATE SET
                    manual_grade=excluded.manual_grade,
                    note=excluded.note,
                    updated_at=excluded.updated_at
                """,
                (uploader_id, grade, note, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
        else:
            conn.execute(
                "DELETE FROM douyin_creator_manual_rating WHERE uploader_id = ?",
                (uploader_id,),
            )
        conn.commit()
    return {"ok": True, "uploader_id": uploader_id, "manual_grade": grade}


def exclude_creator_from_ladder(uploader_id: str, reason: str = "天梯榜取消资格") -> dict[str, Any]:
    db_path, _source_db_path = _rating_db_paths()
    uploader_id = str(uploader_id or "").strip()
    if not uploader_id:
        raise ValueError("Missing uploader_id")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    uploader_name = ""
    with sqlite3.connect(str(db_path)) as conn:
        _ensure_ladder_exclusion_table(conn)
        if _table_exists(conn, CREATOR_TABLE):
            row = conn.execute(
                f'SELECT "UP主姓名" FROM "{CREATOR_TABLE}" WHERE "UP主UID" = ? LIMIT 1',
                (uploader_id,),
            ).fetchone()
            uploader_name = str(row[0] or "").strip() if row else ""
        conn.execute(
            f"""
            INSERT INTO "{LADDER_EXCLUSION_TABLE}"
                (uploader_id, uploader_name, exclude_status, excluded_at, exclude_reason)
            VALUES (?, ?, 'active', ?, ?)
            ON CONFLICT(uploader_id) DO UPDATE SET
                uploader_name=excluded.uploader_name,
                exclude_status='active',
                excluded_at=excluded.excluded_at,
                exclude_reason=excluded.exclude_reason
            """,
            (
                uploader_id,
                uploader_name,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                str(reason or "").strip() or "天梯榜取消资格",
            ),
        )
        conn.commit()
    return {"ok": True, "uploader_id": uploader_id}


def dismiss_tian_can_creator(uploader_id: str, reason: str = "天参榜取消提示") -> dict[str, Any]:
    return tian_can_board.dismiss_tian_can_creator(uploader_id, reason)


def unfollow_tian_can_creator(
    uploader_id: str,
    homepage_url: str,
    reason: str = "天参榜取消关注",
) -> dict[str, Any]:
    return tian_can_board.unfollow_tian_can_creator(uploader_id, homepage_url, reason)


def _is_full_inventory_row(row):
    cached_modes = str(row.get("已缓存模式") or "").lower()
    last_fetch_mode = str(row.get("最近抓取模式") or "").strip().lower()
    has_full = str(row.get("有full缓存") or "").strip().lower()
    return (
        "full" in {part.strip() for part in cached_modes.split(",")}
        or last_fetch_mode == "full"
        or has_full in {"是", "yes", "true", "1", "y"}
    )


def _load_db_reset_uids(db_path):
    if not db_path.exists():
        return set()
    with sqlite3.connect(str(db_path)) as conn:
        if not _table_exists(conn, "douyin_full_status_reset"):
            return set()
        rows = conn.execute(
            """
            SELECT uploader_id
            FROM douyin_full_status_reset
            WHERE reset_status = 'active'
            """
        ).fetchall()
    return {str(row[0] or "").strip() for row in rows if str(row[0] or "").strip()}


def _load_reset_uids(config, db_path):
    from douyin_analyzer.cache import CacheStore

    reset_uids = _load_db_reset_uids(db_path)
    try:
        progress = CacheStore(config).load_progress()
    except Exception:
        return reset_uids
    for uid, entry in (progress or {}).items():
        if not isinstance(entry, dict):
            continue
        summary = entry.get("summary") if isinstance(entry.get("summary"), dict) else {}
        if entry.get("full_status_reset") or str(summary.get("summary_scope") or "").strip().lower() == "status_reset":
            reset_uids.add(str(uid or "").strip())
    return {uid for uid in reset_uids if uid}


def get_status_reset_candidates(threshold: int = 30) -> dict[str, Any]:
    config = _load_douyin_config()
    db_path = Path(config.export_store_db)
    threshold = int(threshold or 30)
    if not db_path.exists():
        return {"db_path": str(db_path), "rows": []}
    reset_uids = _load_reset_uids(config, db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, "cache_inventory_current"):
            return {"db_path": str(db_path), "rows": []}
        rows = conn.execute('SELECT * FROM "cache_inventory_current"').fetchall()
    candidates = []
    for row in rows:
        row_dict = dict(row)
        uid = str(row_dict.get("UP主UID") or "").strip()
        if uid and uid in reset_uids:
            continue
        if not _is_full_inventory_row(row_dict):
            continue
        published = _safe_int(row_dict.get("发布视频数量"))
        cached = _safe_int(row_dict.get("缓存视频数"))
        diff = published - cached
        if published <= 0 or diff <= threshold:
            continue
        candidates.append(
            {
                "uploader_id": uid,
                "uploader_name": row_dict.get("UP主姓名") or "",
                "published_video_count": published,
                "cached_video_count": cached,
                "diff_count": diff,
                "last_fetch_mode": row_dict.get("最近抓取模式") or "",
                "cache_modes": row_dict.get("已缓存模式") or "",
                "progress_cached_at": row_dict.get("进度缓存时间") or "",
                "homepage_url": row_dict.get("UP主主页链接") or "",
            }
        )
    candidates.sort(key=lambda item: item["diff_count"], reverse=True)
    return {"db_path": str(db_path), "rows": candidates}


def _ensure_reset_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS douyin_full_status_reset (
            uploader_id TEXT PRIMARY KEY,
            uploader_name TEXT,
            reset_status TEXT NOT NULL DEFAULT 'active',
            reset_at TEXT NOT NULL,
            reset_reason TEXT,
            published_video_count INTEGER,
            cached_video_count INTEGER,
            diff_count INTEGER
        )
        """
    )


def reset_full_status(uids: list[str]) -> dict[str, Any]:
    from douyin_analyzer.cache import CacheStore

    config = _load_douyin_config()
    db_path = Path(config.export_store_db)
    rows = get_status_reset_candidates(0).get("rows", [])
    candidate_by_uid = {row["uploader_id"]: row for row in rows}
    cache_store = CacheStore(config)
    progress = cache_store.load_progress()
    if not isinstance(progress, dict):
        return {"count": 0}
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    changed = 0
    for uid in sorted({str(uid or "").strip() for uid in (uids or []) if str(uid or "").strip()}):
        entry = progress.get(uid)
        row = candidate_by_uid.get(uid, {})
        if isinstance(entry, dict):
            raw_modes = entry.get("cache_modes") or []
            if isinstance(raw_modes, str):
                raw_modes = raw_modes.split(",")
            modes = [
                str(mode).strip().lower()
                for mode in raw_modes
                if str(mode).strip() and str(mode).strip().lower() != "full"
            ]
            entry["cache_modes"] = sorted(set(modes))
            entry["last_fetch_mode"] = "status_reset"
            entry["cached_at"] = 0
            entry["full_status_reset"] = {
                "reset_at": now_text,
                "reason": "full_cached_video_count_mismatch",
                "published_video_count": row.get("published_video_count", ""),
                "cached_video_count": row.get("cached_video_count", ""),
                "diff_count": row.get("diff_count", ""),
            }
            summary = entry.get("summary")
            if isinstance(summary, dict):
                summary["summary_scope"] = "status_reset"
                summary["status_reset_at"] = now_text
                summary["status_reset_reason"] = "full_cached_video_count_mismatch"
        changed += 1
    if changed:
        cache_store.save_progress(progress)
        _record_reset_rows(db_path, uids, candidate_by_uid, now_text)
        _update_inventory_rows(db_path, uids)
    return {"count": changed}


def _record_reset_rows(db_path, uids, candidate_by_uid, reset_at):
    if not db_path.exists():
        return
    with sqlite3.connect(str(db_path)) as conn:
        _ensure_reset_table(conn)
        for uid in uids:
            row = candidate_by_uid.get(uid, {})
            conn.execute(
                """
                INSERT INTO douyin_full_status_reset (
                    uploader_id, uploader_name, reset_status, reset_at, reset_reason,
                    published_video_count, cached_video_count, diff_count
                )
                VALUES (?, ?, 'active', ?, 'full_cached_video_count_mismatch', ?, ?, ?)
                ON CONFLICT(uploader_id) DO UPDATE SET
                    uploader_name=excluded.uploader_name,
                    reset_status='active',
                    reset_at=excluded.reset_at,
                    reset_reason=excluded.reset_reason,
                    published_video_count=excluded.published_video_count,
                    cached_video_count=excluded.cached_video_count,
                    diff_count=excluded.diff_count
                """,
                (
                    uid,
                    row.get("uploader_name", ""),
                    reset_at,
                    _safe_int(row.get("published_video_count")),
                    _safe_int(row.get("cached_video_count")),
                    _safe_int(row.get("diff_count")),
                ),
            )
        conn.commit()


def _update_inventory_rows(db_path, uids):
    if not db_path.exists():
        return
    with sqlite3.connect(str(db_path)) as conn:
        if not _table_exists(conn, "cache_inventory_current"):
            return
        for uid in uids:
            row = conn.execute(
                'SELECT "已缓存模式" FROM "cache_inventory_current" WHERE "UP主UID" = ?',
                (uid,),
            ).fetchone()
            modes = []
            if row:
                modes = [
                    part.strip()
                    for part in str(row[0] or "").split(",")
                    if part.strip() and part.strip().lower() != "full"
                ]
            conn.execute(
                """
                UPDATE "cache_inventory_current"
                SET "已缓存模式" = ?,
                    "最近抓取模式" = 'status_reset',
                    "有full缓存" = '',
                    "进度缓存时间" = '',
                    "下次可抓取时间" = '',
                    "是否已到期" = '是',
                    "统计范围" = 'status_reset'
                WHERE "UP主UID" = ?
                """,
                (",".join(sorted(set(modes))), uid),
            )
        conn.commit()


def get_archive_state(threshold: int = 100) -> dict[str, Any]:
    from douyin_analyzer.archive import load_archive_candidates, load_archived_creators

    db_path = _export_db_path()
    rating_db_path, _source_db_path = _rating_db_paths()
    candidates = load_archive_candidates(
        db_path,
        inactive_days_threshold=threshold,
        rating_db_path=rating_db_path,
    )
    archived_rows = load_archived_creators(db_path, active_only=False)
    return {
        "db_path": str(db_path),
        "candidates": candidates,
        "archived_rows": archived_rows,
    }


def archive_creators_by_uid(uids: list[str], threshold: int = 100) -> dict[str, Any]:
    from douyin_analyzer.archive import archive_creators

    state = get_archive_state(threshold)
    wanted = {str(uid or "").strip() for uid in (uids or []) if str(uid or "").strip()}
    rows = [row for row in state["candidates"] if str(row.get("uploader_id") or "") in wanted]
    return {"count": archive_creators(Path(state["db_path"]), rows)}


def archive_all_candidates(threshold: int = 100) -> dict[str, Any]:
    from douyin_analyzer.archive import archive_creators

    state = get_archive_state(threshold)
    return {"count": archive_creators(Path(state["db_path"]), state["candidates"])}


def restore_archived_creators(uids: list[str]) -> dict[str, Any]:
    from douyin_analyzer.archive import restore_creators

    db_path = _export_db_path()
    return {"count": restore_creators(db_path, uids)}


def get_bilibili_archive_state(threshold: int = 100) -> dict[str, Any]:
    from bilibili_analyzer.archive import load_archive_candidates, load_archived_creators

    config = _load_bilibili_config()
    db_path = Path(config.export_store_db)
    candidates = load_archive_candidates(config, inactive_days_threshold=threshold)
    archived_rows = load_archived_creators(db_path, active_only=False)
    return {
        "db_path": str(db_path),
        "candidates": candidates,
        "archived_rows": archived_rows,
    }


def archive_bilibili_creators_by_uid(uids: list[str], threshold: int = 100) -> dict[str, Any]:
    from bilibili_analyzer.archive import archive_creators

    state = get_bilibili_archive_state(threshold)
    wanted = {str(uid or "").strip() for uid in (uids or []) if str(uid or "").strip()}
    rows = [row for row in state["candidates"] if str(row.get("uploader_id") or "") in wanted]
    return {"count": archive_creators(Path(state["db_path"]), rows)}


def archive_all_bilibili_candidates(threshold: int = 100) -> dict[str, Any]:
    from bilibili_analyzer.archive import archive_creators

    state = get_bilibili_archive_state(threshold)
    return {"count": archive_creators(Path(state["db_path"]), state["candidates"])}


def restore_bilibili_archived_creators(uids: list[str]) -> dict[str, Any]:
    from bilibili_analyzer.archive import restore_creators

    config = _load_bilibili_config()
    return {"count": restore_creators(config.export_store_db, uids)}
