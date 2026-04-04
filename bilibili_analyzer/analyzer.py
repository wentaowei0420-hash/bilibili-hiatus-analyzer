import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .exporters import (
    save_all_videos_to_csv,
    save_to_csv,
    save_video_duration_analysis_to_csv,
    save_video_duration_report,
)
from .http_client import RateLimitExceededError
from .logging_utils import create_progress, create_table, get_console, smart_print as print
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
    def __init__(self, config, api, cache_store):
        self.config = config
        self.api = api
        self.cache_store = cache_store

    def build_result_item(self, video_info):
        days_since = calculate_days_since(video_info["upload_timestamp"])
        return {
            "uploader_name": video_info["uploader_name"],
            "uploader_id": video_info["uploader_id"],
            "uploader_homepage": build_homepage_url(video_info["uploader_id"]),
            "following_group_ids": "",
            "following_group_names": "",
            "follower_count": 0,
            "published_video_count": 0,
            "average_like_count": 0,
            "average_update_interval_days": None,
            "latest_video_title": video_info["video_title"],
            "upload_timestamp": normalize_timestamp(video_info["upload_timestamp"]),
            "upload_date": timestamp_to_date(video_info["upload_timestamp"]),
            "days_since_update": days_since,
            "days_since_last_video": days_since,
            "view_count": video_info["view_count"],
            "video_url": video_info.get("video_url")
            or f"https://www.bilibili.com/video/{video_info['bvid']}",
            "data_source": "video_api",
        }

    def build_following_result_item(self, following):
        activity_timestamp = following.get("mtime") or 0
        return {
            "uploader_name": following.get("uname", "未知UP主"),
            "uploader_id": following.get("mid"),
            "uploader_homepage": build_homepage_url(following.get("mid")),
            "following_group_ids": following.get("group_id_text", ""),
            "following_group_names": following.get("group_name_text", DEFAULT_GROUP_NAME),
            "follower_count": following.get("follower_count", 0),
            "published_video_count": 0,
            "average_like_count": 0,
            "average_update_interval_days": None,
            "latest_video_title": "未抓取视频详情（回退模式，基于关注列表活跃时间）",
            "activity_timestamp": normalize_timestamp(activity_timestamp),
            "upload_date": timestamp_to_date(activity_timestamp),
            "days_since_update": calculate_days_since(activity_timestamp),
            "days_since_last_video": calculate_days_since(activity_timestamp),
            "view_count": 0,
            "video_url": "",
            "data_source": "followings_mtime",
        }

    def build_no_video_result_item(self, following):
        return {
            "uploader_name": following.get("uname", "未知UP主"),
            "uploader_id": following.get("mid"),
            "uploader_homepage": build_homepage_url(following.get("mid")),
            "following_group_ids": following.get("group_id_text", ""),
            "following_group_names": following.get("group_name_text", DEFAULT_GROUP_NAME),
            "follower_count": following.get("follower_count", 0),
            "published_video_count": 0,
            "average_like_count": 0,
            "average_update_interval_days": None,
            "latest_video_title": "暂无公开视频",
            "upload_date": UNKNOWN_DATE,
            "days_since_update": 0,
            "days_since_last_video": 0,
            "view_count": 0,
            "video_url": "",
            "data_source": "no_video",
        }

    def build_video_duration_summary(self, following, videos):
        total_videos = len(videos)
        short_count = sum(1 for video in videos if video["duration_category"] == SHORT_VIDEO_LABEL)
        medium_count = sum(1 for video in videos if video["duration_category"] == MEDIUM_VIDEO_LABEL)
        medium_long_count = sum(
            1 for video in videos if video["duration_category"] == MEDIUM_LONG_VIDEO_LABEL
        )
        long_count = sum(1 for video in videos if video["duration_category"] == LONG_VIDEO_LABEL)
        total_duration_seconds = sum(video["duration_seconds"] for video in videos)
        average_duration_seconds = int(total_duration_seconds / total_videos) if total_videos else 0
        average_update_interval_days = calculate_average_update_interval_days(
            video.get("publish_timestamp") for video in videos
        )
        latest_publish_timestamp = max(
            (normalize_timestamp(video.get("publish_timestamp")) for video in videos),
            default=0,
        )
        fetched_like_videos = [
            video for video in videos if video.get("bvid") and video.get("like_count_fetched", False)
        ]
        total_like_count = sum(int(video.get("like_count") or 0) for video in fetched_like_videos)
        missing_like_count = sum(
            1 for video in videos if video.get("bvid") and not video.get("like_count_fetched", False)
        )

        return {
            "uploader_name": following.get("uname", "未知UP主"),
            "uploader_id": following.get("mid"),
            "follower_count": following.get("follower_count", 0),
            "total_videos": total_videos,
            "latest_publish_timestamp": latest_publish_timestamp,
            "total_duration_seconds": total_duration_seconds,
            "average_duration_seconds": average_duration_seconds,
            "average_duration_text": seconds_to_duration_text(average_duration_seconds),
            "average_like_count": int(total_like_count / len(fetched_like_videos))
            if fetched_like_videos
            else 0,
            "average_update_interval_days": average_update_interval_days,
            "missing_like_count": missing_like_count,
            "like_data_complete": missing_like_count == 0,
            "short_video_count": short_count,
            "short_video_ratio": format_ratio(short_count, total_videos),
            "medium_video_count": medium_count,
            "medium_video_ratio": format_ratio(medium_count, total_videos),
            "medium_long_video_count": medium_long_count,
            "medium_long_video_ratio": format_ratio(medium_long_count, total_videos),
            "long_video_count": long_count,
            "long_video_ratio": format_ratio(long_count, total_videos),
        }

    @staticmethod
    def _format_output_summary(paths):
        return "、".join(path.name for path in paths if path)

    def populate_duration_summary_defaults(self, summary, videos):
        completed_summary = dict(summary or {})
        completed_summary.setdefault("total_videos", len(videos or []))
        completed_summary.setdefault(
            "average_update_interval_days",
            calculate_average_update_interval_days(
                video.get("publish_timestamp") for video in (videos or [])
            ),
        )
        fetched_like_videos = [
            video
            for video in (videos or [])
            if video.get("bvid") and video.get("like_count_fetched", False)
        ]
        completed_summary.setdefault(
            "average_like_count",
            int(sum(int(video.get("like_count") or 0) for video in fetched_like_videos) / len(fetched_like_videos))
            if fetched_like_videos
            else 0,
        )
        missing_like_count = sum(
            1
            for video in (videos or [])
            if video.get("bvid") and not video.get("like_count_fetched", False)
        )
        completed_summary.setdefault("missing_like_count", missing_like_count)
        completed_summary.setdefault("like_data_complete", missing_like_count == 0)
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
        pending_entries = [
            (mid, entry)
            for mid, entry in duration_progress.items()
            if entry.get("videos", [])
            and self.count_missing_like_videos(entry.get("videos", [])) > 0
        ]

        if pending_entries:
            print(f"👍 准备为 {len(pending_entries)} 位UP主补抓历史点赞数据...")

        with create_progress() as progress:
            task_id = progress.add_task("补抓历史点赞", total=len(pending_entries))
            for mid, entry in pending_entries:
                progress.advance(task_id)
                if remaining_budget <= 0:
                    break

                videos = entry.get("videos", [])
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
                self.cache_store.save_video_duration_progress(duration_progress)
                refreshed_count += 1

                if rate_limit_hit:
                    break

        if refreshed_count:
            print(f"✅ 已补齐 {refreshed_count} 位UP主缓存中的部分真实点赞数据。")
        return duration_progress, remaining_budget, rate_limit_hit

    def enrich_results_with_profile_and_counts(self, results, duration_progress=None, followings=None):
        progress = duration_progress or {}
        following_map = {str(following.get("mid")): following for following in (followings or [])}

        for result in results:
            uploader_id = result.get("uploader_id")
            result["uploader_homepage"] = build_homepage_url(uploader_id)

            following = following_map.get(str(uploader_id), {})
            if following:
                result["following_group_ids"] = following.get("group_id_text", "0")
                result["following_group_names"] = following.get("group_name_text", DEFAULT_GROUP_NAME)
                result["follower_count"] = following.get(
                    "follower_count", result.get("follower_count", 0)
                )
            else:
                result["following_group_ids"] = result.get("following_group_ids") or "0"
                result["following_group_names"] = result.get("following_group_names") or DEFAULT_GROUP_NAME
                result.setdefault("follower_count", 0)

            entry = progress.get(str(uploader_id), {})
            summary = self.populate_duration_summary_defaults(
                entry.get("summary", {}),
                entry.get("videos", []),
            )
            if summary:
                result["published_video_count"] = summary.get(
                    "total_videos", result.get("published_video_count", 0)
                )
                result["average_like_count"] = summary.get(
                    "average_like_count", result.get("average_like_count", 0)
                )
                result["average_update_interval_days"] = summary.get(
                    "average_update_interval_days",
                    result.get("average_update_interval_days"),
                )
            else:
                result.setdefault("published_video_count", 0)
                result.setdefault("average_like_count", 0)
                result.setdefault("average_update_interval_days", None)

        return results

    def save_precise_result(self, mid, result_item, results_by_mid, cached_video_results):
        result_item["cached_at"] = int(time.time())
        mid_str = str(mid)
        results_by_mid[mid_str] = result_item
        cached_video_results[mid_str] = result_item
        self.cache_store.save_precise_progress(cached_video_results)

    def handle_precise_video_result(self, following, video_info, results_by_mid, cached_video_results):
        mid = following.get("mid")
        if video_info:
            result_item = self.build_result_item(video_info)
            result_item["following_group_ids"] = following.get("group_id_text", "")
            result_item["following_group_names"] = following.get("group_name_text", DEFAULT_GROUP_NAME)
            result_item["follower_count"] = following.get("follower_count", 0)
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
                print(f"   ⏭️  {uname} - 当前风控较严，先加入稍后重试队列")
                return following, None, "rate_limit"
            except Exception as exc:
                return following, None, exc

        with create_progress() as progress:
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
                        print(f"   ❌ {following.get('uname')} - 抓取异常: {error}")
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

        print(
            f"📌 批次 {label} 完成：成功 {success_count}，"
            f"无公开视频 {no_video_count}，待重试 {len(remaining_followings)}"
        )
        return remaining_followings

    def analyze_video_durations(self, followings):
        if not self.config.enable_video_duration_analysis:
            return {}

        print()
        print("=" * 60)
        print("📊 正在分析所有关注UP主的全部视频时长...")
        print("=" * 60)
        print(f"   当前视频时长分析并发数: {self.config.video_analysis_workers}")

        duration_progress = self.cache_store.load_video_duration_progress()
        like_fetch_budget = self.config.video_stat_max_requests_per_run
        like_rate_limited = False
        like_budget_exhausted_notified = False

        if duration_progress:
            print(f"♻️  已加载 {len(duration_progress)} 条视频时长分析缓存。")
            if self.config.enable_cached_video_like_backfill:
                duration_progress, like_fetch_budget, like_rate_limited = self.enrich_cached_video_like_counts(
                    followings,
                    duration_progress,
                    like_fetch_budget,
                )
            elif self.config.enable_real_video_like_fetch:
                print("⏭️  已跳过历史缓存视频的自动点赞补抓，避免本轮一开始触发风控。")

        pending_followings = [
            following
            for following in followings
            if self.cache_store.should_refresh_video_duration_cache(
                following, duration_progress.get(str(following.get("mid")))
            )
        ]
        failed_followings = []

        def process_duration(following, index):
            uname = following.get("uname", "未知UP主")
            try:
                time.sleep(random.uniform(0, 1.5))
                return following, self.api.get_all_videos_for_up(following.get("mid"), uname), None
            except RateLimitExceededError:
                print(f"   ⏭️  {uname} - 当前风控较严，先加入稍后重试队列")
                return following, None, "rate_limit"
            except requests.exceptions.RequestException as exc:
                print(f"   ⚠️  {uname} - 网络异常: {exc.__class__.__name__}，先加入稍后重试队列")
                return following, None, "network"
            except Exception as exc:
                return following, None, exc

        with create_progress() as progress:
            task_id = progress.add_task("全量视频时长分析", total=len(pending_followings))
            for start in range(0, len(pending_followings), self.config.video_analysis_batch_size):
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
                                print("⚠️  本轮真实点赞补抓已暂停，剩余视频先保留到后续运行再补抓。")
                        elif (
                            self.config.enable_real_video_like_fetch
                            and like_fetch_budget <= 0
                            and not like_budget_exhausted_notified
                        ):
                            print("⏭️  本轮真实点赞补抓预算已用完，剩余视频跳过点赞补抓。")
                            like_budget_exhausted_notified = True

                        summary = self.build_video_duration_summary(following, videos)
                        duration_progress[str(following.get("mid"))] = {
                            "uploader_name": uname,
                            "uploader_id": following.get("mid"),
                            "cached_at": int(time.time()),
                            "videos": videos,
                            "summary": summary,
                        }
                        self.cache_store.save_video_duration_progress(duration_progress)

                if start + self.config.video_analysis_batch_size < len(pending_followings):
                    cooldown = self.config.video_analysis_batch_cooldown + random.uniform(0, 5)
                    print(
                        f"⏸️  视频分析已完成 {start + len(batch)} 位UP主，"
                        f"批次冷却 {cooldown:.0f} 秒后继续..."
                    )
                    time.sleep(cooldown)

        all_video_rows = []
        summary_rows = []
        for following in followings:
            entry = duration_progress.get(str(following.get("mid")))
            if not entry:
                continue
            all_video_rows.extend(entry.get("videos", []))
            summary = self.populate_duration_summary_defaults(
                entry.get("summary", {}),
                entry.get("videos", []),
            )
            entry["summary"] = summary
            summary_rows.append(summary)

        if not summary_rows:
            print("⚠️  未生成任何视频时长分析结果。")
            return duration_progress

        save_all_videos_to_csv(self.config, all_video_rows)
        save_video_duration_analysis_to_csv(self.config, summary_rows)
        save_video_duration_report(self.config, summary_rows, len(all_video_rows))

        print(
            f"🗂️  视频分析阶段已输出 3 份文件："
            f"{self._format_output_summary([self.config.all_videos_csv, self.config.video_duration_analysis_csv, self.config.video_duration_report_md])}"
        )
        if failed_followings:
            print(f"⚠️  仍有 {len(failed_followings)} 位UP主未完成全量视频分析，下次运行会继续补抓。")
        return duration_progress

    def display_top_results(self, results):
        table = create_table(
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
            average_update_interval_days = result.get("average_update_interval_days")
            average_update_text = (
                f"{average_update_interval_days:.2f}"
                if isinstance(average_update_interval_days, (int, float))
                else "暂无"
            )
            table.add_row(
                str(index),
                str(result["uploader_name"]),
                str(result["days_since_update"]),
                f"{int(result.get('follower_count') or 0):,}",
                str(result.get("published_video_count", 0)),
                str(result.get("average_like_count", 0)),
                average_update_text,
                str(result.get("upload_date", UNKNOWN_DATE)),
            )

        get_console().print()
        get_console().print(table)
        get_console().print()

    def analyze_hiatus(self):
        self.api.check_cookie()

        print("=" * 60)
        print("🎯 B站催更分析器 - 寻找你关注的UP主中的「鸽王」")
        print("=" * 60)
        print()

        followings = self.api.get_followings_list()
        if not followings:
            print("❌ 无法获取关注列表，程序退出")
            return None

        for following in followings:
            relation_stat = self.api.get_uploader_relation_stat(
                following.get("mid"),
                following.get("uname", "UP主"),
            )
            following["follower_count"] = relation_stat.get("follower_count", 0)

        cached_video_results = self.cache_store.load_precise_progress()
        if cached_video_results:
            print(f"♻️  已加载 {len(cached_video_results)} 条历史抓取缓存。")

        results_by_mid = {}
        pending_followings = []
        for following in followings:
            mid = str(following.get("mid"))
            cached_result = cached_video_results.get(mid)
            if cached_result and not self.cache_store.should_refresh_precise_cache(following, cached_result):
                refreshed_result = dict(cached_result)
                self.cache_store.refresh_result_runtime_fields(refreshed_result)
                results_by_mid[mid] = refreshed_result
            else:
                pending_followings.append(following)

        print("🔍 正在精确抓取每位UP主最后一个视频时间...")
        if self.config.analysis_mode == "fallback":
            print("   如遇到无法补抓的UP主，将回退到关注列表活跃时间。")
        else:
            print("   当前为精确模式：仅接受视频动态时间作为最终结果。")
        print()

        failed_followings = []
        if pending_followings:
            print(f"🎬 仍有 {len(pending_followings)} 位UP主需要精确抓取。")
            print(
                f"⏸️  先冷却 {self.config.video_analysis_start_delay} 秒，"
                "降低进入视频动态接口时立刻触发风控的概率..."
            )
            time.sleep(self.config.video_analysis_start_delay)

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
                if start + self.config.batch_size < len(pending_followings):
                    cooldown = self.config.batch_cooldown + random.uniform(0, 5)
                    print(
                        f"⏸️  已完成 {start + len(batch)} 位UP主，"
                        f"批次冷却 {cooldown:.0f} 秒后继续..."
                    )
                    time.sleep(cooldown)

        for retry_round in range(1, self.config.max_failed_retry_rounds + 1):
            if not failed_followings:
                break

            cooldown = self.config.failed_retry_cooldown * retry_round + random.uniform(0, 10)
            print()
            print(f"🔁  第 {retry_round} 轮补抓开始，先冷却 {cooldown:.0f} 秒...")
            time.sleep(cooldown)
            failed_followings = self.run_precise_fetch_round(
                failed_followings,
                f"补抓第{retry_round}轮",
                results_by_mid,
                cached_video_results,
            )

        if self.config.analysis_mode == "fallback" and failed_followings:
            print(f"\n↩️  仍有 {len(failed_followings)} 位UP主未完成精确抓取，回退到关注列表活跃时间。")
            for following in failed_followings:
                mid = str(following.get("mid"))
                if mid not in results_by_mid:
                    results_by_mid[mid] = self.build_following_result_item(following)

        duration_progress = self.cache_store.load_video_duration_progress()
        results = self.enrich_results_with_profile_and_counts(
            list(results_by_mid.values()),
            duration_progress,
            followings,
        )
        if not results:
            print("\n❌ 未能获取到任何视频数据")
            return None

        if self.config.analysis_mode == "precise" and failed_followings:
            print(f"\n⚠️  仍有 {len(failed_followings)} 位UP主因频率限制未获取成功。")
            print("   下次运行会自动复用已保存进度，继续补抓剩余UP主。")

        results.sort(key=lambda item: item["days_since_update"], reverse=True)
        self.display_top_results(results)
        save_to_csv(self.config, results)
        print(
            f"🗂️  B站主榜阶段已输出：{self.config.output_csv.name}，"
            f"共 {len(results)} 位UP主"
        )

        duration_progress = self.analyze_video_durations(followings)
        if duration_progress:
            self.enrich_results_with_profile_and_counts(results, duration_progress, followings)
            save_to_csv(self.config, results)
            print(f"🔄 已用视频分析结果回填主榜：{self.config.output_csv.name}")

        return results
