import math
import sqlite3
from datetime import datetime
from pathlib import Path

from common.export_store import write_rows_to_table
from common.file_io import atomic_write_csv
from .video_scoring import (
    GRADE_SCORES,
    _format_unix_timestamp,
    _normalize_grade,
    _print_summary,
    _safe_float,
    _safe_int,
)


CREATOR_WEIGHTS = {
    "follower": 0.09,
    "total_like": 0.09,
    "recent_update": 0.12,
    "update_frequency": 0.10,
    "video_count": 0.08,
    "history_span": 0.07,
    "avg_like": 0.12,
    "video_grade": 0.15,
    "low_grade_ratio": 0.08,
    "recent_trend": 0.10,
}

FIELDNAMES = [
    "uploader_name",
    "uploader_id",
    "homepage_url",
    "manual_grade",
    "auto_score",
    "auto_grade",
    "final_score",
    "final_grade",
    "confidence",
    "score_source",
    "follower_count",
    "total_like_count",
    "latest_publish_date",
    "inactive_days",
    "avg_update_days",
    "video_count",
    "earliest_publish_date",
    "creator_span_days",
    "avg_like_count",
    "video_grade_score",
    "low_grade_ratio",
    "recent_trend_score",
    "risk_penalty",
    "follower_score",
    "total_like_score",
    "recent_update_score",
    "update_frequency_score",
    "video_count_score",
    "history_span_score",
    "avg_like_score",
    "low_grade_ratio_score",
    "scored_video_count",
    "recent_video_count",
    "grade_s_count",
    "grade_a_count",
    "grade_b_count",
    "grade_c_count",
    "grade_d_count",
    "score_reasons",
    "missing_metrics",
]

HEADERS = {
    "uploader_name": "UP主姓名",
    "uploader_id": "UP主UID",
    "homepage_url": "UP主主页链接",
    "manual_grade": "UP手动等级",
    "auto_score": "UP自动分",
    "auto_grade": "UP自动等级",
    "final_score": "UP最终分",
    "final_grade": "UP最终等级",
    "confidence": "评级置信度",
    "score_source": "评分来源",
    "follower_count": "粉丝数",
    "total_like_count": "获赞总数",
    "latest_publish_date": "最近更新时间",
    "inactive_days": "未更新天数",
    "avg_update_days": "平均几天一更",
    "video_count": "视频数量",
    "earliest_publish_date": "最早视频时间",
    "creator_span_days": "创作跨度(天)",
    "avg_like_count": "平均点赞数",
    "video_grade_score": "视频等级分布分",
    "low_grade_ratio": "低等级视频比例",
    "recent_trend_score": "最近10条趋势分",
    "risk_penalty": "风险扣分",
    "follower_score": "粉丝数量分",
    "total_like_score": "获赞总数分",
    "recent_update_score": "最近更新时间分",
    "update_frequency_score": "平均几天一更分",
    "video_count_score": "视频数量分",
    "history_span_score": "最早视频时间分",
    "avg_like_score": "平均点赞数分",
    "low_grade_ratio_score": "低等级比例分",
    "scored_video_count": "已评分视频数",
    "recent_video_count": "趋势统计视频数",
    "grade_s_count": "S级视频数",
    "grade_a_count": "A级视频数",
    "grade_b_count": "B级视频数",
    "grade_c_count": "C级视频数",
    "grade_d_count": "D级视频数",
    "score_reasons": "评分原因",
    "missing_metrics": "缺失指标",
}

VIDEO_GRADE_VALUES = {
    "S": 100,
    "A": 90,
    "B": 70,
    "C": 45,
    "D": 20,
}


def run_douyin_creator_scoring(config):
    scorer = DouyinCreatorScorer(config)
    rows = scorer.score()
    output_path = Path(getattr(config, "creator_score_csv", config.output_csv.parent / "douyin_creator_scores.csv"))
    atomic_write_csv(output_path, FIELDNAMES, rows, header_row=HEADERS)
    write_rows_to_table(
        config.export_store_db,
        "creator_score_current",
        FIELDNAMES,
        HEADERS,
        rows,
    )

    grade_counts = {}
    for row in rows:
        grade = row.get("final_grade") or "未知"
        grade_counts[grade] = grade_counts.get(grade, 0) + 1
    grade_summary = " / ".join(f"{grade}:{grade_counts.get(grade, 0)}" for grade in ["S", "A", "B", "C", "D"])
    _print_summary(
        "抖音UP主评分完成",
        [
            f"评分UP主数: {len(rows)}",
            f"等级分布: {grade_summary}",
            f"输出文件: {output_path}",
            "说明: S级只来自手动等级；自动评分最高为A级。",
        ],
    )
    return output_path


class DouyinCreatorScorer:
    def __init__(self, config):
        self.config = config
        self.db_path = Path(config.export_store_db)
        self.now_ts = int(datetime.now().timestamp())

    def score(self):
        self._ensure_manual_rating_tables()
        creator_manual = self._load_manual_grades()
        creator_rows = self._load_creator_rows()
        video_stats = self._load_video_stats()
        full_mode_uids = self._load_full_mode_uids()
        if full_mode_uids is not None:
            creator_rows = [
                row for row in creator_rows
                if str(row.get("uploader_id") or "").strip() in full_mode_uids
            ]
        else:
            creator_rows = [
                row for row in creator_rows
                if video_stats.get(str(row.get("uploader_id") or "").strip(), {}).get("scores")
            ]

        follower_p95 = _p95([row["follower_count"] for row in creator_rows])
        total_like_p95 = _p95([row["total_like_count"] for row in creator_rows])
        avg_like_p95 = _p95([row["avg_like_count"] for row in creator_rows])

        rows = []
        for creator in creator_rows:
            rows.append(
                self._score_creator(
                    creator,
                    video_stats.get(creator["uploader_id"], {}),
                    creator_manual.get(creator["uploader_id"], ""),
                    follower_p95,
                    total_like_p95,
                    avg_like_p95,
                )
            )

        rows.sort(
            key=lambda item: (
                -GRADE_SCORES.get(item.get("final_grade"), 0),
                -_safe_float(item.get("final_score"), 0),
                -_safe_int(item.get("follower_count"), 0),
            )
        )
        return rows

    def _ensure_manual_rating_tables(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
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
            conn.commit()

    def _load_manual_grades(self):
        if not self._table_exists("douyin_creator_manual_rating"):
            return {}
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                'SELECT uploader_id, manual_grade FROM "douyin_creator_manual_rating"'
            ).fetchall()
        result = {}
        for uploader_id, grade in rows:
            normalized = _normalize_grade(grade)
            if uploader_id and normalized:
                result[str(uploader_id).strip()] = normalized
        return result

    def _load_creator_rows(self):
        main_rows = self._read_table("main_sheet_current")
        analysis_rows = self._read_table("analysis_sheet_current")
        inventory_rows = self._read_table("cache_inventory_current")
        creators = {}

        for row in main_rows:
            uid = _pick_text(row, "UP主UID")
            if not uid:
                continue
            creators[uid] = {
                "uploader_id": uid,
                "uploader_name": _pick_text(row, "UP主姓名"),
                "homepage_url": _pick_text(row, "UP主主页链接"),
                "follower_count": _safe_int(_pick(row, "粉丝数"), 0),
                "total_like_count": _safe_int(_pick(row, "获赞总数"), 0),
                "latest_publish_date": _pick_text(row, "最后活跃/发布日期"),
                "inactive_days": _safe_float(
                    _pick(row, "未更新天数", "距离最后一个视频发布(天)"),
                    None,
                ),
                "avg_update_days": _safe_float(_pick(row, "平均几天一更"), None),
                "video_count": _safe_int(_pick(row, "发布视频数量"), 0),
                "avg_like_count": _safe_float(_pick(row, "平均点赞数"), 0),
            }

        for row in analysis_rows:
            uid = _pick_text(row, "UP主UID")
            if not uid:
                continue
            item = creators.setdefault(
                uid,
                {
                    "uploader_id": uid,
                    "uploader_name": _pick_text(row, "UP主姓名"),
                    "homepage_url": "",
                    "latest_publish_date": "",
                    "inactive_days": None,
                },
            )
            item["uploader_name"] = item.get("uploader_name") or _pick_text(row, "UP主姓名")
            item["follower_count"] = max(_safe_int(item.get("follower_count"), 0), _safe_int(_pick(row, "粉丝数"), 0))
            item["total_like_count"] = max(
                _safe_int(item.get("total_like_count"), 0),
                _safe_int(_pick(row, "获赞总数"), 0),
            )
            item["video_count"] = max(_safe_int(item.get("video_count"), 0), _safe_int(_pick(row, "视频总数"), 0))
            if _safe_float(item.get("avg_like_count"), 0) <= 0:
                item["avg_like_count"] = _safe_float(_pick(row, "平均点赞数"), 0)
            if item.get("avg_update_days") is None:
                item["avg_update_days"] = _safe_float(_pick(row, "平均几天一更"), None)

        for row in inventory_rows:
            values = list(row.values())
            uid = str(values[2] if len(values) > 2 else "").strip()
            if not uid:
                continue
            item = creators.setdefault(
                uid,
                {
                    "uploader_id": uid,
                    "uploader_name": str(values[0] if len(values) > 0 else "").strip(),
                    "homepage_url": str(values[3] if len(values) > 3 else "").strip(),
                    "latest_publish_date": str(values[24] if len(values) > 24 else "").strip(),
                    "inactive_days": None,
                    "avg_update_days": None,
                    "follower_count": 0,
                    "total_like_count": 0,
                    "video_count": 0,
                    "avg_like_count": 0,
                },
            )
            if not item.get("uploader_name") and len(values) > 0:
                item["uploader_name"] = str(values[0] or "").strip()
            if not item.get("homepage_url") and len(values) > 3:
                item["homepage_url"] = str(values[3] or "").strip()
            if not item.get("latest_publish_date") and len(values) > 24:
                item["latest_publish_date"] = str(values[24] or "").strip()

            published_count = _safe_int(values[6] if len(values) > 6 else 0, 0)
            cached_video_count = _safe_int(values[21] if len(values) > 21 else 0, 0)
            item["follower_count"] = max(
                _safe_int(item.get("follower_count"), 0),
                _safe_int(values[4] if len(values) > 4 else 0, 0),
            )
            item["total_like_count"] = max(
                _safe_int(item.get("total_like_count"), 0),
                _safe_int(values[5] if len(values) > 5 else 0, 0),
            )
            item["video_count"] = max(
                _safe_int(item.get("video_count"), 0),
                published_count,
                cached_video_count,
            )

        return list(creators.values())

    def _load_video_stats(self):
        rows = self._read_table("video_score_current") if self._table_exists("video_score_current") else []
        grouped = {}
        for row in rows:
            uid = _pick_text(row, "UP主UID")
            if not uid:
                continue
            grade = _normalize_grade(_pick(row, "视频最终等级"))
            score = _safe_float(_pick(row, "视频最终分"), None)
            publish_ts = _safe_int(_pick(row, "发布时间戳"), 0)
            item = grouped.setdefault(
                uid,
                {
                    "scores": [],
                    "videos": [],
                    "grade_counts": {grade_key: 0 for grade_key in ["S", "A", "B", "C", "D"]},
                    "earliest_publish_ts": 0,
                    "latest_publish_ts": 0,
                },
            )
            if grade:
                item["grade_counts"][grade] += 1
            if score is not None:
                item["scores"].append(score)
                item["videos"].append({"score": score, "publish_ts": publish_ts})
            if publish_ts > 0:
                if item["earliest_publish_ts"] <= 0 or publish_ts < item["earliest_publish_ts"]:
                    item["earliest_publish_ts"] = publish_ts
                if publish_ts > item["latest_publish_ts"]:
                    item["latest_publish_ts"] = publish_ts

        if self._table_exists("douyin_video_state"):
            with sqlite3.connect(self.db_path) as conn:
                state_rows = conn.execute(
                    """
                    SELECT uploader_id, publish_timestamp
                    FROM douyin_video_state
                    WHERE uploader_id IS NOT NULL AND TRIM(uploader_id) != ''
                      AND publish_timestamp IS NOT NULL AND TRIM(CAST(publish_timestamp AS TEXT)) != ''
                    """
                ).fetchall()
            for uid, publish_timestamp in state_rows:
                uid = str(uid or "").strip()
                publish_ts = _safe_int(publish_timestamp, 0)
                if not uid or publish_ts <= 0:
                    continue
                item = grouped.setdefault(
                    uid,
                    {
                        "scores": [],
                        "videos": [],
                        "grade_counts": {grade_key: 0 for grade_key in ["S", "A", "B", "C", "D"]},
                        "earliest_publish_ts": 0,
                        "latest_publish_ts": 0,
                    },
                )
                if item["earliest_publish_ts"] <= 0 or publish_ts < item["earliest_publish_ts"]:
                    item["earliest_publish_ts"] = publish_ts
                if publish_ts > item["latest_publish_ts"]:
                    item["latest_publish_ts"] = publish_ts

        for item in grouped.values():
            item["videos"].sort(key=lambda video: video.get("publish_ts") or 0, reverse=True)
        return grouped

    def _load_full_mode_uids(self):
        if not self._table_exists("cache_inventory_current"):
            return self._load_full_mode_uids_from_progress()

        rows = self._read_table("cache_inventory_current")
        uid_columns = ("UP主UID", "UP涓籙ID", "uploader_id")
        full_columns = ("有full缓存", "鏈塮ull缂撳瓨", "has_full_cache")
        result = set()
        for row in rows:
            uid = ""
            for column in uid_columns:
                uid = _pick_text(row, column)
                if uid:
                    break
            if not uid:
                continue

            has_full_cache = ""
            for column in full_columns:
                has_full_cache = _pick_text(row, column)
                if has_full_cache:
                    break
            cached_modes = _pick_text(row, "已缓存模式", "cached_modes")
            last_fetch_mode = _pick_text(row, "最近抓取模式", "last_fetch_mode")
            summary_scope = _pick_text(row, "统计范围", "summary_scope", "scope")
            mode_values = {
                part.strip()
                for part in str(cached_modes or "").lower().split(",")
                if part.strip()
            }
            if (
                _is_yes_value(has_full_cache)
                or "full" in mode_values
                or str(last_fetch_mode or "").strip().lower() == "full"
                or str(summary_scope or "").strip().lower() == "full"
            ):
                result.add(uid)
        try:
            from .archive import load_active_archived_uids

            result -= load_active_archived_uids(self.db_path)
        except Exception:
            pass
        return result

    def _load_full_mode_uids_from_progress(self):
        try:
            from .cache import CacheStore

            cache_store = CacheStore(self.config)
            followings = cache_store.load_followings_cache()
            progress = cache_store.load_progress()
        except Exception:
            return set()
        if not isinstance(progress, dict) or not progress:
            return set()

        active_uids = {
            str((user or {}).get("sec_uid") or "").strip()
            for user in (followings or [])
            if isinstance(user, dict) and str((user or {}).get("sec_uid") or "").strip()
        }
        result = set()
        for uid, entry in progress.items():
            uid = str(uid or "").strip()
            if not uid or (active_uids and uid not in active_uids):
                continue
            if CacheStore._entry_has_full_cache(entry):
                result.add(uid)
        try:
            from .archive import load_active_archived_uids

            result -= load_active_archived_uids(self.db_path)
        except Exception:
            pass
        return result

    def _score_creator(self, creator, video_stat, manual_grade, follower_p95, total_like_p95, avg_like_p95):
        follower_count = _safe_int(creator.get("follower_count"), 0)
        total_like_count = _safe_int(creator.get("total_like_count"), 0)
        avg_like_count = _safe_float(creator.get("avg_like_count"), 0)
        latest_publish_ts = _safe_int(video_stat.get("latest_publish_ts"), 0)
        inactive_days_from_videos = _inactive_days_from_video_stats(video_stat, self.now_ts)
        inactive_days = inactive_days_from_videos if inactive_days_from_videos is not None else creator.get("inactive_days")
        avg_update_days = creator.get("avg_update_days")
        video_count = max(_safe_int(creator.get("video_count"), 0), _safe_int(len(video_stat.get("videos", [])), 0))
        earliest_publish_ts = _safe_int(video_stat.get("earliest_publish_ts"), 0)
        creator_span_days = max((self.now_ts - earliest_publish_ts) / 86400, 0) if earliest_publish_ts > 0 else None

        follower_score = _log_p95_score(follower_count, follower_p95)
        total_like_score = _log_p95_score(total_like_count, total_like_p95)
        recent_update_score = _recent_update_score(inactive_days)
        update_frequency_score = _update_frequency_score(avg_update_days)
        video_count_score = _video_count_score(video_count)
        history_span_score = _history_span_score(creator_span_days, inactive_days)
        avg_like_score = _log_p95_score(avg_like_count, avg_like_p95)
        video_grade_score = _video_grade_score(video_stat)
        low_grade_ratio = _low_grade_ratio(video_stat)
        low_grade_ratio_score = _low_grade_ratio_score(low_grade_ratio)
        recent_trend_score = _recent_trend_score(video_stat)

        metric_scores = {
            "follower": follower_score,
            "total_like": total_like_score,
            "recent_update": recent_update_score,
            "update_frequency": update_frequency_score,
            "video_count": video_count_score,
            "history_span": history_span_score,
            "avg_like": avg_like_score,
            "video_grade": video_grade_score,
            "low_grade_ratio": low_grade_ratio_score,
            "recent_trend": recent_trend_score,
        }
        risk_penalty = _risk_penalty(low_grade_ratio, inactive_days, video_count, len(video_stat.get("scores") or []))
        auto_score = _clamp(_weighted_score(metric_scores) - risk_penalty)
        auto_grade = _auto_grade_from_score(auto_score)
        final_score = auto_score
        final_grade = auto_grade
        score_source = "auto"
        reasons = _score_reasons(metric_scores, low_grade_ratio, video_count, inactive_days, risk_penalty)

        if manual_grade:
            final_grade = manual_grade
            final_score = GRADE_SCORES.get(manual_grade, auto_score)
            score_source = "manual_creator_grade" if manual_grade != "S" else "manual_s_creator"
            reasons.insert(0, f"UP手动等级={manual_grade}")

        grade_counts = video_stat.get("grade_counts") or {}
        scored_video_count = len(video_stat.get("scores") or [])
        recent_video_count = min(len(video_stat.get("videos") or []), 10)
        missing_metrics = []
        if scored_video_count <= 0:
            missing_metrics.append("视频等级分布")
        if earliest_publish_ts <= 0:
            missing_metrics.append("最早视频时间")
        if avg_update_days is None:
            missing_metrics.append("平均几天一更")
        if inactive_days is None:
            missing_metrics.append("最近更新时间")

        return {
            "uploader_name": creator.get("uploader_name", ""),
            "uploader_id": creator.get("uploader_id", ""),
            "homepage_url": creator.get("homepage_url", ""),
            "manual_grade": manual_grade,
            "auto_score": round(auto_score, 2),
            "auto_grade": auto_grade,
            "final_score": round(final_score, 2),
            "final_grade": final_grade,
            "confidence": _confidence_label(video_count, scored_video_count, missing_metrics),
            "score_source": score_source,
            "follower_count": follower_count,
            "total_like_count": total_like_count,
            "latest_publish_date": _format_unix_timestamp(latest_publish_ts) or creator.get("latest_publish_date"),
            "inactive_days": "" if inactive_days is None else round(inactive_days, 2),
            "avg_update_days": "" if avg_update_days is None else round(avg_update_days, 2),
            "video_count": video_count,
            "earliest_publish_date": _format_unix_timestamp(earliest_publish_ts),
            "creator_span_days": "" if creator_span_days is None else round(creator_span_days, 2),
            "avg_like_count": round(avg_like_count, 2),
            "video_grade_score": _round_or_empty(video_grade_score),
            "low_grade_ratio": round(low_grade_ratio, 4),
            "recent_trend_score": _round_or_empty(recent_trend_score),
            "risk_penalty": round(risk_penalty, 2),
            "follower_score": round(follower_score, 2),
            "total_like_score": round(total_like_score, 2),
            "recent_update_score": _round_or_empty(recent_update_score),
            "update_frequency_score": _round_or_empty(update_frequency_score),
            "video_count_score": round(video_count_score, 2),
            "history_span_score": _round_or_empty(history_span_score),
            "avg_like_score": round(avg_like_score, 2),
            "low_grade_ratio_score": round(low_grade_ratio_score, 2),
            "scored_video_count": scored_video_count,
            "recent_video_count": recent_video_count,
            "grade_s_count": grade_counts.get("S", 0),
            "grade_a_count": grade_counts.get("A", 0),
            "grade_b_count": grade_counts.get("B", 0),
            "grade_c_count": grade_counts.get("C", 0),
            "grade_d_count": grade_counts.get("D", 0),
            "score_reasons": "；".join(reasons),
            "missing_metrics": "，".join(missing_metrics),
        }

    def _read_table(self, table_name):
        if not self._table_exists(table_name):
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(f'SELECT * FROM "{table_name}"').fetchall()]

    def _table_exists(self, table_name):
        if not self.db_path.exists():
            return False
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()
        return row is not None


def _weighted_score(metric_scores):
    total_weight = 0
    score = 0
    for key, weight in CREATOR_WEIGHTS.items():
        value = metric_scores.get(key)
        if value is None:
            continue
        total_weight += weight
        score += _clamp(value) * weight
    if total_weight <= 0:
        return 0
    return _clamp(score / total_weight)


def _auto_grade_from_score(score):
    score = _safe_float(score, 0)
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def _log_p95_score(value, p95):
    value = max(_safe_float(value, 0), 0)
    p95 = max(_safe_float(p95, 0), 0)
    if value <= 0 or p95 <= 0:
        return 0
    return _clamp(math.log1p(value) / math.log1p(p95) * 100)


def _recent_update_score(inactive_days):
    if inactive_days is None:
        return None
    days = _safe_float(inactive_days, 999999)
    if days <= 7:
        return 100
    if days <= 30:
        return 85
    if days <= 90:
        return 65
    if days <= 180:
        return 40
    if days <= 365:
        return 20
    return 5


def _update_frequency_score(avg_update_days):
    if avg_update_days is None:
        return None
    days = _safe_float(avg_update_days, 999999)
    if days <= 1:
        return 75
    if days <= 7:
        return 100
    if days <= 15:
        return 85
    if days <= 30:
        return 65
    if days <= 90:
        return 40
    return 15


def _video_count_score(video_count):
    count = _safe_int(video_count, 0)
    if count <= 5:
        return 20
    if count <= 20:
        return 45
    if count <= 50:
        return 65
    if count <= 100:
        return 80
    if count <= 300:
        return 100
    return 90


def _history_span_score(span_days, inactive_days):
    if span_days is None:
        return None
    days = _safe_float(span_days, 0)
    inactive = _safe_float(inactive_days, 999999) if inactive_days is not None else 999999
    if days <= 30:
        return 40
    if days <= 90:
        return 55
    if days <= 365:
        return 70
    if days <= 365 * 3:
        return 90
    return 100 if inactive <= 90 else 50


def _video_grade_score(video_stat):
    grade_counts = video_stat.get("grade_counts") or {}
    total = sum(_safe_int(grade_counts.get(grade), 0) for grade in VIDEO_GRADE_VALUES)
    if total <= 0:
        return None
    score = 0
    for grade, value in VIDEO_GRADE_VALUES.items():
        score += _safe_int(grade_counts.get(grade), 0) / total * value
    return _clamp(score)


def _low_grade_ratio(video_stat):
    grade_counts = video_stat.get("grade_counts") or {}
    total = sum(_safe_int(grade_counts.get(grade), 0) for grade in VIDEO_GRADE_VALUES)
    if total <= 0:
        return 0
    return (_safe_int(grade_counts.get("C"), 0) + _safe_int(grade_counts.get("D"), 0)) / total


def _low_grade_ratio_score(ratio):
    ratio = _safe_float(ratio, 0)
    if ratio <= 0.10:
        return 100
    if ratio <= 0.25:
        return 80
    if ratio <= 0.40:
        return 60
    if ratio <= 0.60:
        return 35
    return 15


def _recent_trend_score(video_stat):
    scores = [_safe_float(value, 0) for value in (video_stat.get("scores") or []) if value is not None]
    videos = [video for video in (video_stat.get("videos") or []) if video.get("score") is not None]
    if not scores or not videos:
        return None
    history_avg = sum(scores) / len(scores)
    if history_avg <= 0:
        return 65
    recent_scores = [_safe_float(video.get("score"), 0) for video in videos[:10]]
    recent_avg = sum(recent_scores) / len(recent_scores)
    change_rate = (recent_avg - history_avg) / history_avg
    if change_rate >= 0.30:
        return 100
    if change_rate >= 0.10:
        return 85
    if change_rate >= -0.10:
        return 65
    if change_rate >= -0.30:
        return 40
    return 20


def _inactive_days_from_video_stats(video_stat, now_ts):
    latest_ts = _safe_int(video_stat.get("latest_publish_ts"), 0)
    if latest_ts <= 0:
        return None
    return max((now_ts - latest_ts) / 86400, 0)


def _confidence_label(video_count, scored_video_count, missing_metrics):
    count = max(_safe_int(video_count, 0), _safe_int(scored_video_count, 0))
    if count < 5:
        base = "低"
    elif count <= 20:
        base = "中"
    else:
        base = "高"
    if missing_metrics:
        if base == "高":
            return "中"
        return "低"
    return base


def _risk_penalty(low_grade_ratio, inactive_days, video_count, scored_video_count):
    penalty = 0
    if low_grade_ratio >= 0.60:
        penalty += 10
    elif low_grade_ratio >= 0.40:
        penalty += 5
    if inactive_days is not None:
        days = _safe_float(inactive_days, 0)
        if days > 365:
            penalty += 10
        elif days > 180:
            penalty += 5
    if _safe_int(video_count, 0) <= 5:
        penalty += 5
    if _safe_int(scored_video_count, 0) <= 0:
        penalty += 8
    return penalty


def _score_reasons(metric_scores, low_grade_ratio, video_count, inactive_days, risk_penalty):
    reasons = []
    if _safe_float(metric_scores.get("follower"), 0) >= 90:
        reasons.append("粉丝规模较高")
    if _safe_float(metric_scores.get("total_like"), 0) >= 90:
        reasons.append("历史获赞积累较强")
    if _safe_float(metric_scores.get("video_grade"), 0) >= 80:
        reasons.append("视频等级分布优秀")
    if _safe_float(metric_scores.get("recent_update"), 0) >= 85:
        reasons.append("近期仍保持更新")
    if _safe_float(metric_scores.get("update_frequency"), 0) >= 85:
        reasons.append("更新节奏稳定")
    if _safe_float(metric_scores.get("recent_trend"), 0) >= 85:
        reasons.append("最近10条表现上升")
    if low_grade_ratio >= 0.40:
        reasons.append("C/D低等级视频比例偏高")
    if inactive_days is not None and _safe_float(inactive_days, 0) > 180:
        reasons.append("停更时间较长")
    if _safe_int(video_count, 0) < 5:
        reasons.append("视频数量较少，评级需观察")
    if risk_penalty > 0:
        reasons.append(f"风险扣分{risk_penalty:g}")
    return reasons


def _p95(values):
    cleaned = sorted(max(_safe_float(value, 0), 0) for value in values if _safe_float(value, 0) > 0)
    if not cleaned:
        return 0
    index = int((len(cleaned) - 1) * 0.95)
    return cleaned[index]


def _pick(row, *names):
    for name in names:
        if name in row:
            return row.get(name)
    return None


def _pick_text(row, *names):
    value = _pick(row, *names)
    return str(value or "").strip()


def _is_yes_value(value):
    text = str(value or "").strip().lower()
    return text in {"是", "yes", "true", "1", "y"}


def _clamp(value, low=0, high=100):
    return max(low, min(high, _safe_float(value, 0)))


def _round_or_empty(value, digits=2):
    if value is None:
        return ""
    return round(_safe_float(value, 0), digits)
