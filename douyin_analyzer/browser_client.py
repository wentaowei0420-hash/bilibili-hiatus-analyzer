import random
import time

from DrissionPage import ChromiumOptions, ChromiumPage

from bilibili_analyzer.logging_utils import smart_print as print

from .utils import (
    categorize_duration,
    normalize_duration_seconds,
    normalize_timestamp,
    parse_view_count,
    seconds_to_duration_text,
    timestamp_to_date,
)


class DouyinBrowserClient:
    def __init__(self, config):
        self.config = config
        self.page = None

    def start(self):
        if self.page is not None:
            return self.page

        co = ChromiumOptions()
        co.set_user_data_path(str(self.config.browser_user_data_path))
        self.page = ChromiumPage(co)
        return self.page

    def close(self):
        if self.page is not None:
            try:
                self.page.quit()
            except Exception:
                pass
            self.page = None

    def ensure_login(self):
        page = self.start()
        page.get(self.config.home_url)
        time.sleep(self.config.page_load_delay)
        if page.ele("text=登录", timeout=2):
            print("⚠️  尚未登录抖音，请先在浏览器中完成扫码登录。")
            input("登录成功并刷新页面后，按回车继续...")
        print("✅ 抖音登录状态已确认。")

    def get_followings(self):
        page = self.start()
        print("📥 正在抓取抖音关注列表...")
        page.listen.start(self.config.following_api_pattern)
        page.get(self.config.self_user_url)
        time.sleep(self.config.page_load_delay)

        try:
            tab = page.ele("text:关注", timeout=3)
            if tab:
                tab.click()
                time.sleep(1)
        except Exception:
            pass

        followings = []
        seen_sec_uids = set()
        empty_rounds = 0

        while True:
            self._scroll_active_containers()
            packet = page.listen.wait(timeout=self.config.packet_timeout)
            if not packet:
                empty_rounds += 1
                if empty_rounds >= self.config.empty_round_limit:
                    break
                continue

            empty_rounds = 0
            data = self._extract_packet_body(packet)
            for user in data.get("followings") or []:
                sec_uid = user.get("sec_uid") or ""
                if not sec_uid or sec_uid in seen_sec_uids:
                    continue
                seen_sec_uids.add(sec_uid)
                followings.append(
                    {
                        "nickname": user.get("nickname", "未知UP主"),
                        "sec_uid": sec_uid,
                        "homepage": f"https://www.douyin.com/user/{sec_uid}",
                    }
                )

            print(f"   已发现 {len(followings)} 位抖音博主...")
            if data.get("has_more") in (0, False):
                break

        page.listen.stop()
        print(f"✅ 成功获取 {len(followings)} 位抖音关注博主")
        return followings

    def get_all_videos_for_user(self, user):
        page = self.start()
        page.listen.start(self.config.post_api_pattern)
        page.get(user["homepage"])
        time.sleep(self.config.page_load_delay)

        videos_by_id = {}
        empty_rounds = 0

        while True:
            packet = page.listen.wait(timeout=self.config.packet_timeout)
            if packet:
                empty_rounds = 0
                data = self._extract_packet_body(packet)
                for aweme in data.get("aweme_list") or []:
                    video = self._build_video_row(user, aweme)
                    if video:
                        videos_by_id[video["aweme_id"]] = video
                if data.get("has_more") in (0, False):
                    break
            else:
                empty_rounds += 1
                if empty_rounds >= self.config.empty_round_limit:
                    break

            self._scroll_active_containers()
            time.sleep(self.config.scroll_pause + random.uniform(0, 0.5))

        page.listen.stop()
        time.sleep(self.config.user_request_interval + random.uniform(0, 0.5))

        videos = sorted(
            videos_by_id.values(),
            key=lambda item: normalize_timestamp(item.get("publish_timestamp")),
            reverse=True,
        )
        print(f"   ✅ 共获取 {user['nickname']} 的 {len(videos)} 个视频")
        return videos

    def _extract_packet_body(self, packet):
        try:
            body = packet.response.body
            if isinstance(body, dict):
                return body
        except Exception:
            pass
        return {}

    def _scroll_active_containers(self):
        self.start().run_js(
            """
            let scrollables = Array.from(document.querySelectorAll('*')).filter(
                el => el.scrollHeight > el.clientHeight && el.clientHeight > 150 &&
                      getComputedStyle(el).overflowY !== 'hidden'
            );
            scrollables.forEach(el => el.scrollTop += 1600);
            window.scrollBy(0, 1600);
            """
        )

    def _build_video_row(self, user, aweme):
        aweme_id = aweme.get("aweme_id") or ""
        if not aweme_id:
            return None

        video_info = aweme.get("video") or {}
        duration_seconds = normalize_duration_seconds(
            aweme.get("duration")
            or video_info.get("duration")
            or video_info.get("duration_ms")
        )
        publish_timestamp = normalize_timestamp(aweme.get("create_time"))
        statistics = aweme.get("statistics") or {}

        return {
            "uploader_name": user["nickname"],
            "uploader_id": user["sec_uid"],
            "video_title": aweme.get("desc") or "无标题视频",
            "aweme_id": aweme_id,
            "publish_date": timestamp_to_date(publish_timestamp),
            "publish_timestamp": publish_timestamp,
            "duration_text": seconds_to_duration_text(duration_seconds),
            "duration_seconds": duration_seconds,
            "duration_category": categorize_duration(duration_seconds),
            "view_count": parse_view_count(statistics.get("play_count")),
            "video_url": f"https://www.douyin.com/video/{aweme_id}",
        }
