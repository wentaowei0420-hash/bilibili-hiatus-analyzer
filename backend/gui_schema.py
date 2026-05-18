from __future__ import annotations

from .task_runner import BILIBILI_RUNTIME_FIELDS as BILIBILI_RUNTIME_ENV_FIELDS
from .task_runner import DOUYIN_RUNTIME_FIELDS as DOUYIN_RUNTIME_ENV_FIELDS


BILIBILI_RUNTIME_FIELD_UI = {
    "video_stat_batch_cooldown": ("视频统计批次冷却", 0, 3600, 1),
    "request_delay": ("请求基础间隔", 0, 3600, 1),
    "max_request_delay": ("请求最大间隔", 0, 3600, 1),
    "video_analysis_start_delay": ("视频分析启动等待", 0, 3600, 1),
    "batch_cooldown": ("主批次冷却", 0, 3600, 1),
    "long_rate_limit_cooldown": ("限流长冷却", 0, 7200, 1),
    "rate_limit_retry_before_long_cooldown": ("长冷却前重试次数", 1, 100, 1),
    "max_rate_limit_retries": ("限流最大重试次数", 1, 100, 1),
    "failed_retry_cooldown": ("失败重试冷却", 0, 7200, 1),
    "video_analysis_batch_cooldown": ("视频分析批次冷却", 0, 3600, 1),
}

DOUYIN_RUNTIME_FIELD_UI = {
    "page_load_delay": ("页面加载等待", 0.0, 1200.0, 0.1),
    "user_request_interval": ("用户请求间隔", 0.0, 1200.0, 0.1),
    "request_rate_limit_per_second": ("每秒请求上限", 0.1, 100.0, 0.1),
    "retry_backoff_base_seconds": ("重试退避起始秒数", 0.0, 7200.0, 0.5),
    "retry_backoff_max_seconds": ("重试退避最大秒数", 0.0, 7200.0, 0.5),
    "conservative_mode_duration_seconds": ("保守模式持续秒数", 0.0, 7200.0, 1.0),
    "refresh_batch_cooldown": ("刷新批次冷却", 0.0, 7200.0, 0.5),
    "browser_restart_interval_users": ("浏览器重启间隔用户数", 1, 10000, 1),
    "video_page_load_delay": ("视频页加载等待", 0.0, 1200.0, 0.1),
    "service_error_retry_wait": ("服务异常重试等待", 0.0, 7200.0, 0.5),
    "service_error_long_cooldown": ("服务异常长冷却", 0.0, 7200.0, 0.5),
    "service_error_global_cooldown": ("服务异常全局冷却", 0.0, 7200.0, 0.5),
    "rate_limit_retry_wait": ("限流重试等待", 0.0, 7200.0, 0.5),
    "rate_limit_long_cooldown": ("限流长冷却", 0.0, 7200.0, 0.5),
    "rate_limit_global_cooldown": ("限流全局冷却", 0.0, 7200.0, 0.5),
    "progress_save_interval_users": ("进度保存间隔用户数", 1, 10000, 1),
    "intermediate_upload_interval_users": ("中间上传间隔用户数", 1, 10000, 1),
    "unfollow_interval_seconds": ("取消关注间隔秒数", 0.0, 1200.0, 0.1),
    "unfollow_batch_cooldown": ("取消关注批次冷却", 0.0, 7200.0, 0.5),
    "unfollow_restart_interval": ("取消关注重启间隔", 1, 10000, 1),
    "unfollow_failure_cooldown": ("取消关注失败冷却", 0.0, 7200.0, 0.5),
    "video_browser_fallback_max_ids": ("浏览器兜底打开视频数", 0, 100, 1),
}


def _runtime_fields(env_fields, ui_fields):
    fields = []
    for name, (env_name, value_type) in env_fields.items():
        label, minimum, maximum, step = ui_fields[name]
        fields.append(
            {
                "name": name,
                "env_name": env_name,
                "label": label,
                "type": value_type,
                "minimum": minimum,
                "maximum": maximum,
                "step": step,
            }
        )
    return fields


def _options(items):
    return [{"label": label, "value": value} for label, value in items]


def _columns(items):
    return [{"label": label, "key": key} for label, key in items]


def get_gui_metadata() -> dict[str, object]:
    return {
        "runtime_fields": {
            "bilibili": _runtime_fields(BILIBILI_RUNTIME_ENV_FIELDS, BILIBILI_RUNTIME_FIELD_UI),
            "douyin": _runtime_fields(DOUYIN_RUNTIME_ENV_FIELDS, DOUYIN_RUNTIME_FIELD_UI),
        },
        "fetch_order_options": {
            "bilibili": _options(
                [
                    ("粉丝数", "follower_count"),
                    ("视频总数", "published_video_count"),
                    ("平均点赞数", "average_like_count"),
                ]
            ),
            "douyin": _options(
                [
                    ("粉丝数", "follower_count"),
                    ("视频总数", "published_video_count"),
                    ("获赞总数", "total_favorited"),
                    ("平均点赞数", "average_like_count"),
                ]
            ),
            "directions": _options([("降序", "desc"), ("升序", "asc")]),
        },
        "main_window": {
            "bilibili_modes": _options(
                [
                    ("精确模式（主榜 + 视频分析）", "precise_full"),
                    ("精确模式（仅主榜）", "precise_main_only"),
                    ("回退模式（主榜 + 视频分析）", "fallback_full"),
                    ("回退模式（仅主榜）", "fallback_main_only"),
                ]
            ),
            "douyin_modes": _options(
                [
                    ("基础统计模式（粉丝数/获赞总数/视频数）", "counts"),
                    ("主页校验模式（按基础缓存进主页核对）", "verify"),
                    ("监控模式（推荐日常使用）", "monitor"),
                    ("增量模式（只补变化数据）", "delta"),
                    ("完整模式（抓取视频明细）", "full"),
                ]
            ),
            "browser_backends": _options(
                [
                    ("DrissionPage", "drission"),
                    ("Playwright", "playwright"),
                ]
            ),
            "platforms": _options(
                [
                    ("B站 + 抖音", "both"),
                    ("仅 B站", "bilibili"),
                    ("仅 抖音", "douyin"),
                    ("抖音取消关注", "douyin_unfollow"),
                    ("B站 UID 全量视频", "bilibili_uid"),
                    ("抖音 UID 全量视频", "douyin_uid"),
                    ("导出抖音高赞视频", "douyin_high_like"),
                    ("抖音视频评分", "douyin_video_score"),
                    ("抖音UP主评分", "douyin_creator_score"),
                    ("导出抖音精简表", "douyin_compact_export"),
                ]
            ),
            "actions": _options(
                [
                    ("仅抓取", "fetch"),
                    ("抓取并上传飞书", "fetch_upload"),
                    ("仅上传飞书", "upload"),
                ]
            ),
        },
        "stats": {
            "modes": _options(
                [
                    ("主页校验模式", "verify"),
                    ("监控模式", "monitor"),
                    ("完整模式", "full"),
                ]
            ),
            "creator_video_buckets": [
                {"label": "0~50", "lower": 0, "upper": 50},
                {"label": "51~300", "lower": 51, "upper": 300},
                {"label": "301~500", "lower": 301, "upper": 500},
                {"label": "501~1000", "lower": 501, "upper": 1000},
                {"label": "1001以上", "lower": 1001, "upper": None},
            ],
            "video_duration_buckets": [
                {"label": "0~20s", "lower": 0, "upper": 20},
                {"label": "21~60s", "lower": 21, "upper": 60},
                {"label": "61s以上", "lower": 61, "upper": None},
            ],
        },
        "rating": {
            "grades": ["S", "A", "B", "C", "D"],
            "factor_columns": _columns(
                [
                    ("粉丝数量分", "粉丝数量分"),
                    ("获赞总数分", "获赞总数分"),
                    ("最近更新时间分", "最近更新时间分"),
                    ("平均几天一更分", "平均几天一更分"),
                    ("视频数量分", "视频数量分"),
                    ("最早视频时间分", "最早视频时间分"),
                    ("平均点赞数分", "平均点赞数分"),
                    ("视频等级分布分", "视频等级分布分"),
                    ("低等级比例分", "低等级比例分"),
                    ("最近10条趋势分", "最近10条趋势分"),
                    ("风险扣分", "风险扣分"),
                ]
            ),
        },
        "tables": {
            "rating_creator_top": _columns(
                [
                    ("UP主", "uploader_name"),
                    ("等级", "final_grade"),
                    ("分数", "final_score"),
                    ("置信度", "confidence"),
                    ("粉丝数", "follower_count"),
                    ("作品数(缓存)", "video_count"),
                    ("详情", "uploader_id"),
                ]
            ),
            "rating_creator_low": _columns(
                [
                    ("UP主", "uploader_name"),
                    ("等级", "final_grade"),
                    ("分数", "final_score"),
                    ("置信度", "confidence"),
                    ("未更新天数", "inactive_days"),
                    ("低等级比例", "low_grade_ratio"),
                    ("详情", "uploader_id"),
                ]
            ),
            "rating_archived_creator": _columns(
                [
                    ("UP主", "uploader_name"),
                    ("等级", "final_grade"),
                    ("分数", "final_score"),
                    ("置信度", "confidence"),
                    ("未更新天数", "inactive_days"),
                    ("粉丝数", "follower_count"),
                    ("作品数", "published_video_count"),
                    ("归档时间", "archived_at"),
                    ("归档原因", "archive_reason"),
                    ("详情", "uploader_id"),
                ]
            ),
            "status_reset": _columns(
                [
                    ("UP主", "uploader_name"),
                    ("发布视频数", "published_video_count"),
                    ("缓存视频数", "cached_video_count"),
                    ("差值", "diff_count"),
                    ("最近抓取模式", "last_fetch_mode"),
                    ("已缓存模式", "cache_modes"),
                    ("缓存时间", "progress_cached_at"),
                    ("UP主主页链接", "homepage_url"),
                ]
            ),
            "archive_candidates": _columns(
                [
                    ("UP主", "uploader_name"),
                    ("未更新天数", "inactive_days"),
                    ("最后发布时间", "latest_publish_time"),
                    ("等级", "final_grade"),
                    ("粉丝数", "follower_count"),
                    ("作品数", "published_video_count"),
                    ("缓存视频数", "cached_video_count"),
                    ("最近抓取模式", "last_fetch_mode"),
                    ("UP主主页链接", "homepage_url"),
                ]
            ),
            "archived": _columns(
                [
                    ("UP主", "uploader_name"),
                    ("状态", "archive_status"),
                    ("未更新天数", "inactive_days"),
                    ("最后发布时间", "latest_publish_time"),
                    ("等级", "final_grade"),
                    ("归档时间", "archived_at"),
                    ("归档原因", "archive_reason"),
                    ("UP主主页链接", "homepage_url"),
                ]
            ),
        },
    }
