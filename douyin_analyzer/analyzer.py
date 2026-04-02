import time

from bilibili_analyzer.logging_utils import smart_print as print

from .exporters import (
    save_all_videos_to_csv,
    save_to_csv,
    save_video_duration_analysis_to_csv,
    save_video_duration_report,
)
from .utils import (
    DEFAULT_GROUP_NAME,
    LONG_VIDEO_LABEL,
    MEDIUM_LONG_VIDEO_LABEL,
    MEDIUM_VIDEO_LABEL,
    SHORT_VIDEO_LABEL,
    UNKNOWN_DATE,
    calculate_average_update_interval_days,
    calculate_days_since,
    format_ratio,
    normalize_timestamp,
    seconds_to_duration_text,
)


class DouyinHiatusAnalyzer:
    def __init__(self, config, browser_client, cache_store):
        self.config = config
        self.browser_client = browser_client
        self.cache_store = cache_store

    def build_result_item(self, user, summary, latest_video):
        upload_timestamp = normalize_timestamp(latest_video.get("publish_timestamp"))
        days_since = calculate_days_since(upload_timestamp)
        return {
            "uploader_name": user["nickname"],
            "uploader_id": user["sec_uid"],
            "uploader_homepage": user["homepage"],
            "following_group_names": DEFAULT_GROUP_NAME,
            "published_video_count": summary["total_videos"],
            "average_update_interval_days": summary.get("average_update_interval_days"),
            "latest_video_title": latest_video.get("video_title", "无标题视频"),
            "upload_timestamp": upload_timestamp,
            "upload_date": latest_video.get("publish_date", UNKNOWN_DATE),
            "days_since_update": days_since,
            "days_since_last_video": days_since,
            "view_count": latest_video.get("view_count", 0),
            "video_url": latest_video.get("video_url", ""),
            "data_source": "douyin_video_api",
        }

    def build_no_video_result_item(self, user):
        return {
            "uploader_name": user["nickname"],
            "uploader_id": user["sec_uid"],
            "uploader_homepage": user["homepage"],
            "following_group_names": DEFAULT_GROUP_NAME,
            "published_video_count": 0,
            "average_update_interval_days": None,
            "latest_video_title": "暂无公开视频",
            "upload_date": UNKNOWN_DATE,
            "days_since_update": 0,
            "days_since_last_video": 0,
            "view_count": 0,
            "video_url": "",
            "data_source": "no_video",
        }

    def build_fetch_failed_result_item(self, user):
        return {
            "uploader_name": user["nickname"],
            "uploader_id": user["sec_uid"],
            "uploader_homepage": user["homepage"],
            "following_group_names": DEFAULT_GROUP_NAME,
            "published_video_count": 0,
            "average_update_interval_days": None,
            "latest_video_title": "抓取失败",
            "upload_date": UNKNOWN_DATE,
            "days_since_update": 0,
            "days_since_last_video": 0,
            "view_count": 0,
            "video_url": "",
            "data_source": "fetch_failed",
        }

    def build_empty_summary(self, user):
        return {
            "uploader_name": user["nickname"],
            "uploader_id": user["sec_uid"],
            "total_videos": 0,
            "latest_publish_timestamp": 0,
            "total_duration_seconds": 0,
            "average_duration_seconds": 0,
            "average_duration_text": "00:00",
            "average_update_interval_days": None,
            "short_video_count": 0,
            "short_video_ratio": "0.00%",
            "medium_video_count": 0,
            "medium_video_ratio": "0.00%",
            "medium_long_video_count": 0,
            "medium_long_video_ratio": "0.00%",
            "long_video_count": 0,
            "long_video_ratio": "0.00%",
        }

    def build_video_duration_summary(self, user, videos):
        if not videos:
            return self.build_empty_summary(user)

        total_videos = len(videos)
        short_count = sum(1 for video in videos if video["duration_category"] == SHORT_VIDEO_LABEL)
        medium_count = sum(1 for video in videos if video["duration_category"] == MEDIUM_VIDEO_LABEL)
        medium_long_count = sum(
            1 for video in videos if video["duration_category"] == MEDIUM_LONG_VIDEO_LABEL
        )
        long_count = sum(1 for video in videos if video["duration_category"] == LONG_VIDEO_LABEL)
        total_duration_seconds = sum(video["duration_seconds"] for video in videos)
        average_duration_seconds = int(total_duration_seconds / total_videos) if total_videos else 0
        latest_publish_timestamp = max(
            (normalize_timestamp(video.get("publish_timestamp")) for video in videos),
            default=0,
        )

        return {
            "uploader_name": user["nickname"],
            "uploader_id": user["sec_uid"],
            "total_videos": total_videos,
            "latest_publish_timestamp": latest_publish_timestamp,
            "total_duration_seconds": total_duration_seconds,
            "average_duration_seconds": average_duration_seconds,
            "average_duration_text": seconds_to_duration_text(average_duration_seconds),
            "average_update_interval_days": calculate_average_update_interval_days(
                video.get("publish_timestamp") for video in videos
            ),
            "short_video_count": short_count,
            "short_video_ratio": format_ratio(short_count, total_videos),
            "medium_video_count": medium_count,
            "medium_video_ratio": format_ratio(medium_count, total_videos),
            "medium_long_video_count": medium_long_count,
            "medium_long_video_ratio": format_ratio(medium_long_count, total_videos),
            "long_video_count": long_count,
            "long_video_ratio": format_ratio(long_count, total_videos),
        }

    def display_top_results(self, results):
        print("\n" + "=" * 60)
        print("🏆 抖音断更排行榜 - Top 10")
        print("=" * 60)
        print()

        for index, result in enumerate(results[:10], 1):
            print(f"第 {index} 名: {result['uploader_name']}")
            print(f"   ⏰ 已断更 {result['days_since_update']} 天")
            print(f"   🎞️  发布视频数量: {result.get('published_video_count', 0)}")
            average_update_interval_days = result.get("average_update_interval_days")
            average_update_text = (
                f"{average_update_interval_days} 天"
                if average_update_interval_days is not None
                else "暂无数据"
            )
            print(f"   📐 平均几天一更: {average_update_text}")
            print(f"   📺 最新视频: {result['latest_video_title']}")
            print(f"   📅 发布日期: {result['upload_date']}")
            print(f"   🔗 主页: {result['uploader_homepage']}")
            print()

    def analyze_hiatus(self):
        self.browser_client.ensure_login()
        followings = self.browser_client.get_followings()
        if not followings:
            print("❌ 未能获取到任何抖音关注列表")
            return None

        progress = self.cache_store.load_progress()
        if progress:
            print(f"♻️  已加载 {len(progress)} 条抖音缓存")

        results = []
        all_video_rows = []
        summary_rows = []

        for index, user in enumerate(followings, 1):
            print(f"[{index}/{len(followings)}] 正在分析 {user['nickname']} ...")
            entry = progress.get(user["sec_uid"])
            if self.cache_store.should_refresh_cache(entry):
                try:
                    videos = self.browser_client.get_all_videos_for_user(user)
                except Exception as exc:
                    print(f"⚠️  {user['nickname']} 抓取失败: {exc}")
                    if entry:
                        videos = entry.get("videos", [])
                    else:
                        results.append(self.build_fetch_failed_result_item(user))
                        continue

                summary = self.build_video_duration_summary(user, videos)
                progress[user["sec_uid"]] = {
                    "cached_at": int(time.time()),
                    "user": user,
                    "videos": videos,
                    "summary": summary,
                }
                self.cache_store.save_progress(progress)
            else:
                videos = entry.get("videos", [])
                summary = entry.get("summary", self.build_empty_summary(user))

            if videos:
                latest_video = max(
                    videos,
                    key=lambda item: normalize_timestamp(item.get("publish_timestamp")),
                )
                result = self.build_result_item(user, summary, latest_video)
            else:
                result = self.build_no_video_result_item(user)

            self.cache_store.refresh_result_runtime_fields(result)
            results.append(result)
            summary_rows.append(summary)
            all_video_rows.extend(videos)

        results.sort(key=lambda item: item.get("days_since_update", 0), reverse=True)
        self.display_top_results(results)
        save_to_csv(self.config, results)

        if self.config.enable_video_duration_analysis:
            save_all_videos_to_csv(self.config, all_video_rows)
            save_video_duration_analysis_to_csv(self.config, summary_rows)
            save_video_duration_report(self.config, summary_rows, len(all_video_rows))

        print(f"✅ 抖音排行已保存到文件: {self.config.output_csv.name}")
        if self.config.enable_video_duration_analysis:
            print(f"✅ 抖音视频明细已保存到文件: {self.config.all_videos_csv.name}")
            print(f"✅ 抖音视频时长分析已保存到文件: {self.config.video_duration_analysis_csv.name}")
            print(f"✅ 抖音视频时长报告已保存到文件: {self.config.video_duration_report_md.name}")
        return results
