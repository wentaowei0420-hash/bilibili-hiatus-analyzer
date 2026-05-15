import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from common.domain_models import AnalysisResult, CreatorProfile, DataSource, VideoDurationSummary
from common.output_ports import NoopExportService
from common.repositories import AnalyzerCacheRepository
from common.runtime_control import OperationCancelled, check_stop
from .console_reporter import RichAnalyzerReporter
from .export_service import BilibiliExportService
from .http_client import RateLimitExceededError
from .utils import (
    DEFAULT_GROUP_NAME,
    LONG_VIDEO_LABEL,
    MEDIUM_LONG_VIDEO_LABEL,
    MEDIUM_VIDEO_LABEL,
    SHORT_VIDEO_LABEL,
    UNKNOWN_DATE,
    build_homepage_url,
    calculate_average_update_interval_days,
    calculate_days_since,
    format_ratio,
    normalize_timestamp,
    seconds_to_duration_text,
    timestamp_to_date,
)


class BilibiliHiatusAnalyzer:
    def __init__(
        self,
        config,
        api,
        cache_store,
        max_followings=None,
        reporter=None,
        export_service=None,
        export_outputs=True,
    ):
        self.config = config
        self.api = api
        self.cache_repository = AnalyzerCacheRepository(cache_store, platform="bilibili")
        self.reporter = reporter or RichAnalyzerReporter()
        self.export_service = export_service or (
            BilibiliExportService(config) if export_outputs else NoopExportService()
        )
        try:
            self.max_followings = int(max_followings) if max_followings is not None else None
        except (TypeError, ValueError):
            self.max_followings = None
        if self.max_followings is not None and self.max_followings <= 0:
            self.max_followings = None

    FETCH_ORDER_LABELS = {
        "follower_count": "\u7c89\u4e1d\u6570",
        "published_video_count": "\u89c6\u9891\u603b\u6570",
        "average_like_count": "\u5e73\u5747\u70b9\u8d5e\u6570",
    }

    @staticmethod
    def _safe_int(value, default=0):
        try:
            if value in (None, ""):
                return default
            return int(float(str(value).replace(",", "").strip()))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _env_flag(name, default=True):
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def get_following_fetch_order(cls):
        field = str(os.getenv("BILIBILI_FETCH_ORDER_BY", "follower_count") or "follower_count").strip()
        if field not in cls.FETCH_ORDER_LABELS:
            field = "follower_count"
        descending = cls._env_flag("BILIBILI_FETCH_ORDER_DESC", True)
        return field, descending, cls.FETCH_ORDER_LABELS[field]

    def _build_following_metric_index(self):
        return self.cache_repository.get_creator_metric_index()

    def _check_stop(self, on_cancel=None, *, reraise=True):
        try:
            check_stop()
        except OperationCancelled:
            if on_cancel:
                on_cancel()
            if reraise:
                raise
            return False
        return True

    def _resolve_following_sort_value(self, following, field, metric_index):
        profile = CreatorProfile.from_mapping(following if isinstance(following, dict) else {}, platform="bilibili")
        mid = str(profile.uploader_id or "").strip()
        cached = metric_index.get(mid) if mid else None
        if field == "published_video_count":
            return cached.published_video_count if cached else 0
        if field == "average_like_count":
            return getattr(cached, "average_like_count", 0) if cached else 0
        return profile.follower_count or (cached.follower_count if cached else 0)

    def sort_followings_by_follower_count(self, followings):
        field, descending, _label = self.get_following_fetch_order()
        metric_index = self._build_following_metric_index()

        def sort_key(following):
            profile = CreatorProfile.from_mapping(following if isinstance(following, dict) else {}, platform="bilibili")
            value = self._resolve_following_sort_value(following, field, metric_index)
            primary = -value if descending else value
            return (primary, profile.uploader_name, profile.uploader_id)

        return sorted(followings or [], key=sort_key)

    def build_result_item(self, video_info):
        days_since = calculate_days_since(video_info["upload_timestamp"])
        return AnalysisResult(
            platform="bilibili",
            uploader_id=str(video_info["uploader_id"]),
            uploader_name=str(video_info["uploader_name"]),
            uploader_homepage=build_homepage_url(video_info["uploader_id"]),
            days_since_update=days_since,
            upload_date=timestamp_to_date(video_info["upload_timestamp"]),
            following_group_ids="",
            following_group_names="",
            latest_video_title=video_info["video_title"],
            upload_timestamp=normalize_timestamp(video_info["upload_timestamp"]),
            days_since_last_video=days_since,
            view_count=video_info["view_count"],
            video_url=video_info.get("video_url")
            or f"https://www.bilibili.com/video/{video_info['bvid']}",
            data_source=DataSource.VIDEO_API.value,
        )

    def build_following_result_item(self, following):
        activity_timestamp = following.get("mtime") or 0
        days_since = calculate_days_since(activity_timestamp)
        return AnalysisResult(
            platform="bilibili",
            uploader_id=str(following.get("mid") or ""),
            uploader_name=str(following.get("uname", "未知UP主")),
            uploader_homepage=build_homepage_url(following.get("mid")),
            follower_count=self._safe_int(following.get("follower_count"), 0),
            upload_date=timestamp_to_date(activity_timestamp),
            days_since_update=days_since,
            following_group_ids=following.get("group_id_text", ""),
            following_group_names=following.get("group_name_text", DEFAULT_GROUP_NAME),
            latest_video_title="未抓取视频详情（回退模式，基于关注列表活跃时间）",
            activity_timestamp=normalize_timestamp(activity_timestamp),
            days_since_last_video=days_since,
            view_count=0,
            video_url="",
            data_source=DataSource.FOLLOWINGS_MTIME.value,
        )

    def build_no_video_result_item(self, following):
        return AnalysisResult(
            platform="bilibili",
            uploader_id=str(following.get("mid") or ""),
            uploader_name=str(following.get("uname", "未知UP主")),
            uploader_homepage=build_homepage_url(following.get("mid")),
            follower_count=self._safe_int(following.get("follower_count"), 0),
            upload_date=UNKNOWN_DATE,
            days_since_update=0,
            following_group_ids=following.get("group_id_text", ""),
            following_group_names=following.get("group_name_text", DEFAULT_GROUP_NAME),
            latest_video_title="暂无公开视频",
            days_since_last_video=0,
            view_count=0,
            video_url="",
            data_source=DataSource.NO_VIDEO.value,
        )

    def build_video_duration_summary(self, following, videos):
        return VideoDurationSummary.from_video_records(
            following,
            videos,
            platform="bilibili",
            short_label=SHORT_VIDEO_LABEL,
            medium_label=MEDIUM_VIDEO_LABEL,
            medium_long_label=MEDIUM_LONG_VIDEO_LABEL,
            long_label=LONG_VIDEO_LABEL,
            average_update_interval_fn=calculate_average_update_interval_days,
            duration_text_fn=seconds_to_duration_text,
            ratio_fn=format_ratio,
            timestamp_normalizer=normalize_timestamp,
        ).to_dict()

    @staticmethod
    def _format_output_summary(paths):
        return "、".join(path.name for path in paths if path)

    def populate_duration_summary_defaults(self, summary, videos):
        completed_summary = dict(summary or {})
        defaults = self.build_video_duration_summary({}, videos or [])
        for key in (
            "total_videos",
            "average_update_interval_days",
            "average_like_count",
            "missing_like_count",
            "like_data_complete",
        ):
            completed_summary.setdefault(key, defaults.get(key))
        return completed_summary

    @staticmethod
    def count_missing_like_videos(videos):
        return sum(
            1 for video in (videos or []) if video.get("bvid") and not video.get("like_count_fetched", False)
        )

    def enrich_video_like_counts_with_budget(self, videos, uname, remaining_budget):
        if not self.config.enable_real_video_like_fetch or remaining_budget <= 0:
            return remaining_budget, False

        max_videos = min(self.config.video_stat_recent_limit, remaining_budget)
        stats = self.api.enrich_videos_with_detail_stats(videos, uname, max_videos=max_videos)
        remaining_budget = max(0, remaining_budget - stats.get("attempted", 0))
        return remaining_budget, stats.get("rate_limit_hit", False)

    def enrich_cached_video_like_counts(self, followings, duration_progress, remaining_budget):
        if (
            not self.config.enable_real_video_like_fetch
            or not self.config.enable_cached_video_like_backfill
            or not duration_progress
            or remaining_budget <= 0
        ):
            return duration_progress, remaining_budget, False

        following_map = {str(following.get("mid")): following for following in followings}
        refreshed_count = 0
        rate_limit_hit = False
        pending_entries = []
        for mid, entry in self.cache_repository.iter_duration_progress_entries(duration_progress):
            videos, _summary = self.cache_repository.duration_progress_payload(entry)
            if videos and self.count_missing_like_videos(videos) > 0:
                pending_entries.append((mid, entry))

        if pending_entries:
            self.reporter.message(f"👍 准备为 {len(pending_entries)} 位UP主补抓历史点赞数据...")

        with self.reporter.progress() as progress:
            task_id = progress.add_task("补抓历史点赞", total=len(pending_entries))
            for mid, entry in pending_entries:
                progress.advance(task_id)
                if remaining_budget <= 0:
                    break

                videos, _summary = self.cache_repository.duration_progress_payload(entry)
                following = following_map.get(str(mid), {})
                uname = entry.get("uploader_name") or following.get("uname", "未知UP主")
                remaining_budget, rate_limit_hit = self.enrich_video_like_counts_with_budget(
                    videos,
                    uname,
                    remaining_budget,
                )
                entry["summary"] = self.build_video_duration_summary(following, videos)
                entry["cached_at"] = int(time.time())
                duration_progress[str(mid)] = entry
                self.cache_repository.save_duration_progress(duration_progress)
                refreshed_count += 1

                if rate_limit_hit:
                    break

        if refreshed_count:
            self.reporter.message(f"✅ 已补齐 {refreshed_count} 位UP主缓存中的部分真实点赞数据。")
        return duration_progress, remaining_budget, rate_limit_hit

    def enrich_results_with_profile_and_counts(self, results, duration_progress=None, followings=None):
        progress = duration_progress or {}
        following_map = {str(following.get("mid")): following for following in (followings or [])}

        enriched = []
        for item in results:
            result = item if isinstance(item, AnalysisResult) else AnalysisResult.from_mapping(item, platform="bilibili")
            uploader_id = result.uploader_id
            result.uploader_homepage = build_homepage_url(uploader_id)

            following = following_map.get(str(uploader_id), {})
            if following:
                result.following_group_ids = following.get("group_id_text", "0")
                result.following_group_names = following.get("group_name_text", DEFAULT_GROUP_NAME)
                result.follower_count = self._safe_int(
                    following.get("follower_count", result.follower_count),
                    result.follower_count,
                )
            else:
                result.following_group_ids = result.following_group_ids or "0"
                result.following_group_names = result.following_group_names or DEFAULT_GROUP_NAME

            entry = self.cache_repository.duration_progress_entry(progress, uploader_id)
            videos, cached_summary = self.cache_repository.duration_progress_payload(entry)
            summary = self.populate_duration_summary_defaults(cached_summary, videos)
            if summary:
                result.published_video_count = self._safe_int(
                    summary.get("total_videos", result.published_video_count),
                    result.published_video_count,
                )
                result.average_like_count = self._safe_int(
                    summary.get("average_like_count", result.average_like_count),
                    result.average_like_count,
                )
                result.average_update_interval_days = summary.get(
                    "average_update_interval_days",
                    result.average_update_interval_days,
                )
            enriched.append(result)

        return enriched

    def save_precise_result(self, mid, result_item, results_by_mid, cached_video_results):
        cache_row = result_item.to_dict() if hasattr(result_item, "to_dict") else dict(result_item)
        cache_row["cached_at"] = int(time.time())
        mid_str = str(mid)
        results_by_mid[mid_str] = result_item
        cached_video_results[mid_str] = cache_row
        self.cache_repository.save_precise_results(cached_video_results)

    def handle_precise_video_result(self, following, video_info, results_by_mid, cached_video_results):
        mid = following.get("mid")
        if video_info:
            result_item = self.build_result_item(video_info)
            result_item.following_group_ids = following.get("group_id_text", "")
            result_item.following_group_names = following.get("group_name_text", DEFAULT_GROUP_NAME)
            result_item.follower_count = self._safe_int(following.get("follower_count"), 0)
            self.save_precise_result(mid, result_item, results_by_mid, cached_video_results)
            return True

        if video_info is False:
            result_item = self.build_no_video_result_item(following)
            self.save_precise_result(mid, result_item, results_by_mid, cached_video_results)
            return True

        return False

    def run_precise_fetch_round(self, followings, label, results_by_mid, cached_video_results):
        remaining_followings = []
        success_count = 0
        no_video_count = 0
        error_count = 0

        def fetch_single(following, index):
            uname = following.get("uname", "未知UP主")
            try:
                time.sleep(random.uniform(0, 1.5))
                return following, self.api.get_latest_video(following.get("mid"), uname), None
            except RateLimitExceededError:
                self.reporter.message(f"   ⏭️  {uname} - 当前风控较严，先加入稍后重试队列")
                return following, None, "rate_limit"
            except Exception as exc:
                return following, None, exc

        with self.reporter.progress() as progress:
            task_id = progress.add_task(f"精确抓取 {label}", total=len(followings))
            with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
                futures = {
                    executor.submit(fetch_single, following, index + 1): following
                    for index, following in enumerate(followings)
                }

                for future in as_completed(futures):
                    following, video_info, error = future.result()
                    progress.advance(task_id)
                    if error == "rate_limit":
                        error_count += 1
                        remaining_followings.append(following)
                        continue
                    if error is not None:
                        self.reporter.message(f"   ❌ {following.get('uname')} - 抓取异常: {error}")
                        error_count += 1
                        remaining_followings.append(following)
                        continue
                    if video_info:
                        success_count += 1
                    elif video_info is False:
                        no_video_count += 1
                    else:
                        error_count += 1
                    if not self.handle_precise_video_result(
                        following, video_info, results_by_mid, cached_video_results
                    ):
                        remaining_followings.append(following)

        self.reporter.panel(
            f"📌 精确抓取批次 {label}",
            [
                f"成功抓取: {success_count}",
                f"无公开视频: {no_video_count}",
                f"待补抓: {len(remaining_followings)}",
                f"异常/失败: {error_count}",
            ],
            border_style="blue",
        )
        return remaining_followings

    def analyze_video_durations(self, followings):
        if not self.config.enable_video_duration_analysis:
            return {}

        self.reporter.message()
        self.reporter.message("=" * 60)
        self.reporter.message("📊 正在分析所有关注UP主的全部视频时长...")
        self.reporter.message("=" * 60)
        self.reporter.message(f"   当前视频时长分析并发数: {self.config.video_analysis_workers}")

        duration_progress = self.cache_repository.load_duration_progress()
        like_fetch_budget = self.config.video_stat_max_requests_per_run
        like_rate_limited = False
        like_budget_exhausted_notified = False

        if duration_progress:
            self.reporter.message(f"♻️  已加载 {len(duration_progress)} 条视频时长分析缓存。")
            if self.config.enable_cached_video_like_backfill:
                duration_progress, like_fetch_budget, like_rate_limited = self.enrich_cached_video_like_counts(
                    followings,
                    duration_progress,
                    like_fetch_budget,
                )
            elif self.config.enable_real_video_like_fetch:
                self.reporter.message("⏭️  已跳过历史缓存视频的自动点赞补抓，避免本轮一开始触发风控。")

        pending_followings = [
            following
            for following in followings
            if self.cache_repository.should_refresh_duration_result(
                following,
                self.cache_repository.duration_progress_entry(duration_progress, following.get("mid")),
            )
        ]
        failed_followings = []

        def process_duration(following, index):
            uname = following.get("uname", "未知UP主")
            try:
                time.sleep(random.uniform(0, 1.5))
                return following, self.api.get_all_videos_for_up(following.get("mid"), uname), None
            except RateLimitExceededError:
                self.reporter.message(f"   ⏭️  {uname} - 当前风控较严，先加入稍后重试队列")
                return following, None, "rate_limit"
            except requests.exceptions.RequestException as exc:
                self.reporter.message(f"   ⚠️  {uname} - 网络异常: {exc.__class__.__name__}，先加入稍后重试队列")
                return following, None, "network"
            except Exception as exc:
                return following, None, exc

        with self.reporter.progress() as progress:
            task_id = progress.add_task("全量视频时长分析", total=len(pending_followings))
            for start in range(0, len(pending_followings), self.config.video_analysis_batch_size):
                self._check_stop()
                batch = pending_followings[start:start + self.config.video_analysis_batch_size]
                with ThreadPoolExecutor(max_workers=self.config.video_analysis_workers) as executor:
                    futures = {
                        executor.submit(process_duration, following, start + index + 1): following
                        for index, following in enumerate(batch)
                    }

                    for future in as_completed(futures):
                        following, videos, error = future.result()
                        progress.advance(task_id)
                        uname = following.get("uname", "未知UP主")
                        if error or videos is None:
                            failed_followings.append(following)
                            continue

                        if self.config.enable_real_video_like_fetch and like_fetch_budget > 0 and not like_rate_limited:
                            like_fetch_budget, like_rate_limited = self.enrich_video_like_counts_with_budget(
                                videos,
                                uname,
                                like_fetch_budget,
                            )
                            if like_rate_limited:
                                self.reporter.message("⚠️  本轮真实点赞补抓已暂停，剩余视频先保留到后续运行再补抓。")
                        elif (
                            self.config.enable_real_video_like_fetch
                            and like_fetch_budget <= 0
                            and not like_budget_exhausted_notified
                        ):
                            self.reporter.message("⏭️  本轮真实点赞补抓预算已用完，剩余视频跳过点赞补抓。")
                            like_budget_exhausted_notified = True

                        summary = self.build_video_duration_summary(following, videos)
                        duration_progress[str(following.get("mid"))] = (
                            self.cache_repository.build_duration_progress_entry(
                                following,
                                videos,
                                summary,
                            )
                        )
                        self.cache_repository.save_duration_progress(duration_progress)

                if not self._check_stop(
                    lambda: self.cache_repository.save_duration_progress(duration_progress),
                    reraise=False,
                ):
                    break

                if start + self.config.video_analysis_batch_size < len(pending_followings):
                    cooldown = self.config.video_analysis_batch_cooldown + random.uniform(0, 5)
                    self.reporter.message(
                        f"⏸️  视频分析已完成 {start + len(batch)} 位UP主，"
                        f"批次冷却 {cooldown:.0f} 秒后继续..."
                    )
                    self.reporter.wait(cooldown, "B站视频分析批次冷却中")

        all_video_rows = []
        summary_rows = []
        for following in followings:
            self._check_stop()
            entry = self.cache_repository.duration_progress_entry(duration_progress, following.get("mid"))
            if not entry:
                continue
            videos, summary = self.cache_repository.duration_progress_payload(entry)
            all_video_rows.extend(videos)
            if summary:
                summary_rows.append(summary)

        self.export_service.save_video_duration_outputs(all_video_rows, summary_rows)

        self.reporter.panel(
            "🗂️ B站视频分析输出",
            [
                f"输出文件: {self._format_output_summary([self.config.all_videos_csv, self.config.video_duration_analysis_csv, self.config.video_duration_report_md])}",
                f"视频明细: {len(all_video_rows)} 条",
                f"UP 汇总: {len(summary_rows)} 位",
                f"待下轮补抓: {len(failed_followings)} 位" if failed_followings else "待下轮补抓: 0 位",
            ],
            border_style="magenta",
        )
        return duration_progress

    def display_top_results(self, results):
        table = self.reporter.create_table(
            "🏆 B站鸽王排行榜 Top 10",
            [
                ("排名", "right", "bold"),
                ("UP主", "left"),
                ("已鸽天数", "right"),
                ("粉丝数", "right"),
                ("视频数", "right"),
                ("平均点赞", "right"),
                ("平均几天一更", "right"),
                ("最新发布日期", "left"),
            ],
        )

        for index, result in enumerate(results[:10], 1):
            result_model = result if isinstance(result, AnalysisResult) else AnalysisResult.from_mapping(result, platform="bilibili")
            average_update_interval_days = result_model.average_update_interval_days
            average_update_text = (
                f"{average_update_interval_days:.2f}"
                if isinstance(average_update_interval_days, (int, float))
                else "暂无"
            )
            table.add_row(
                str(index),
                result_model.uploader_name,
                str(result_model.days_since_update),
                f"{result_model.follower_count:,}",
                str(result_model.published_video_count),
                str(result_model.average_like_count),
                average_update_text,
                result_model.upload_date or UNKNOWN_DATE,
            )

        self.reporter.render()
        self.reporter.render(table)
        self.reporter.render()

    def _announce_analysis_start(self):
        self.reporter.message("=" * 60)
        self.reporter.message("🎯 B站催更分析器 - 寻找你关注的UP主中的「鸽王」")
        self.reporter.message("=" * 60)
        self.reporter.message()

    def _fetch_and_prepare_followings(self):
        followings = self.api.get_followings_list()
        if not followings:
            self.reporter.message("❌ 无法获取关注列表，程序退出")
            return [], False

        for following in followings:
            self._check_stop()
            relation_stat = self.api.get_uploader_relation_stat(
                following.get("mid"),
                following.get("uname", "UP主"),
            )
            following["follower_count"] = relation_stat.get("follower_count", 0)

        order_field, order_desc, order_label = self.get_following_fetch_order()
        followings = self.sort_followings_by_follower_count(followings)
        total_followings = len(followings)
        partial_run = self.max_followings is not None and self.max_followings < total_followings
        if partial_run:
            followings = followings[: self.max_followings]
            self.reporter.message(
                f"🧩 部分抓取模式已生效 | 全部关注={total_followings} 位 | "
                f"本轮仅处理排序靠前的 {len(followings)} 位"
            )
        direction_label = "从高到低" if order_desc else "从低到高"
        self.reporter.message(f"📊 已按{order_label}{direction_label}排序后开始抓取。")
        return followings, partial_run

    def _split_cached_and_pending(self, followings):
        cached_video_results = self.cache_repository.load_precise_results()
        if cached_video_results:
            self.reporter.message(f"♻️  已加载 {len(cached_video_results)} 条历史抓取缓存。")

        results_by_mid = {}
        pending_followings = []
        for following in followings:
            mid = str(following.get("mid"))
            cached_result = cached_video_results.get(mid)
            if cached_result and not self.cache_repository.should_refresh_precise_result(following, cached_result):
                refreshed_result = dict(cached_result)
                self.cache_repository.refresh_result_runtime_fields(refreshed_result)
                results_by_mid[mid] = AnalysisResult.from_mapping(refreshed_result, platform="bilibili")
            else:
                pending_followings.append(following)
        return results_by_mid, pending_followings, cached_video_results

    def _execute_precise_fetch_with_retry(
        self,
        pending_followings,
        results_by_mid,
        cached_video_results,
        followings,
    ):
        self.reporter.message("🔍 正在精确抓取每位UP主最后一个视频时间...")
        if self.config.analysis_mode == "fallback":
            self.reporter.message("   如遇到无法补抓的UP主，将回退到关注列表活跃时间。")
        else:
            self.reporter.message("   当前为精确模式：仅接受视频动态时间作为最终结果。")
        self.reporter.message()

        failed_followings = []
        if pending_followings:
            self.reporter.message(f"🎬 仍有 {len(pending_followings)} 位UP主需要精确抓取。")
            self.reporter.message(
                f"⏸️  先冷却 {self.config.video_analysis_start_delay} 秒，"
                "降低进入视频动态接口时立刻触发风控的概率..."
            )
            self.reporter.wait(
                self.config.video_analysis_start_delay,
                "B站视频分析启动冷却中",
            )

            for start in range(0, len(pending_followings), self.config.batch_size):
                batch = pending_followings[start:start + self.config.batch_size]
                batch_label = f"{start + 1}-{start + len(batch)}"
                failed_followings.extend(
                    self.run_precise_fetch_round(
                        batch,
                        batch_label,
                        results_by_mid,
                        cached_video_results,
                    )
                )
                self._check_stop(lambda: self._save_partial_results(results_by_mid, followings))
                if start + self.config.batch_size < len(pending_followings):
                    cooldown = self.config.batch_cooldown + random.uniform(0, 5)
                    self.reporter.message(
                        f"⏸️  已完成 {start + len(batch)} 位UP主，"
                        f"批次冷却 {cooldown:.0f} 秒后继续..."
                    )
                    self.reporter.wait(cooldown, "B站精确抓取批次冷却中")

        return self._retry_failed_precise_fetches(
            failed_followings,
            results_by_mid,
            cached_video_results,
        )

    def _retry_failed_precise_fetches(self, failed_followings, results_by_mid, cached_video_results):
        for retry_round in range(1, self.config.max_failed_retry_rounds + 1):
            if not failed_followings:
                break

            cooldown = self.config.failed_retry_cooldown * retry_round + random.uniform(0, 10)
            self.reporter.message()
            self.reporter.message(f"🔁  第 {retry_round} 轮补抓开始，先冷却 {cooldown:.0f} 秒...")
            self.reporter.wait(cooldown, f"B站补抓第 {retry_round} 轮冷却中")
            failed_followings = self.run_precise_fetch_round(
                failed_followings,
                f"补抓第{retry_round}轮",
                results_by_mid,
                cached_video_results,
            )
        return failed_followings

    def _apply_fallback_results(self, failed_followings, results_by_mid):
        if self.config.analysis_mode != "fallback" or not failed_followings:
            return
        self.reporter.message(f"\n↩️  仍有 {len(failed_followings)} 位UP主未完成精确抓取，回退到关注列表活跃时间。")
        for following in failed_followings:
            mid = str(following.get("mid"))
            if mid not in results_by_mid:
                results_by_mid[mid] = self.build_following_result_item(following)

    def _save_partial_results(self, results_by_mid, followings):
        duration_progress = self.cache_repository.load_duration_progress()
        results = self.enrich_results_with_profile_and_counts(
            list(results_by_mid.values()),
            duration_progress,
            followings,
        )
        if results:
            results.sort(key=lambda item: item.days_since_update, reverse=True)
            self.export_service.save_main_results(results)

    def _enrich_sort_and_export_results(self, raw_results, followings, failed_followings, partial_run):
        duration_progress = self.cache_repository.load_duration_progress()
        results = self.enrich_results_with_profile_and_counts(raw_results, duration_progress, followings)
        if not results:
            self.reporter.message("\n❌ 未能获取到任何视频数据")
            return None

        if self.config.analysis_mode == "precise" and failed_followings:
            self.reporter.message(f"\n⚠️  仍有 {len(failed_followings)} 位UP主因频率限制未获取成功。")
            self.reporter.message("   下次运行会自动复用已保存进度，继续补抓剩余UP主。")

        results.sort(key=lambda item: item.days_since_update, reverse=True)
        self.display_top_results(results)
        self.export_service.save_main_results(results, merge_existing=partial_run)
        self.reporter.panel(
            "🗂️ B站主榜输出",
            [
                f"文件: {self.config.output_csv.name}",
                f"结果数: {len(results)} 位UP主",
                f"未完成精确抓取: {len(failed_followings)} 位" if failed_followings else "未完成精确抓取: 0 位",
            ],
            border_style="green",
        )
        return results

    def analyze_hiatus(self):
        self.api.check_cookie()
        self._announce_analysis_start()

        followings, partial_run = self._fetch_and_prepare_followings()
        if not followings:
            return None

        results_by_mid, pending_followings, cached_video_results = self._split_cached_and_pending(followings)
        failed_followings = self._execute_precise_fetch_with_retry(
            pending_followings,
            results_by_mid,
            cached_video_results,
            followings,
        )
        self._apply_fallback_results(failed_followings, results_by_mid)

        results = self._enrich_sort_and_export_results(
            list(results_by_mid.values()),
            followings,
            failed_followings,
            partial_run,
        )
        if not results:
            return None

        duration_progress = self.analyze_video_durations(followings)
        if duration_progress:
            self.enrich_results_with_profile_and_counts(results, duration_progress, followings)
            self.export_service.save_main_results(results, merge_existing=partial_run)
            self.reporter.panel(
                "🔄 B站主榜回填完成",
                [
                    f"已回填文件: {self.config.output_csv.name}",
                    f"视频分析缓存: {len(duration_progress)} 条",
                ],
                border_style="yellow",
            )

        return results



