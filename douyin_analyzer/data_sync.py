import json
import sqlite3
from datetime import datetime

from common.platform_store import ensure_platform_schema

from .analyzer import DouyinHiatusAnalyzer
from .cache import CacheStore
from .creator_scoring import run_douyin_creator_scoring
from .exporters import save_cache_inventory_to_csv
from .video_scoring import DouyinVideoScorer, run_douyin_video_scoring


VIDEO_ID_COLUMN = "\u89c6\u9891ID"
DOWNLOAD_STATUS_COLUMN = "\u4e0b\u8f7d\u72b6\u6001"
UPLOADER_ID_COLUMN = "UP\u4e3bUID"
SCORED_VIDEO_COUNT_COLUMN = "\u5df2\u8bc4\u5206\u89c6\u9891\u6570"
LAST_FETCH_MODE_COLUMN = "\u6700\u8fd1\u6293\u53d6\u6a21\u5f0f"
HAS_FULL_CACHE_COLUMN = "\u6709full\u7f13\u5b58"
PUBLISHED_VIDEO_COUNT_COLUMN = "\u53d1\u5e03\u89c6\u9891\u6570\u91cf"
CACHED_VIDEO_COUNT_COLUMN = "\u7f13\u5b58\u89c6\u9891\u6570"


def _table_count(db_path, table_name):
    if not db_path.exists():
        return 0
    with sqlite3.connect(db_path) as conn:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if not exists:
            return 0
        row = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()
    return int(row[0] or 0) if row else 0


def _table_exists(conn, table_name):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone() is not None


def _first_value(row, *keys):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _normalize_progress_video(video, uploader_id, uploader_name):
    row = dict(video)
    row.setdefault("uploader_id", uploader_id)
    row.setdefault("uploader_name", uploader_name)
    row.setdefault("author_id", uploader_id)
    row.setdefault("author_name", uploader_name)

    title = _first_value(row, "video_title", "title", "desc", "description")
    if title is not None:
        row.setdefault("video_title", title)

    like_count = _first_value(row, "like_count", "digg_count", "点赞数")
    if like_count is not None:
        row.setdefault("like_count", like_count)

    publish_timestamp = _first_value(row, "publish_timestamp", "create_time", "发布时间戳")
    if publish_timestamp is not None:
        row.setdefault("publish_timestamp", publish_timestamp)

    duration_seconds = _first_value(row, "duration_seconds", "duration", "video_duration")
    if duration_seconds is not None:
        row.setdefault("duration_seconds", duration_seconds)

    return row


def _loads_json(text):
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_status_reset_uids(config):
    reset_uids = set()
    db_path = config.export_store_db
    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as conn:
                if _table_exists(conn, "douyin_full_status_reset"):
                    reset_uids.update(
                        str(row[0] or "").strip()
                        for row in conn.execute(
                            """
                            SELECT uploader_id
                            FROM douyin_full_status_reset
                            WHERE reset_status = 'active'
                            """
                        ).fetchall()
                        if str(row[0] or "").strip()
                    )
        except Exception:
            pass
    try:
        progress = CacheStore(config).load_progress()
    except Exception:
        return reset_uids
    for uid, entry in (progress or {}).items():
        if not isinstance(entry, dict):
            continue
        summary = entry.get("summary") if isinstance(entry.get("summary"), dict) else {}
        summary_scope = str(summary.get("summary_scope") or "").strip().lower()
        if entry.get("full_status_reset") or summary_scope == "status_reset":
            reset_uids.add(str(uid or "").strip())
    return {uid for uid in reset_uids if uid}


def _load_current_progress_video_ids(config):
    try:
        videos = DouyinVideoScorer(config)._load_progress_video_rows()
    except Exception:
        return set()
    return {
        str((video or {}).get("aweme_id") or (video or {}).get("video_id") or "").strip()
        for video in (videos or [])
        if isinstance(video, dict)
        and str((video or {}).get("aweme_id") or (video or {}).get("video_id") or "").strip()
    }


def _safe_int(value, default=None):
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _bulk_upsert_video_state(conn, rows, source_mode="current"):
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sql = """
        INSERT INTO "douyin_video_state" (
            video_id,
            uploader_id,
            uploader_name,
            publish_timestamp,
            like_count,
            duration_seconds,
            source_mode,
            first_seen_at,
            last_seen_at,
            is_available,
            payload_json,
            metadata_json,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            uploader_id=COALESCE(NULLIF(excluded.uploader_id, ''), "douyin_video_state".uploader_id),
            uploader_name=COALESCE(NULLIF(excluded.uploader_name, ''), "douyin_video_state".uploader_name),
            publish_timestamp=COALESCE(excluded.publish_timestamp, "douyin_video_state".publish_timestamp),
            like_count=COALESCE(excluded.like_count, "douyin_video_state".like_count),
            duration_seconds=COALESCE(excluded.duration_seconds, "douyin_video_state".duration_seconds),
            source_mode=CASE
                WHEN LOWER(COALESCE("douyin_video_state".source_mode, '')) = 'full'
                     AND LOWER(COALESCE(excluded.source_mode, '')) != 'full'
                THEN "douyin_video_state".source_mode
                ELSE COALESCE(NULLIF(excluded.source_mode, ''), "douyin_video_state".source_mode)
            END,
            last_seen_at=excluded.last_seen_at,
            is_available=1,
            payload_json=excluded.payload_json,
            metadata_json=COALESCE(excluded.metadata_json, "douyin_video_state".metadata_json),
            updated_at=excluded.updated_at
    """
    values = []
    for row in rows or []:
        row = row if isinstance(row, dict) else {}
        video_id = str(row.get("aweme_id") or row.get("video_id") or "").strip()
        if not video_id:
            continue
        uploader_id = str(row.get("uploader_id") or row.get("author_id") or "").strip()
        uploader_name = str(row.get("uploader_name") or row.get("author_name") or "").strip()
        metadata = row.get("metadata")
        metadata_json = json.dumps(metadata, ensure_ascii=False) if isinstance(metadata, (dict, list)) else None
        values.append(
            (
                video_id,
                uploader_id,
                uploader_name,
                _safe_int(row.get("publish_timestamp") or row.get("create_time")),
                _safe_int(row.get("like_count"), 0),
                _safe_int(row.get("duration_seconds")),
                str(row.get("_sync_source_mode") or source_mode or "").strip(),
                now_text,
                now_text,
                json.dumps(row, ensure_ascii=False),
                metadata_json,
                now_text,
            )
        )
    if values:
        conn.executemany(sql, values)
    return len(values)


def _sync_raw_videos_to_state(config, batch_size=5000):
    db_path = config.export_store_db
    if not db_path.exists():
        return 0
    ensure_platform_schema(db_path, "douyin")
    processed = 0
    with sqlite3.connect(db_path) as conn:
        if not _table_exists(conn, "douyin_video_raw"):
            return 0
        missing_raw_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM douyin_video_raw AS r
            LEFT JOIN douyin_video_state AS s ON r.video_id = s.video_id
            WHERE r.video_id IS NOT NULL
              AND TRIM(r.video_id) != ''
              AND s.video_id IS NULL
            """
        ).fetchone()[0]
        if missing_raw_count <= 0:
            return 0
        cursor = conn.execute(
            """
            SELECT uploader_id, video_id, publish_timestamp, payload_json
            FROM douyin_video_raw
            WHERE video_id IS NOT NULL AND TRIM(video_id) != ''
            """
        )
        while True:
            raw_rows = cursor.fetchmany(batch_size)
            if not raw_rows:
                break
            video_rows = []
            for uploader_id, video_id, publish_timestamp, payload_json in raw_rows:
                payload = _loads_json(payload_json)
                row = _normalize_progress_video(payload, str(uploader_id or "").strip(), "")
                row.setdefault("aweme_id", str(video_id or "").strip())
                row.setdefault("video_id", str(video_id or "").strip())
                if publish_timestamp not in (None, ""):
                    row.setdefault("publish_timestamp", publish_timestamp)
                video_rows.append(row)
            if video_rows:
                processed += _bulk_upsert_video_state(conn, video_rows, source_mode="raw")
        conn.commit()
    return processed


def _sync_progress_videos_to_state(config, progress):
    ensure_platform_schema(config.export_store_db, "douyin")
    processed_creators = 0
    processed_videos = 0
    skipped_creators = 0
    video_rows = []

    for uid, entry in progress.items():
        if not isinstance(entry, dict):
            skipped_creators += 1
            continue
        videos = entry.get("videos") or []
        if not isinstance(videos, list) or not videos:
            skipped_creators += 1
            continue

        user = entry.get("user") if isinstance(entry.get("user"), dict) else {}
        uploader_id = str((user or {}).get("sec_uid") or uid or "").strip()
        uploader_name = (user or {}).get("nickname") or (user or {}).get("uploader_name") or ""
        source_mode = str(entry.get("last_fetch_mode") or "progress").strip() or "progress"
        summary = entry.get("summary") if isinstance(entry.get("summary"), dict) else {}
        summary_scope = str(summary.get("summary_scope") or "").strip().lower()
        raw_cache_modes = entry.get("cache_modes") or []
        if isinstance(raw_cache_modes, str):
            raw_cache_modes = raw_cache_modes.split(",")
        cache_modes = {
            str(mode or "").strip().lower()
            for mode in raw_cache_modes
            if str(mode or "").strip()
        }
        if not entry.get("full_status_reset") and (
            "full" in cache_modes
            or str(entry.get("last_fetch_mode") or "").strip().lower() == "full"
            or summary_scope in {"full", "preserved_full"}
        ):
            source_mode = "full"

        entry_count = 0
        for video in videos:
            if not isinstance(video, dict):
                continue
            row = _normalize_progress_video(video, uploader_id, uploader_name)
            row["_sync_source_mode"] = source_mode
            video_rows.append(row)
            entry_count += 1

        if entry_count:
            processed_creators += 1
            processed_videos += entry_count
        else:
            skipped_creators += 1

    if video_rows:
        with sqlite3.connect(config.export_store_db) as conn:
            _bulk_upsert_video_state(conn, video_rows, source_mode="progress")
            conn.commit()

    return processed_creators, processed_videos, skipped_creators


def _refresh_inventory_video_counts(config):
    db_path = config.export_store_db
    if not db_path.exists():
        return 0
    with sqlite3.connect(db_path) as conn:
        if not (
            _table_exists(conn, "cache_inventory_current")
            and _table_exists(conn, "douyin_video_state")
        ):
            return 0
        rows = conn.execute(
            """
            SELECT uploader_id, COUNT(*) AS count
            FROM douyin_video_state
            WHERE uploader_id IS NOT NULL AND TRIM(uploader_id) != ''
            GROUP BY uploader_id
            """
        ).fetchall()
        updated = 0
        for uploader_id, count in rows:
            cursor = conn.execute(
                f'''
                UPDATE cache_inventory_current
                SET "{CACHED_VIDEO_COUNT_COLUMN}" = ?
                WHERE "{UPLOADER_ID_COLUMN}" = ?
                  AND CAST(COALESCE("{CACHED_VIDEO_COUNT_COLUMN}", 0) AS INTEGER) != ?
                ''',
                (int(count or 0), str(uploader_id or "").strip(), int(count or 0)),
            )
            updated += cursor.rowcount or 0
        conn.commit()
    return updated


def _refresh_cache_inventory_current(config):
    cache_store = CacheStore(config)
    analyzer = DouyinHiatusAnalyzer(config, browser_client=None, cache_store=cache_store)
    cache_rows = analyzer.build_cache_inventory_rows(
        cache_store.load_followings_cache_payload(),
        cache_store.load_progress(),
    )
    save_cache_inventory_to_csv(config, cache_rows)
    return len(cache_rows)


def diagnose_data_links(config):
    db_path = config.export_store_db
    current_progress_video_ids = _load_current_progress_video_ids(config)
    report = {
        "cache_inventory_current": _table_count(db_path, "cache_inventory_current"),
        "douyin_video_state": _table_count(db_path, "douyin_video_state"),
        "video_score_current": _table_count(db_path, "video_score_current"),
        "creator_score_current": _table_count(db_path, "creator_score_current"),
        "aweme": _table_count(db_path, "aweme"),
        "douyin_video_raw": _table_count(db_path, "douyin_video_raw"),
        "current_progress_videos": len(current_progress_video_ids),
        "current_progress_not_scored": 0,
        "score_not_current_progress": 0,
        "state_not_scored": 0,
        "score_not_state": 0,
        "score_download_mark_without_aweme": 0,
        "creator_not_inventory": 0,
        "full_inventory_not_creator_score": 0,
        "creator_score_zero_but_state_has": 0,
        "full_cache_count_mismatch_gt_30": 0,
    }
    if not db_path.exists():
        return report

    reset_uids = _load_status_reset_uids(config)
    reset_filter = ""
    reset_params = []
    if reset_uids:
        placeholders = ",".join("?" for _ in reset_uids)
        reset_filter = f'AND i."{UPLOADER_ID_COLUMN}" NOT IN ({placeholders})'
        reset_params = sorted(reset_uids)

    with sqlite3.connect(db_path) as conn:
        if current_progress_video_ids:
            scored_ids = set()
            if _table_exists(conn, "video_score_current"):
                scored_ids = {
                    str(row[0] or "").strip()
                    for row in conn.execute(
                        f'''
                        SELECT "{VIDEO_ID_COLUMN}"
                        FROM video_score_current
                        WHERE "{VIDEO_ID_COLUMN}" IS NOT NULL
                          AND TRIM("{VIDEO_ID_COLUMN}") != ''
                        '''
                    ).fetchall()
                    if str(row[0] or "").strip()
                }
            report["current_progress_not_scored"] = len(current_progress_video_ids - scored_ids)
            report["score_not_current_progress"] = len(scored_ids - current_progress_video_ids)

        if _table_exists(conn, "douyin_video_state") and _table_exists(conn, "video_score_current"):
            report["state_not_scored"] = conn.execute(
                f'''
                SELECT COUNT(*)
                FROM douyin_video_state AS s
                LEFT JOIN video_score_current AS v ON s.video_id = v."{VIDEO_ID_COLUMN}"
                WHERE v."{VIDEO_ID_COLUMN}" IS NULL
                '''
            ).fetchone()[0]
            report["score_not_state"] = conn.execute(
                f'''
                SELECT COUNT(*)
                FROM video_score_current AS v
                LEFT JOIN douyin_video_state AS s ON v."{VIDEO_ID_COLUMN}" = s.video_id
                WHERE s.video_id IS NULL
                '''
            ).fetchone()[0]

        if _table_exists(conn, "aweme") and _table_exists(conn, "video_score_current"):
            report["score_download_mark_without_aweme"] = conn.execute(
                f'''
                SELECT COUNT(*)
                FROM video_score_current AS v
                LEFT JOIN aweme AS a ON v."{VIDEO_ID_COLUMN}" = a.aweme_id
                WHERE COALESCE(v."{DOWNLOAD_STATUS_COLUMN}", '') != ''
                  AND a.aweme_id IS NULL
                '''
            ).fetchone()[0]

        if _table_exists(conn, "creator_score_current") and _table_exists(conn, "cache_inventory_current"):
            report["creator_not_inventory"] = conn.execute(
                f'''
                SELECT COUNT(*)
                FROM creator_score_current AS c
                LEFT JOIN cache_inventory_current AS i ON c."{UPLOADER_ID_COLUMN}" = i."{UPLOADER_ID_COLUMN}"
                WHERE i."{UPLOADER_ID_COLUMN}" IS NULL
                '''
            ).fetchone()[0]
            report["full_inventory_not_creator_score"] = conn.execute(
                f'''
                SELECT COUNT(*)
                FROM cache_inventory_current AS i
                LEFT JOIN creator_score_current AS c ON c."{UPLOADER_ID_COLUMN}" = i."{UPLOADER_ID_COLUMN}"
                WHERE (
                    LOWER(COALESCE(i."{LAST_FETCH_MODE_COLUMN}", '')) = 'full'
                    OR LOWER(COALESCE(i."{HAS_FULL_CACHE_COLUMN}", '')) IN ('是', 'yes', 'true', '1')
                )
                AND c."{UPLOADER_ID_COLUMN}" IS NULL
                {reset_filter}
                ''',
                reset_params,
            ).fetchone()[0]

            report["full_cache_count_mismatch_gt_30"] = conn.execute(
                f'''
                SELECT COUNT(*)
                FROM cache_inventory_current AS i
                WHERE (
                    LOWER(COALESCE(i."{LAST_FETCH_MODE_COLUMN}", '')) = 'full'
                    OR LOWER(COALESCE(i."{HAS_FULL_CACHE_COLUMN}", '')) IN ('是', 'yes', 'true', '1')
                )
                AND (
                    CAST(COALESCE(i."{PUBLISHED_VIDEO_COUNT_COLUMN}", 0) AS INTEGER)
                    - CAST(COALESCE(i."{CACHED_VIDEO_COUNT_COLUMN}", 0) AS INTEGER)
                ) > 30
                {reset_filter}
                ''',
                reset_params,
            ).fetchone()[0]

        if (
            _table_exists(conn, "creator_score_current")
            and _table_exists(conn, "douyin_video_state")
        ):
            report["creator_score_zero_but_state_has"] = conn.execute(
                f'''
                SELECT COUNT(*)
                FROM creator_score_current AS c
                JOIN (
                    SELECT uploader_id, COUNT(*) AS n
                    FROM douyin_video_state
                    GROUP BY uploader_id
                ) AS s ON c."{UPLOADER_ID_COLUMN}" = s.uploader_id
                WHERE CAST(COALESCE(c."{SCORED_VIDEO_COUNT_COLUMN}", 0) AS INTEGER) = 0
                  AND s.n > 0
                '''
            ).fetchone()[0]
    return report


def sync_progress_videos_to_state(config, *, rerun_scores=True):
    before_diagnostics = diagnose_data_links(config)
    cache_store = CacheStore(config)
    progress = cache_store.load_progress()
    if not isinstance(progress, dict):
        progress = {}

    before_state_count = _table_count(config.export_store_db, "douyin_video_state")
    before_video_score_count = _table_count(config.export_store_db, "video_score_current")
    before_creator_score_count = _table_count(config.export_store_db, "creator_score_current")

    raw_videos_processed = _sync_raw_videos_to_state(config)
    processed_creators, processed_videos, skipped_creators = _sync_progress_videos_to_state(config, progress)
    inventory_rows_updated = _refresh_cache_inventory_current(config)

    video_score_path = None
    creator_score_path = None
    if rerun_scores:
        video_score_path = run_douyin_video_scoring(config)
        creator_score_path = run_douyin_creator_scoring(config)

    after_state_count = _table_count(config.export_store_db, "douyin_video_state")
    after_video_score_count = _table_count(config.export_store_db, "video_score_current")
    after_creator_score_count = _table_count(config.export_store_db, "creator_score_current")
    after_diagnostics = diagnose_data_links(config)

    return {
        "processed_creators": processed_creators,
        "skipped_creators": skipped_creators,
        "processed_videos": processed_videos,
        "raw_videos_processed": raw_videos_processed,
        "inventory_rows_updated": inventory_rows_updated,
        "video_state_before": before_state_count,
        "video_state_after": after_state_count,
        "video_score_before": before_video_score_count,
        "video_score_after": after_video_score_count,
        "creator_score_before": before_creator_score_count,
        "creator_score_after": after_creator_score_count,
        "video_score_path": str(video_score_path or ""),
        "creator_score_path": str(creator_score_path or ""),
        "before_diagnostics": before_diagnostics,
        "after_diagnostics": after_diagnostics,
    }
