import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .utils import normalize_timestamp, timestamp_to_date


ARCHIVE_TABLE = "douyin_archived_creators"
ACTIVE_STATUS = "active"
RESTORED_STATUS = "restored"
MANUAL_KEEP_GRADES = {"S", "A"}


def _now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def _connect(db_path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _table_exists(conn, table_name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _read_table(conn, table_name):
    if not _table_exists(conn, table_name):
        return []
    return [dict(row) for row in conn.execute(f'SELECT * FROM "{table_name}"').fetchall()]


def _pick(row, *names, default=""):
    row = row if isinstance(row, dict) else {}
    for name in names:
        if name in row and row.get(name) not in (None, ""):
            return row.get(name)
    return default


def _text(value):
    return str(value or "").strip()


def _upper(value):
    return _text(value).upper()


def _is_yes(value):
    return _text(value).lower() in {"是", "yes", "true", "1", "y", "full"}


def _safe_float(value, default=0.0):
    try:
        text = _text(value).replace(",", "")
        return float(text) if text else default
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=0):
    try:
        text = _text(value).replace(",", "")
        return int(float(text)) if text else default
    except (TypeError, ValueError):
        return default


def _json_dumps(data):
    return json.dumps(data or {}, ensure_ascii=False, default=str)


def _inactive_days_from_timestamp(timestamp):
    timestamp = normalize_timestamp(timestamp)
    if not timestamp:
        return None
    try:
        seconds = (datetime.now() - datetime.fromtimestamp(timestamp)).total_seconds()
    except Exception:
        return None
    return round(max(seconds / 86400, 0), 2)


def _video_title_from_payload(payload_json, fallback=""):
    try:
        payload = json.loads(payload_json or "{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return _text(
        payload.get("video_title")
        or payload.get("title")
        or payload.get("desc")
        or payload.get("description")
        or fallback
    )


def _remember_latest_video(result, uid, timestamp, title="", video_id=""):
    uid = _text(uid)
    timestamp = normalize_timestamp(timestamp)
    if not uid or timestamp <= 0:
        return
    current = result.get(uid) or {}
    if timestamp <= normalize_timestamp(current.get("publish_timestamp")):
        return
    result[uid] = {
        "publish_timestamp": timestamp,
        "publish_time": timestamp_to_date(timestamp),
        "video_title": _text(title),
        "video_id": _text(video_id),
    }


def _load_latest_cached_videos(conn):
    result = {}
    if _table_exists(conn, "douyin_video_state"):
        rows = conn.execute(
            """
            SELECT uploader_id, video_id, publish_timestamp, payload_json
            FROM douyin_video_state
            WHERE uploader_id IS NOT NULL AND TRIM(uploader_id) != ''
              AND publish_timestamp IS NOT NULL AND TRIM(CAST(publish_timestamp AS TEXT)) != ''
            """
        ).fetchall()
        for row in rows:
            _remember_latest_video(
                result,
                row["uploader_id"],
                row["publish_timestamp"],
                _video_title_from_payload(row["payload_json"], row["video_id"]),
                row["video_id"],
            )

    if _table_exists(conn, "video_score_current"):
        for row in _read_table(conn, "video_score_current"):
            _remember_latest_video(
                result,
                _pick(row, "UP主UID", "uploader_id"),
                _pick(row, "发布时间戳", "publish_timestamp"),
                _pick(row, "视频标题", "video_title", "title"),
                _pick(row, "视频ID", "video_id", "aweme_id"),
            )
    return result


def ensure_archive_table(db_path):
    with _connect(db_path) as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{ARCHIVE_TABLE}" (
                uploader_id TEXT PRIMARY KEY,
                uploader_name TEXT,
                homepage_url TEXT,
                manual_grade TEXT,
                final_grade TEXT,
                final_score REAL,
                confidence TEXT,
                follower_count INTEGER,
                total_like_count INTEGER,
                published_video_count INTEGER,
                cached_video_count INTEGER,
                latest_video_title TEXT,
                latest_publish_time TEXT,
                inactive_days REAL,
                avg_update_days REAL,
                cached_modes TEXT,
                last_fetch_mode TEXT,
                has_full_cache TEXT,
                archived_at TEXT,
                archive_reason TEXT,
                archive_status TEXT NOT NULL DEFAULT 'active',
                restored_at TEXT,
                restore_reason TEXT,
                source_snapshot_json TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f'CREATE INDEX IF NOT EXISTS "idx_{ARCHIVE_TABLE}_status" '
            f'ON "{ARCHIVE_TABLE}" (archive_status)'
        )
        conn.commit()


def load_active_archived_uids(db_path):
    ensure_archive_table(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f'SELECT uploader_id FROM "{ARCHIVE_TABLE}" WHERE archive_status=?',
            (ACTIVE_STATUS,),
        ).fetchall()
    return {_text(row["uploader_id"]) for row in rows if _text(row["uploader_id"])}


def is_creator_archived(db_path, uploader_id):
    uploader_id = _text(uploader_id)
    if not uploader_id:
        return False
    ensure_archive_table(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            f'SELECT 1 FROM "{ARCHIVE_TABLE}" WHERE uploader_id=? AND archive_status=?',
            (uploader_id, ACTIVE_STATUS),
        ).fetchone()
    return row is not None


def _load_manual_keep_uids(conn):
    keep = set()
    if _table_exists(conn, "douyin_creator_manual_rating"):
        for row in conn.execute(
            'SELECT uploader_id, manual_grade FROM "douyin_creator_manual_rating"'
        ).fetchall():
            if _upper(row["manual_grade"]) in MANUAL_KEEP_GRADES:
                uid = _text(row["uploader_id"])
                if uid:
                    keep.add(uid)
    return keep


def _has_full_data(inventory_row):
    has_full = _pick(inventory_row, "有full缓存", "has_full_cache")
    cached_modes = _pick(inventory_row, "已缓存模式", "cached_modes")
    last_fetch_mode = _pick(inventory_row, "最近抓取模式", "last_fetch_mode")
    scope = _pick(inventory_row, "统计范围", "scope")
    mode_values = {part.strip() for part in _text(cached_modes).lower().split(",") if part.strip()}
    return (
        _is_yes(has_full)
        or "full" in mode_values
        or _text(last_fetch_mode).lower() == "full"
        or _text(scope).lower() == "full"
    )


def _merge_creator_snapshot(uid, inventory, main, analysis, score, latest_cached_video=None):
    inventory = inventory or {}
    main = main or {}
    analysis = analysis or {}
    score = score or {}
    latest_cached_video = latest_cached_video or {}
    uploader_name = _pick(score, "UP主姓名") or _pick(main, "UP主姓名") or _pick(inventory, "UP主姓名")
    homepage_url = _pick(score, "UP主主页链接") or _pick(main, "UP主主页链接") or _pick(inventory, "UP主主页链接")
    latest_publish_time = (
        _pick(latest_cached_video, "publish_time")
        or _pick(score, "最近更新时间")
        or _pick(main, "最后活跃/发布日期")
        or _pick(inventory, "缓存最新发布时间")
    )
    inactive_days = (
        _inactive_days_from_timestamp(_pick(latest_cached_video, "publish_timestamp"))
        if latest_cached_video
        else None
    )
    if inactive_days is None:
        inactive_days = _safe_float(
            _pick(score, "未更新天数") or _pick(main, "未更新天数", "距离最后一个视频发布(天)"),
            default=0.0,
        )
    published_video_count = max(
        _safe_int(_pick(score, "视频数量"), 0),
        _safe_int(_pick(main, "发布视频数量"), 0),
        _safe_int(_pick(analysis, "视频总数"), 0),
        _safe_int(_pick(inventory, "发布视频数量"), 0),
    )
    row = {
        "uploader_id": uid,
        "uploader_name": uploader_name,
        "homepage_url": homepage_url,
        "manual_grade": _pick(score, "UP手动等级"),
        "final_grade": _pick(score, "UP最终等级"),
        "final_score": _safe_float(_pick(score, "UP最终分"), 0.0),
        "confidence": _pick(score, "评级置信度"),
        "follower_count": max(_safe_int(_pick(score, "粉丝数"), 0), _safe_int(_pick(main, "粉丝数"), 0), _safe_int(_pick(inventory, "粉丝数"), 0)),
        "total_like_count": max(_safe_int(_pick(score, "获赞总数"), 0), _safe_int(_pick(main, "获赞总数"), 0), _safe_int(_pick(inventory, "获赞总数"), 0)),
        "published_video_count": published_video_count,
        "cached_video_count": _safe_int(_pick(inventory, "缓存视频数"), 0),
        "latest_video_title": (
            _pick(latest_cached_video, "video_title")
            or _pick(main, "最新视频标题")
            or _pick(inventory, "缓存最新视频标题")
        ),
        "latest_publish_time": latest_publish_time,
        "inactive_days": inactive_days,
        "avg_update_days": _safe_float(_pick(score, "平均几天一更") or _pick(main, "平均几天一更"), 0.0),
        "cached_modes": _pick(inventory, "已缓存模式"),
        "last_fetch_mode": _pick(inventory, "最近抓取模式"),
        "has_full_cache": _pick(inventory, "有full缓存", "has_full_cache"),
        "source_snapshot": {
            "cache_inventory_current": inventory,
            "main_sheet_current": main,
            "analysis_sheet_current": analysis,
            "creator_score_current": score,
        },
    }
    return row


def load_archive_candidates(db_path, inactive_days_threshold=100):
    ensure_archive_table(db_path)
    threshold = _safe_float(inactive_days_threshold, 100.0)
    with _connect(db_path) as conn:
        inventory_rows = _read_table(conn, "cache_inventory_current")
        main_by_uid = {
            _text(_pick(row, "UP主UID", "UP涓籙ID", "uploader_id")): row
            for row in _read_table(conn, "main_sheet_current")
        }
        analysis_by_uid = {
            _text(_pick(row, "UP主UID", "UP涓籙ID", "uploader_id")): row
            for row in _read_table(conn, "analysis_sheet_current")
        }
        score_by_uid = {
            _text(_pick(row, "UP主UID", "UP涓籙ID", "uploader_id")): row
            for row in _read_table(conn, "creator_score_current")
        }
        latest_video_by_uid = _load_latest_cached_videos(conn)
        active_archived = load_active_archived_uids(db_path)
        manual_keep = _load_manual_keep_uids(conn)

    candidates = []
    seen = set()
    for inventory in inventory_rows:
        uid = _text(_pick(inventory, "UP主UID", "UP涓籙ID", "uploader_id"))
        if not uid or uid in seen:
            continue
        seen.add(uid)
        if uid in active_archived or not _has_full_data(inventory):
            continue
        score = score_by_uid.get(uid, {})
        manual_grade = _upper(_pick(score, "UP手动等级"))
        if uid in manual_keep or manual_grade in MANUAL_KEEP_GRADES:
            continue
        row = _merge_creator_snapshot(
            uid,
            inventory,
            main_by_uid.get(uid, {}),
            analysis_by_uid.get(uid, {}),
            score,
            latest_video_by_uid.get(uid),
        )
        if row["inactive_days"] < threshold:
            continue
        candidates.append(row)
    candidates.sort(key=lambda item: (item.get("inactive_days") or 0, item.get("final_score") or 0), reverse=True)
    return candidates


def archive_creators(db_path, creator_rows, reason=None):
    ensure_archive_table(db_path)
    now = _now_text()
    reason = _text(reason) or "长期未更新，已手动归档"
    rows = [row for row in (creator_rows or []) if _text(row.get("uploader_id"))]
    if not rows:
        return 0
    with _connect(db_path) as conn:
        for row in rows:
            conn.execute(
                f"""
                INSERT INTO "{ARCHIVE_TABLE}" (
                    uploader_id, uploader_name, homepage_url, manual_grade, final_grade,
                    final_score, confidence, follower_count, total_like_count,
                    published_video_count, cached_video_count, latest_video_title,
                    latest_publish_time, inactive_days, avg_update_days, cached_modes,
                    last_fetch_mode, has_full_cache, archived_at, archive_reason,
                    archive_status, restored_at, restore_reason, source_snapshot_json,
                    updated_at
                ) VALUES (
                    :uploader_id, :uploader_name, :homepage_url, :manual_grade, :final_grade,
                    :final_score, :confidence, :follower_count, :total_like_count,
                    :published_video_count, :cached_video_count, :latest_video_title,
                    :latest_publish_time, :inactive_days, :avg_update_days, :cached_modes,
                    :last_fetch_mode, :has_full_cache, :archived_at, :archive_reason,
                    :archive_status, '', '', :source_snapshot_json, :updated_at
                )
                ON CONFLICT(uploader_id) DO UPDATE SET
                    uploader_name=excluded.uploader_name,
                    homepage_url=excluded.homepage_url,
                    manual_grade=excluded.manual_grade,
                    final_grade=excluded.final_grade,
                    final_score=excluded.final_score,
                    confidence=excluded.confidence,
                    follower_count=excluded.follower_count,
                    total_like_count=excluded.total_like_count,
                    published_video_count=excluded.published_video_count,
                    cached_video_count=excluded.cached_video_count,
                    latest_video_title=excluded.latest_video_title,
                    latest_publish_time=excluded.latest_publish_time,
                    inactive_days=excluded.inactive_days,
                    avg_update_days=excluded.avg_update_days,
                    cached_modes=excluded.cached_modes,
                    last_fetch_mode=excluded.last_fetch_mode,
                    has_full_cache=excluded.has_full_cache,
                    archived_at=excluded.archived_at,
                    archive_reason=excluded.archive_reason,
                    archive_status=excluded.archive_status,
                    restored_at='',
                    restore_reason='',
                    source_snapshot_json=excluded.source_snapshot_json,
                    updated_at=excluded.updated_at
                """,
                {
                    **row,
                    "archived_at": now,
                    "archive_reason": reason,
                    "archive_status": ACTIVE_STATUS,
                    "source_snapshot_json": _json_dumps(row.get("source_snapshot")),
                    "updated_at": now,
                },
            )
        conn.commit()
    return len(rows)


def load_archived_creators(db_path, active_only=False):
    ensure_archive_table(db_path)
    with _connect(db_path) as conn:
        if active_only:
            rows = conn.execute(
                f'SELECT * FROM "{ARCHIVE_TABLE}" WHERE archive_status=? ORDER BY inactive_days DESC',
                (ACTIVE_STATUS,),
            ).fetchall()
        else:
            rows = conn.execute(
                f'SELECT * FROM "{ARCHIVE_TABLE}" ORDER BY archive_status ASC, inactive_days DESC'
            ).fetchall()
    return [dict(row) for row in rows]


def restore_creators(db_path, uploader_ids, reason=None):
    ensure_archive_table(db_path)
    now = _now_text()
    ids = [_text(uid) for uid in (uploader_ids or []) if _text(uid)]
    if not ids:
        return 0
    with _connect(db_path) as conn:
        count = 0
        for uid in ids:
            cursor = conn.execute(
                f"""
                UPDATE "{ARCHIVE_TABLE}"
                SET archive_status=?, restored_at=?, restore_reason=?, updated_at=?
                WHERE uploader_id=? AND archive_status=?
                """,
                (RESTORED_STATUS, now, _text(reason) or "手动恢复", now, uid, ACTIVE_STATUS),
            )
            count += cursor.rowcount or 0
        conn.commit()
    return count
