import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .http_client import RateLimitExceededError
from .logging_utils import smart_print as print
from .utils import (
    categorize_duration,
    format_group_ids,
    format_group_names,
    normalize_group_ids,
    normalize_timestamp,
    parse_duration_to_seconds,
    parse_view_count,
    timestamp_to_date,
)


class BilibiliApi:
    def __init__(self, config, client):
        self.config = config
        self.client = client

    def check_cookie(self):
        if not self.config.cookie.strip() or self.config.cookie == "在这里粘贴你的Cookie":
            print("错误: 请先配置 BILIBILI_COOKIE。")
            raise SystemExit(1)

    def get_user_mid(self):
        try:
            data = self.client.get_json_with_retry(self.config.nav_api, request_name="获取当前用户信息")
            if data.get("code") == 0:
                return data.get("data", {}).get("mid")
            print(f"获取用户信息失败: {data.get('message', '未知错误')}")
            return None
        except Exception as exc:
            print(f"获取用户信息出错: {exc}")
            return None

    def get_following_tag_map(self):
        try:
            data = self.client.get_json_with_retry(
                self.config.following_tags_api,
                request_name="获取关注分组列表",
            )
            if data.get("code") != 0:
                print(f"获取关注分组失败: {data.get('message', '未知错误')}")
                return {}

            tag_map = {}
            for item in data.get("data", []) or []:
                tag_id = item.get("tagid")
                name = item.get("name")
                if tag_id is None or not name:
                    continue
                tag_map[int(tag_id)] = name
            return tag_map
        except Exception as exc:
            print(f"获取关注分组出错: {exc}")
            return {}

    def get_followings_list(self):
        print("📜 正在获取关注列表...")
        user_mid = self.get_user_mid()
        if not user_mid:
            return None

        tag_name_map = self.get_following_tag_map()
        all_followings = []
        page = 1

        while True:
            try:
                data = self.client.get_json_with_retry(
                    self.config.followings_api,
                    params={"vmid": user_mid, "pn": page, "ps": 50, "order": "desc"},
                    request_name=f"获取关注列表第 {page} 页",
                )
                if data.get("code") != 0:
                    print(f"获取关注列表失败: {data.get('message', '未知错误')}")
                    return None

                followings = data.get("data", {}).get("list", []) or []
                if not followings:
                    break

                for following in followings:
                    group_ids = normalize_group_ids(following.get("tag"))
                    following["group_ids"] = group_ids
                    following["group_id_text"] = format_group_ids(group_ids)
                    following["group_name_text"] = format_group_names(group_ids, tag_name_map)

                all_followings.extend(followings)
                print(f"已获取 {len(all_followings)} 位UP主...")

                total = (data.get("data") or {}).get("total", 0)
                if total and len(all_followings) >= total:
                    break

                page += 1
                time.sleep(self.client.get_request_delay())
            except Exception as exc:
                print(f"获取关注列表出错: {exc}")
                return None

        print(f"✅ 成功获取 {len(all_followings)} 位关注的UP主")
        return all_followings

    def get_uploader_relation_stat(self, mid, uname="UP主"):
        try:
            data = self.client.get_json_with_retry(
                self.config.relation_stat_api,
                params={"vmid": mid},
                request_name=f"获取 {uname} 的粉丝统计",
            )
            if data.get("code") != 0:
                print(f"   ⚠️  {uname} - 获取粉丝统计失败: {data.get('message', '未知错误')}")
                return {}

            payload = data.get("data", {}) or {}
            return {
                "follower_count": parse_view_count(payload.get("follower", 0)),
                "following_count": parse_view_count(payload.get("following", 0)),
            }
        except Exception as exc:
            print(f"   ⚠️  {uname} - 获取粉丝统计出错: {exc}")
            return {}

    def extract_video_info_from_dynamic_item(self, item, uname, mid):
        if item.get("type") != "DYNAMIC_TYPE_AV":
            return None

        modules = item.get("modules", {}) or {}
        module_author = modules.get("module_author", {}) or {}
        archive = (
            (modules.get("module_dynamic", {}) or {}).get("major", {}) or {}
        ).get("archive", {}) or {}
        if not archive:
            return None

        jump_url = archive.get("jump_url") or ""
        if jump_url.startswith("//"):
            jump_url = f"https:{jump_url}"

        like_count, like_count_fetched = self.extract_video_like_info(archive)
        return {
            "uploader_name": uname,
            "uploader_id": mid,
            "video_title": archive.get("title", "未知标题"),
            "bvid": archive.get("bvid", ""),
            "upload_timestamp": normalize_timestamp(module_author.get("pub_ts")),
            "like_count": like_count,
            "like_count_fetched": like_count_fetched,
            "view_count": parse_view_count((archive.get("stat") or {}).get("play", 0)),
            "video_url": jump_url,
        }

    def extract_video_like_info(self, video):
        stat = (video.get("stat") or {}) if isinstance(video, dict) else {}
        candidates = [
            (video, "like"),
            (video, "like_count"),
            (video, "favorites"),
            (video, "favorite"),
            (video, "fav"),
            (stat, "like"),
            (stat, "favorite"),
            (stat, "favorites"),
        ]
        for container, key in candidates:
            if isinstance(container, dict) and key in container and container.get(key) is not None:
                return parse_view_count(container.get(key)), True
        return 0, False

    def extract_video_like_count(self, video):
        like_count, _ = self.extract_video_like_info(video)
        return like_count

    def get_video_detail_stat(self, bvid, video_title="视频"):
        try:
            data = self.client.get_json_with_retry(
                self.config.video_view_api,
                params={"bvid": bvid},
                request_name=f"获取 {video_title} 的视频统计",
            )
            if data.get("code") != 0:
                print(f"   ⚠️  {video_title} - 获取视频统计失败: {data.get('message', '未知错误')}")
                return {}

            stat = ((data.get("data") or {}).get("stat") or {})
            return {
                "like_count": parse_view_count(stat.get("like", 0)),
                "view_count": parse_view_count(stat.get("view", 0)),
            }
        except RateLimitExceededError:
            raise
        except Exception as exc:
            print(f"   ⚠️  {video_title} - 获取视频统计出错: {exc}")
            return {}

    def enrich_videos_with_detail_stats(self, videos, uname, max_videos=None):
        pending_indexes = [
            index
            for index, video in enumerate(videos)
            if video.get("bvid") and not video.get("like_count_fetched", False)
        ]
        if not pending_indexes:
            return {"attempted": 0, "completed": 0, "rate_limit_hit": False}

        if max_videos is not None and max_videos >= 0:
            pending_indexes.sort(
                key=lambda index: normalize_timestamp(
                    videos[index].get("publish_timestamp") or videos[index].get("upload_timestamp")
                ),
                reverse=True,
            )
            pending_indexes = pending_indexes[:max_videos]

        if not pending_indexes:
            return {"attempted": 0, "completed": 0, "rate_limit_hit": False}

        print(f"   🔡  {uname} - 正在补抓 {len(pending_indexes)} 个视频的真实点赞数...")

        def fetch_single(index):
            video = videos[index]
            time.sleep(random.uniform(0, 0.3))
            stats = self.get_video_detail_stat(
                video.get("bvid", ""),
                video.get("video_title", video.get("bvid", "视频")),
            )
            return index, stats

        completed = 0
        rate_limit_hit = False
        batch_size = max(1, self.config.video_stat_batch_size)

        for start in range(0, len(pending_indexes), batch_size):
            batch_indexes = pending_indexes[start:start + batch_size]
            try:
                with ThreadPoolExecutor(
                    max_workers=max(1, min(self.config.video_stat_workers, len(batch_indexes)))
                ) as executor:
                    futures = [executor.submit(fetch_single, index) for index in batch_indexes]
                    for future in as_completed(futures):
                        index, stats = future.result()
                        if not stats:
                            continue
                        videos[index]["like_count"] = stats.get(
                            "like_count", videos[index].get("like_count", 0)
                        )
                        videos[index]["view_count"] = stats.get(
                            "view_count", videos[index].get("view_count", 0)
                        )
                        videos[index]["like_count_fetched"] = True
                        completed += 1
            except RateLimitExceededError:
                rate_limit_hit = True
                break

            if start + batch_size < len(pending_indexes):
                cooldown = self.config.video_stat_batch_cooldown + random.uniform(0, 3)
                print(
                    f"   ⏸️  {uname} - 点赞补抓已完成 {start + len(batch_indexes)} 个视频，"
                    f"冷却 {cooldown:.0f} 秒后继续..."
                )
                time.sleep(cooldown)

        if rate_limit_hit:
            print(f"   ⚠️  {uname} - 点赞补抓触发频率限制，本轮先暂停，下次再继续补抓。")

        return {
            "attempted": len(pending_indexes),
            "completed": completed,
            "rate_limit_hit": rate_limit_hit,
        }

    def get_latest_video(self, mid, uname):
        try:
            offset = ""
            for page_no in range(1, self.config.max_dynamic_pages + 1):
                data = self.client.get_json_with_retry(
                    self.config.space_dynamic_api,
                    params={"host_mid": mid, "offset": offset},
                    request_name=f"获取 {uname} 的视频动态第 {page_no} 页",
                )
                if data.get("code") != 0:
                    print(f"   ⚠️  {uname} - API返回错误: {data.get('message', '未知错误')}")
                    return None

                payload = data.get("data", {}) or {}
                items = payload.get("items", []) or []
                candidates = [
                    video
                    for video in (
                        self.extract_video_info_from_dynamic_item(item, uname, mid) for item in items
                    )
                    if video
                ]
                if candidates:
                    return max(candidates, key=lambda item: item.get("upload_timestamp", 0))

                offset = payload.get("offset", "")
                if not payload.get("has_more") or not offset:
                    break

                time.sleep(self.client.get_request_delay())

            print(f"   📥 {uname} - 最近动态中未找到视频")
            return False
        except RateLimitExceededError:
            raise
        except requests.exceptions.RequestException as exc:
            print(f"   ❌ {uname} - 网络请求失败: {exc}")
            return None
        except Exception as exc:
            print(f"   ❌ {uname} - 解析数据失败: {exc}")
            return None

    def get_all_videos_for_up(self, mid, uname):
        all_videos = []
        page_no = 1
        total_count = None

        while True:
            data = self.client.get_wbi_json_with_retry(
                self.config.space_wbi_arc_search_api,
                params={
                    "mid": mid,
                    "ps": self.config.video_list_page_size,
                    "pn": page_no,
                    "order": "pubdate",
                },
                request_name=f"获取 {uname} 的全部视频第 {page_no} 页",
            )
            if data.get("code") != 0:
                print(f"   ⚠️  {uname} - 获取全部视频失败: {data.get('message', '未知错误')}")
                return None

            payload = data.get("data", {}) or {}
            total_count = total_count or ((payload.get("page") or {}).get("count") or 0)
            video_list = ((payload.get("list") or {}).get("vlist") or [])
            if not video_list:
                break

            for video in video_list:
                duration_text = video.get("length", "")
                duration_seconds = parse_duration_to_seconds(duration_text)
                like_count, like_count_fetched = self.extract_video_like_info(video)

                jump_url = video.get("jump_url") or ""
                if jump_url.startswith("//"):
                    jump_url = f"https:{jump_url}"
                elif not jump_url and video.get("bvid"):
                    jump_url = f"https://www.bilibili.com/video/{video.get('bvid')}"

                all_videos.append(
                    {
                        "uploader_name": uname,
                        "uploader_id": mid,
                        "video_title": video.get("title", "未知标题"),
                        "bvid": video.get("bvid", ""),
                        "publish_date": timestamp_to_date(video.get("created")),
                        "publish_timestamp": normalize_timestamp(video.get("created")),
                        "duration_text": duration_text,
                        "duration_seconds": duration_seconds,
                        "duration_category": categorize_duration(duration_seconds),
                        "like_count": like_count,
                        "like_count_fetched": like_count_fetched,
                        "view_count": parse_view_count(video.get("play", 0)),
                        "video_url": jump_url,
                    }
                )

            if total_count and len(all_videos) >= total_count:
                break

            page_no += 1
            time.sleep(self.client.get_request_delay())

        return all_videos
