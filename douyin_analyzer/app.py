import json
import sqlite3
import traceback
from datetime import datetime
from pathlib import Path

from common.file_io import atomic_write_csv, atomic_write_text
from common.platform_store import (
    upsert_cache_entries,
    upsert_video_state_rows,
)
from bilibili_analyzer.feishu_uploader import FeishuUploader
from bilibili_analyzer.logging_utils import (
    create_summary_panel,
    get_console,
    setup_logging,
)

from .analyzer import DouyinHiatusAnalyzer
from .browser_client import DouyinBrowserClient
from .cache import CacheStore
from .config import load_analyzer_config, load_feishu_config
from .exporters import save_cache_inventory_to_csv, save_video_duration_analysis_to_csv
from .playwright_browser_client import PlaywrightDouyinBrowserClient
from .rating.store import rating_store_db_path, source_store_db_path


def create_douyin_browser_client(config):
    backend = str(getattr(config, "browser_backend", "drission") or "drission").strip().lower()
    if backend == "playwright":
        return PlaywrightDouyinBrowserClient(config)
    return DouyinBrowserClient(config)


def load_unfollow_targets(list_path):
    path = Path(list_path)
    if not path.exists():
        get_console().print(create_summary_panel("Unfollow List Missing", [str(path)], border_style="red"))
        return []

    targets = []
    with path.open("r", encoding="utf-8") as unfollow_file:
        for line in unfollow_file:
            text = line.strip()
            if text and not text.startswith("#"):
                targets.append(text)
    return targets


def remove_unfollow_target(list_path, homepage):
    path = Path(list_path)
    if not path.exists():
        return

    normalized_homepage = DouyinBrowserClient.normalize_homepage_url(homepage)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return

    updated_lines = []
    removed = False
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#"):
            updated_lines.append(line)
            continue
        normalized_line = DouyinBrowserClient.normalize_homepage_url(raw)
        if not removed and normalized_line == normalized_homepage:
            removed = True
            continue
        updated_lines.append(line)

    if removed:
        atomic_write_text(path, "\n".join(updated_lines) + ("\n" if updated_lines else ""), encoding="utf-8")


def remove_unfollowed_local_state(config, homepage):
    cache_store = CacheStore(config)
    removed_uids = cache_store.remove_unfollowed_user(homepage=homepage)
    if removed_uids:
        get_console().print(
            create_summary_panel(
                "Douyin Local State Cleaned",
                [
                    f"Removed UID count: {len(removed_uids)}",
                    f"UIDs: {', '.join(removed_uids[:5])}" + (" ..." if len(removed_uids) > 5 else ""),
                ],
                border_style="green",
            )
        )
    return removed_uids


def run_partial_feishu_upload(processed_count):
    run_feishu_upload(prune_missing=False)


def run_cached_feishu_preupload(fetch_mode_override=None):
    config = load_analyzer_config(fetch_mode_override=fetch_mode_override)
    cache_store = CacheStore(config)
    if cache_store.is_followings_cache_expired():
        get_console().print(
            create_summary_panel(
                "Douyin Cached Preupload Skipped",
                [
                    "Followings cache is expired.",
                    "Cached preupload was skipped to avoid pushing stale unfollowed creators.",
                ],
                border_style="yellow",
            )
        )
        return False
    analyzer = DouyinHiatusAnalyzer(
        config,
        browser_client=None,
        cache_store=cache_store,
        upload_callback=None,
    )
    if not analyzer.export_cached_snapshot():
        return False

    get_console().print(
        create_summary_panel(
            "Douyin Cached Snapshot",
            ["Cached main-sheet snapshot will be uploaded before the live crawl starts."],
            border_style="cyan",
        )
    )
    run_feishu_upload(prune_missing=False)
    return True


def run_analysis(
    trigger_upload=True,
    fetch_mode_override=None,
    max_followings=None,
    recent_video_limit_override=None,
    reporter=None,
    export_outputs=True,
):
    config = load_analyzer_config(
        fetch_mode_override=fetch_mode_override,
        recent_video_limit_override=recent_video_limit_override,
    )
    setup_logging(config.log_dir, "douyin_app")
    export_outputs = bool(export_outputs or trigger_upload)

    fetch_mode = (config.fetch_mode or "counts").strip().lower()
    enable_partial_upload = trigger_upload and export_outputs and fetch_mode != "counts"

    browser_client = create_douyin_browser_client(config)
    cache_store = CacheStore(config)
    analyzer = DouyinHiatusAnalyzer(
        config,
        browser_client,
        cache_store,
        upload_callback=run_partial_feishu_upload if enable_partial_upload else None,
        max_followings=max_followings,
        reporter=reporter,
        export_outputs=export_outputs,
    )

    if enable_partial_upload:
        try:
            run_cached_feishu_preupload(fetch_mode_override=fetch_mode_override)
        except Exception as exc:
            get_console().print(
                create_summary_panel(
                    "Preupload Skipped",
                    [f"Cached preupload failed but the crawl will continue: {exc}"],
                    border_style="yellow",
                )
            )
    elif trigger_upload and fetch_mode == "counts":
        get_console().print(
            create_summary_panel(
                "Douyin Counts Mode",
                ["Counts mode will upload only once after the full crawl finishes."],
                border_style="cyan",
            )
        )

    try:
        results = analyzer.analyze_hiatus()
    finally:
        browser_client.close()

    if trigger_upload and results is not None:
        get_console().print(
            create_summary_panel(
                "Douyin Main Sheet Sync",
                ["Analysis finished. Main data sheet will be synced now."],
                border_style="cyan",
            )
        )
        run_feishu_upload(prune_missing=True)

    return results


def run_feishu_upload(prune_missing=True):
    config = load_feishu_config()
    setup_logging(config.log_dir, "douyin_feishu_upload")

    analyzer_config = load_analyzer_config()
    cache_store = CacheStore(analyzer_config)
    if cache_store.is_followings_cache_expired():
        get_console().print(
            create_summary_panel(
                "Douyin Upload Blocked",
                [
                    "Followings cache is expired.",
                    "Upload was blocked to avoid syncing stale followed creators to Feishu.",
                    "Please run a fresh Douyin crawl before uploading the main sheet again.",
                ],
                border_style="red",
            )
        )
        return False

    analyzer = DouyinHiatusAnalyzer(
        analyzer_config,
        browser_client=None,
        cache_store=cache_store,
        upload_callback=None,
    )
    analyzer.export_cached_snapshot()

    uploader = FeishuUploader(config)
    uploader.run(prune_missing=prune_missing)
    return True


def run_unfollow(list_path):
    config = load_analyzer_config(fetch_mode_override="counts")
    setup_logging(config.log_dir, "douyin_unfollow")

    targets = load_unfollow_targets(list_path)
    if not targets:
        get_console().print(create_summary_panel("Douyin Unfollow", ["No valid homepage found in list."], border_style="yellow"))
        return []

    browser_client = create_douyin_browser_client(config)
    try:
        browser_client.ensure_login()
        results = browser_client.unfollow_users_by_homepages(
            targets,
            on_unfollowed=lambda homepage: (
                remove_unfollow_target(list_path, homepage),
                remove_unfollowed_local_state(config, homepage),
            ),
        )
    finally:
        browser_client.close()

    unfollowed = sum(1 for item in results if item.get("status") == "unfollowed")
    skipped = sum(1 for item in results if item.get("status") == "skipped")
    failed = sum(1 for item in results if item.get("status") not in {"unfollowed", "skipped"})

    get_console().print(
        create_summary_panel(
            "Douyin Unfollow Finished",
            [
                f"Targets: {len(results)}",
                f"Unfollowed: {unfollowed}",
                f"Already not followed: {skipped}",
                f"Failed: {failed}",
            ],
            border_style="green",
        )
    )
    return results


def run_cache_liked_videos_as_s(limit=None):
    config = load_analyzer_config()
    setup_logging(config.log_dir, "douyin_liked_video_cache")
    browser_client = create_douyin_browser_client(config)
    try:
        browser_client.ensure_login()
        videos = browser_client.get_liked_videos_from_homepage(limit=limit)
    finally:
        browser_client.close()

    output_path = config.output_csv.parent / "douyin_liked_videos_cached.csv"
    fieldnames = [
        "uploader_name",
        "uploader_id",
        "video_id",
        "aweme_id",
        "video_title",
        "video_url",
        "publish_date",
        "publish_timestamp",
        "duration_seconds",
        "like_count",
        "video_manual_grade",
    ]
    rows = []
    for video in videos or []:
        row = dict(video)
        video_id = str(row.get("aweme_id") or row.get("video_id") or "").strip()
        if not video_id:
            continue
        row["video_id"] = video_id
        row["aweme_id"] = video_id
        row["video_manual_grade"] = "S"
        row["source_mode"] = "liked"
        rows.append(row)

    atomic_write_csv(output_path, fieldnames, rows)
    upsert_video_state_rows(
        source_store_db_path(config),
        "douyin",
        rows,
        video_id_column="aweme_id",
        uploader_id_column="uploader_id",
        uploader_name_column="uploader_name",
        source_mode="liked",
    )
    upsert_cache_entries(
        config.export_store_db,
        "douyin",
        {row["aweme_id"]: row for row in rows if row.get("aweme_id")},
        cache_type="liked_videos",
        source_mode="liked",
        uploader_id_getter=lambda _key, payload: (payload or {}).get("uploader_id", ""),
        cached_at_getter=lambda _payload: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    _upsert_manual_video_grades(
        rating_store_db_path(config),
        [row.get("aweme_id") for row in rows],
        grade="S",
        note="homepage liked video cache",
    )

    from .rating.video_scoring import run_douyin_video_scoring

    video_score_path = run_douyin_video_scoring(config)
    try:
        from .rating.creator_scoring import run_douyin_creator_scoring

        run_douyin_creator_scoring(config)
    except Exception as exc:
        get_console().print(
            create_summary_panel(
                "Douyin Creator Score Refresh Skipped",
                [str(exc)],
                border_style="yellow",
            )
        )

    get_console().print(
        create_summary_panel(
            "Douyin Liked Videos Cached",
            [
                f"Liked videos: {len(rows)}",
                "Manual video grade: S",
                f"Cache CSV: {output_path}",
                f"Video score CSV: {video_score_path}",
            ],
            border_style="green",
        )
    )
    return {
        "video_count": len(rows),
        "output_path": str(output_path),
        "video_score_path": str(video_score_path),
    }


def _upsert_manual_video_grades(db_path, video_ids, grade="S", note=""):
    video_ids = sorted({str(item or "").strip() for item in (video_ids or []) if str(item or "").strip()})
    if not video_ids:
        return 0
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS douyin_video_manual_rating (
                video_id TEXT PRIMARY KEY,
                manual_grade TEXT NOT NULL,
                note TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO douyin_video_manual_rating (video_id, manual_grade, note, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                manual_grade=excluded.manual_grade,
                note=excluded.note,
                updated_at=excluded.updated_at
            """,
            [(video_id, grade, note, updated_at) for video_id in video_ids],
        )
        conn.commit()
    return len(video_ids)


def run_score_videos_from_cache(*, refresh_inventory=True):
    config = load_analyzer_config()
    setup_logging(config.log_dir, "douyin_video_scoring")
    if refresh_inventory:
        refresh_cache_inventory_current(config)
    from .rating.video_scoring import run_douyin_video_scoring

    return run_douyin_video_scoring(config)


def run_score_creators_from_cache(*, refresh_inventory=True):
    config = load_analyzer_config()
    setup_logging(config.log_dir, "douyin_creator_scoring")
    if refresh_inventory:
        refresh_cache_inventory_current(config)
    if _sqlite_table_count(rating_store_db_path(config), "video_score_current") <= 0:
        from .rating.video_scoring import run_douyin_video_scoring

        run_douyin_video_scoring(config)
    from .rating.creator_scoring import run_douyin_creator_scoring

    return run_douyin_creator_scoring(config)


def refresh_cache_inventory_current(config=None):
    config = config or load_analyzer_config()
    cache_store = CacheStore(config)
    analyzer = DouyinHiatusAnalyzer(config, browser_client=None, cache_store=cache_store)
    cache_rows = analyzer.build_cache_inventory_rows(
        cache_store.load_followings_cache_payload(),
        cache_store.load_progress(),
    )
    save_cache_inventory_to_csv(config, cache_rows)
    return len(cache_rows)


def run_prune_export_snapshots(vacuum=False):
    config = load_analyzer_config()
    from common.export_store import prune_disabled_snapshot_history

    deleted = prune_disabled_snapshot_history(config.export_store_db)
    if vacuum:
        import sqlite3

        with sqlite3.connect(config.export_store_db) as conn:
            conn.execute("VACUUM")
    return deleted


def _sqlite_table_count(db_path, table_name):
    import sqlite3
    from pathlib import Path

    db_path = Path(db_path)
    if not db_path.exists():
        return 0
    with sqlite3.connect(db_path) as conn:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if not exists:
            return 0
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0] or 0)


def _load_liked_video_state_rows(db_path):
    db_path = Path(db_path) if db_path else None
    if not db_path or not db_path.exists():
        return []

    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='douyin_video_state'"
            ).fetchone()
            if not exists:
                return []

            rows = conn.execute(
                """
                SELECT video_id, uploader_id, uploader_name, publish_timestamp, like_count,
                       duration_seconds, source_mode, payload_json
                FROM douyin_video_state
                WHERE video_id IS NOT NULL AND TRIM(video_id) != ''
                """
            ).fetchall()
    except Exception as exc:
        get_console().print(
            create_summary_panel(
                "Liked Video Cache Read Failed",
                [f"Database: {db_path}", f"Error: {exc}"],
                border_style="yellow",
            )
        )
        return []

    videos = []
    for raw in rows:
        video_id, uploader_id, uploader_name, publish_timestamp, like_count, duration_seconds, source_mode, payload_json = raw
        try:
            payload = json.loads(payload_json or "{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        if str(source_mode or "").strip().lower() != "liked" and metadata.get("liked_cache") is not True:
            continue
        row = dict(payload)
        row.setdefault("aweme_id", str(video_id or "").strip())
        row.setdefault("video_id", str(video_id or "").strip())
        row.setdefault("uploader_id", str(uploader_id or "").strip())
        row.setdefault("uploader_name", str(uploader_name or "").strip())
        row.setdefault("publish_timestamp", publish_timestamp)
        row.setdefault("like_count", like_count)
        row.setdefault("duration_seconds", duration_seconds)
        row.setdefault("video_manual_grade", "S")
        videos.append(row)
    return videos


def main(fetch_mode_override=None):
    try:
        run_analysis(trigger_upload=True, fetch_mode_override=fetch_mode_override)
    except KeyboardInterrupt:
        get_console().print(create_summary_panel("Interrupted", ["Execution was cancelled by user."], border_style="yellow"))
    except Exception as exc:
        get_console().print(create_summary_panel("Douyin Error", [str(exc)], border_style="red"))
        traceback.print_exc()


def upload_main():
    try:
        run_feishu_upload()
    except KeyboardInterrupt:
        get_console().print(create_summary_panel("Interrupted", ["Upload was cancelled by user."], border_style="yellow"))
    except Exception as exc:
        get_console().print(create_summary_panel("Upload Error", [str(exc)], border_style="red"))
        traceback.print_exc()
