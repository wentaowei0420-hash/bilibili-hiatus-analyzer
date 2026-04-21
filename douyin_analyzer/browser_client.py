import json
import random
import re
import time
from urllib.parse import urlparse

from DrissionPage import ChromiumOptions, ChromiumPage

from bilibili_analyzer.logging_utils import create_progress, smart_print as print, wait_with_progress

from .rate_limiter import DouyinRateLimiter
from .utils import (
    categorize_duration,
    normalize_duration_seconds,
    normalize_timestamp,
    parse_view_count,
    seconds_to_duration_text,
    timestamp_to_date,
)


class DouyinServiceError(RuntimeError):
    pass


class DouyinRateLimitError(RuntimeError):
    pass


class DouyinBrowserClient:
    def __init__(self, config):
        self.config = config
        self.page = None
        self.service_error_streak = 0
        self.rate_limit_streak = 0
        self.rate_limiter = DouyinRateLimiter(config)

    def _minimize_window_if_possible(self):
        if self.page is None:
            return
        try:
            self.page.set.window.mini()
        except Exception:
            pass

    def start(self):
        if self.page is not None:
            return self.page

        co = ChromiumOptions()
        if getattr(self.config, "browser_binary_path", None):
            try:
                co.set_browser_path(str(self.config.browser_binary_path))
            except Exception:
                pass
        co.set_argument("--mute-audio")
        co.set_argument("--start-minimized")
        co.set_user_data_path(str(self.config.browser_user_data_path))
        self.page = ChromiumPage(co)
        self._minimize_window_if_possible()
        return self.page

    def _respect_request_rate(self):
        self.rate_limiter.before_request()

    def _compute_backoff_seconds(self, attempt, base_seconds=None, max_seconds=None):
        return self.rate_limiter.compute_backoff_seconds(attempt, base_seconds, max_seconds)

    def _emit_conservative_mode_notice_if_needed(self):
        if self.rate_limiter.consume_conservative_notice():
            print(
                "⚠️  抖音访问已自动切换到保守模式：将降低请求频率、缩短补采数量，并拉长恢复节奏。"
            )

    def _open_page(self, url, load_delay=None):
        self._respect_request_rate()
        page = self.start()
        page.get(url)
        delay = self.config.page_load_delay if load_delay is None else load_delay
        if delay > 0:
            time.sleep(delay)
        return page

    def close(self):
        if self.page is not None:
            try:
                self.page.quit()
            except Exception:
                pass
            self.page = None

    def restart(self, wait_seconds=0):
        self.close()
        if wait_seconds > 0:
            wait_with_progress(wait_seconds, "抖音浏览器会话重启冷却中")
        page = self.start()
        self._minimize_window_if_possible()
        return page

    def ensure_login(self):
        page = self._open_page(self.config.home_url, self.config.page_load_delay)
        if page.ele("text=登录", timeout=2):
            print("⚠️  尚未登录抖音，请先在浏览器中完成扫码登录。")
            input("登录成功并刷新页面后，按回车继续...")
        print("✅ 抖音登录状态已确认。")

    def _drain_listen_packets(self, timeout, gap=1):
        packets = []
        for step in self.start().listen.steps(timeout=timeout, gap=gap):
            if step is False:
                break
            if isinstance(step, list):
                packets.extend(item for item in step if item)
            elif step:
                packets.append(step)
        return packets

    def get_followings(self):
        page = self.start()
        print("📜 正在抓取抖音关注列表...")
        page.listen.start(self.config.following_api_pattern)
        self._open_page(self.config.self_user_url, self.config.page_load_delay)
        expected_following_count = self._extract_following_count_from_dom()
        if self._page_has_rate_limit():
            raise DouyinRateLimitError("抖音关注列表页触发速率限制")

        try:
            tab = page.ele("text:关注", timeout=3)
            if tab:
                tab.click()
                time.sleep(1)
                if self._page_has_rate_limit():
                    raise DouyinRateLimitError("抖音关注列表页触发速率限制")
        except DouyinRateLimitError:
            raise
        except Exception:
            pass

        try:
            list_tab = page.ele("text:鍒楄〃", timeout=2)
            if list_tab:
                list_tab.click()
                time.sleep(0.8)
        except Exception:
            pass

        followings = []
        seen_sec_uids = set()
        empty_rounds = 0
        stagnant_rounds = 0
        has_more = True

        with create_progress(transient=False) as progress:
            task_id = progress.add_task("抓取抖音关注列表", total=50)
            dynamic_total = max(expected_following_count, 50) if expected_following_count else 50
            progress.update(task_id, total=dynamic_total)
            while has_more and empty_rounds < self.config.empty_round_limit:
                if self._page_has_rate_limit():
                    raise DouyinRateLimitError("抖音关注列表页触发速率限制")
                progress.update(
                    task_id,
                    total=dynamic_total,
                    completed=len(followings),
                    description=f"抓取抖音关注列表 | 已获取 {len(followings)} 位 | 正在等待新数据包",
                )
                self._scroll_active_containers()
                packets = self._drain_listen_packets(timeout=self.config.packet_timeout)
                if not packets:
                    if self._page_has_rate_limit():
                        raise DouyinRateLimitError("抖音关注列表页触发速率限制")
                    empty_rounds += 1
                    progress.update(
                        task_id,
                        total=dynamic_total,
                        completed=len(followings),
                        description=(
                            f"抓取抖音关注列表 | 已获取 {len(followings)} 位 | "
                            f"本轮无新增 ({empty_rounds}/{self.config.empty_round_limit})"
                        ),
                    )
                    continue

                empty_rounds = 0
                new_users = 0
                for packet in packets:
                    data = self._extract_packet_body(packet)
                    if self._packet_has_rate_limit(data):
                        raise DouyinRateLimitError("抖音关注列表接口触发速率限制")
                    for user in data.get("followings") or []:
                        sec_uid = user.get("sec_uid") or ""
                        if not sec_uid or sec_uid in seen_sec_uids:
                            continue
                        seen_sec_uids.add(sec_uid)
                        new_users += 1
                        followings.append(
                            {
                                "nickname": user.get("nickname", "未知UP主"),
                                "remark_name": self._extract_remark_name(user) or "",
                                "sec_uid": sec_uid,
                                "homepage": f"https://www.douyin.com/user/{sec_uid}",
                                "follower_count": self._extract_follower_count(user),
                                "aweme_count": self._extract_aweme_count(user),
                                "total_favorited": self._extract_total_favorited(user),
                                "latest_publish_timestamp": self._extract_latest_publish_timestamp(user),
                            }
                        )

                    if data.get("has_more") in (0, False):
                        has_more = False

                if new_users == 0:
                    stagnant_rounds += 1
                else:
                    stagnant_rounds = 0

                if expected_following_count:
                    dynamic_total = max(dynamic_total, expected_following_count)
                else:
                    dynamic_total = max(len(followings) + 50, dynamic_total)
                progress.update(
                    task_id,
                    total=dynamic_total,
                    completed=len(followings),
                    description=(
                        f"抓取抖音关注列表 | 已获取 {len(followings)} 位 | "
                        f"本轮新增 {new_users} 位"
                    ),
                )

                if stagnant_rounds >= self.config.empty_round_limit:
                    progress.update(
                        task_id,
                        total=dynamic_total,
                        completed=len(followings),
                        description=f"抓取抖音关注列表 | 已获取 {len(followings)} 位 | 连续无新增，准备结束",
                    )
                    break

        page.listen.stop()
        self.rate_limiter.record_success()
        if expected_following_count and len(followings) < expected_following_count:
            raise RuntimeError(
                f"抖音关注列表抓取失败：主页显示关注 {expected_following_count} 位，实际仅抓取到 {len(followings)} 位。"
            )
        if expected_following_count and len(followings) > expected_following_count:
            print(
                f"⚠️  抖音主页显示关注 {expected_following_count} 位，但本轮抓取到 {len(followings)} 位，请检查主页关注数解析是否准确。"
            )
        print(f"✅ 成功获取 {len(followings)} 位抖音关注博主")
        return followings

    def get_all_videos_for_user(self, user):
        return self._collect_videos_for_user(user, limit=None)

    def get_recent_videos_for_user(self, user, limit):
        return self._collect_videos_for_user(user, limit=max(1, int(limit or 1)))

    def _collect_videos_for_user(self, user, limit=None):
        page = self.start()
        for attempt in range(1, self.config.video_page_retry_count + 1):
            videos_by_id = {}
            empty_rounds = 0
            stagnant_rounds = 0
            page.listen.start([self.config.post_api_pattern, self.config.video_detail_api_pattern])
            try:
                self._open_page(user["homepage"], self.config.video_page_load_delay)
                if self._page_has_rate_limit():
                    raise RuntimeError("rate_limit")
                if self._page_has_service_error():
                    raise RuntimeError("service_error")
                if not user.get("total_favorited"):
                    total_favorited = self._extract_total_favorited_from_dom()
                    if total_favorited:
                        user["total_favorited"] = total_favorited

                while empty_rounds < self.config.video_empty_round_limit:
                    if self._page_has_rate_limit():
                        raise RuntimeError("rate_limit")
                    self._scroll_video_page_fast()
                    packets = self._drain_listen_packets(timeout=self.config.video_packet_timeout)
                    if packets:
                        empty_rounds = 0
                        new_videos = 0
                        should_stop = False
                        for packet in packets:
                            data = self._extract_packet_body(packet)
                            if self._packet_has_rate_limit(data):
                                raise RuntimeError("rate_limit")
                            if self._packet_has_service_error(data):
                                raise RuntimeError("service_error")
                            self._update_user_profile_from_packet(user, data)
                            for aweme in self._extract_awemes_from_packet_body(data):
                                video = self._build_video_row(user, aweme)
                                if video and video["aweme_id"] not in videos_by_id:
                                    new_videos += 1
                                    videos_by_id[video["aweme_id"]] = video
                                elif video:
                                    videos_by_id[video["aweme_id"]] = video
                            if limit and len(videos_by_id) >= limit:
                                should_stop = True
                                break
                            if data.get("has_more") in (0, False):
                                should_stop = True
                                break

                        if new_videos == 0:
                            stagnant_rounds += 1
                        else:
                            stagnant_rounds = 0

                        if should_stop or stagnant_rounds >= self.config.video_empty_round_limit:
                            break
                    else:
                        if self._page_has_rate_limit():
                            raise RuntimeError("rate_limit")
                        if self._page_has_service_error():
                            raise RuntimeError("service_error")
                        empty_rounds += 1
                        if empty_rounds >= self.config.video_empty_round_limit:
                            break

                videos = sorted(
                    videos_by_id.values(),
                    key=lambda item: normalize_timestamp(item.get("publish_timestamp")),
                    reverse=True,
                )
                if (not videos or (limit and len(videos) < limit)) and self.rate_limiter.current_fallback_max_ids() > 0:
                    fallback_videos = self._collect_videos_by_browser_fallback(
                        user,
                        limit,
                        existing_ids=set(videos_by_id),
                    )
                    for video in fallback_videos:
                        videos_by_id[video["aweme_id"]] = video
                    videos = sorted(
                        videos_by_id.values(),
                        key=lambda item: normalize_timestamp(item.get("publish_timestamp")),
                        reverse=True,
                    )
                if limit:
                    videos = videos[:limit]
                self.service_error_streak = 0
                self.rate_limit_streak = 0
                self.rate_limiter.record_success()
                time.sleep(self.rate_limiter.scaled_seconds(self.config.user_request_interval) + random.uniform(0, 0.2))
                print(f"   ✅ 共获取 {user['nickname']} 的 {len(videos)} 个视频")
                return videos
            except RuntimeError as exc:
                if str(exc) == "rate_limit":
                    self.rate_limit_streak += 1
                    self.rate_limiter.record_rate_limit()
                    self._emit_conservative_mode_notice_if_needed()
                    if attempt >= self.config.video_page_retry_count:
                        fallback_videos = self._collect_videos_by_browser_fallback(user, limit)
                        if fallback_videos:
                            return fallback_videos[:limit] if limit else fallback_videos
                        raise DouyinRateLimitError("页面触发速率限制，重试后仍无法恢复")

                    wait_seconds = self.config.rate_limit_retry_wait + self._compute_backoff_seconds(attempt)
                    if self.rate_limit_streak >= 2:
                        wait_seconds += self.config.rate_limit_long_cooldown
                    self._recover_from_rate_limit(user, wait_seconds)
                    continue

                if str(exc) != "service_error":
                    raise
                self.service_error_streak += 1
                self.rate_limiter.record_service_error()
                self._emit_conservative_mode_notice_if_needed()
                if attempt >= self.config.video_page_retry_count:
                    fallback_videos = self._collect_videos_by_browser_fallback(user, limit)
                    if fallback_videos:
                        return fallback_videos[:limit] if limit else fallback_videos
                    raise DouyinServiceError("页面出现服务异常，重试后仍无法恢复")

                wait_seconds = self.config.service_error_retry_wait + self._compute_backoff_seconds(attempt)
                if self.service_error_streak >= 3:
                    wait_seconds += self.config.service_error_long_cooldown
                self._recover_from_service_error(user, wait_seconds)
            finally:
                try:
                    page.listen.stop()
                except Exception:
                    pass

    def _extract_packet_body(self, packet):
        try:
            body = packet.response.body
            if isinstance(body, dict):
                return body
        except Exception:
            pass
        return {}

    def _page_body_text(self):
        try:
            return self.start().run_js("return document.body ? document.body.innerText : '';") or ""
        except Exception:
            return ""

    @staticmethod
    def _extract_awemes_from_packet_body(data):
        if not isinstance(data, dict):
            return []
        aweme_list = data.get("aweme_list")
        if isinstance(aweme_list, list):
            return aweme_list

        for key in ["aweme_detail", "detail", "item", "aweme"]:
            candidate = data.get(key)
            if isinstance(candidate, dict) and candidate.get("aweme_id"):
                return [candidate]

        inner_data = data.get("data")
        if isinstance(inner_data, dict):
            inner_list = inner_data.get("aweme_list")
            if isinstance(inner_list, list):
                return inner_list
            for key in ["aweme_detail", "detail", "item", "aweme"]:
                candidate = inner_data.get(key)
                if isinstance(candidate, dict) and candidate.get("aweme_id"):
                    return [candidate]
        return []

    def _collect_visible_aweme_ids_from_dom(self, limit=None):
        result = self.start().run_js(
            """
            const anchors = Array.from(document.querySelectorAll('a[href*="/video/"]'));
            const ids = [];
            const seen = new Set();
            for (const anchor of anchors) {
              const href = anchor.getAttribute('href') || '';
              const match = href.match(/\\/video\\/(\\d+)/);
              if (!match) continue;
              const awemeId = match[1];
              if (seen.has(awemeId)) continue;
              const rect = anchor.getBoundingClientRect();
              if (rect.width <= 0 || rect.height <= 0) continue;
              seen.add(awemeId);
              ids.push(awemeId);
            }
            return ids;
            """
        )
        aweme_ids = result if isinstance(result, list) else []
        if limit:
            aweme_ids = aweme_ids[:limit]
        return [str(aweme_id).strip() for aweme_id in aweme_ids if str(aweme_id).strip()]

    def _fetch_video_detail_by_aweme_id(self, user, aweme_id):
        page = self.start()
        page.listen.start([self.config.video_detail_api_pattern, self.config.post_api_pattern])
        try:
            self._open_page(f"https://www.douyin.com/video/{aweme_id}", self.config.video_page_load_delay)
            packets = self._drain_listen_packets(timeout=self.config.video_packet_timeout)
            for packet in packets:
                data = self._extract_packet_body(packet)
                self._update_user_profile_from_packet(user, data)
                for aweme in self._extract_awemes_from_packet_body(data):
                    if str(aweme.get("aweme_id") or "") == str(aweme_id):
                        return self._build_video_row(user, aweme)
        finally:
            try:
                page.listen.stop()
            except Exception:
                pass
        return None

    def _collect_videos_by_browser_fallback(self, user, limit=None, existing_ids=None):
        existing_ids = {str(item) for item in (existing_ids or set()) if str(item)}
        max_ids = self.rate_limiter.current_fallback_max_ids()
        if limit:
            max_ids = min(max_ids, int(limit))
        candidate_ids = [
            aweme_id
            for aweme_id in self._collect_visible_aweme_ids_from_dom(limit=max_ids)
            if aweme_id not in existing_ids
        ]
        recovered = []
        for index, aweme_id in enumerate(candidate_ids, 1):
            try:
                video = self._fetch_video_detail_by_aweme_id(user, aweme_id)
            except Exception:
                video = None
            if video:
                recovered.append(video)
            if limit and len(recovered) >= limit:
                break
            if index < len(candidate_ids):
                time.sleep(self.rate_limiter.scaled_seconds(min(self.config.user_request_interval, 0.8)) + random.uniform(0.1, 0.3))
        if recovered:
            print(f"   ↩ 浏览器兜底补回 {user['nickname']} 的 {len(recovered)} 个视频详情")
        return recovered

    def _page_has_service_error(self):
        try:
            body_text = self.start().run_js("return document.body ? document.body.innerText : '';") or ""
        except Exception:
            return False
        return "服务异常" in body_text and "拉取数据" in body_text

    def _page_has_rate_limit(self):
        try:
            body_text = self.start().run_js("return document.body ? document.body.innerText : '';") or ""
        except Exception:
            return False
        return "触发速率限制" in body_text

    @staticmethod
    def _packet_has_service_error(data):
        if not isinstance(data, dict):
            return False
        text = str(data)
        return "服务异常" in text and "拉取数据" in text

    @staticmethod
    def _packet_has_rate_limit(data):
        if not isinstance(data, dict):
            return False
        text = str(data)
        return "触发速率限制" in text

    def _recover_from_service_error(self, user, wait_seconds):
        if self.service_error_streak >= 2:
            page = self.restart(wait_seconds)
        else:
            wait_with_progress(wait_seconds, f"抖音服务异常恢复冷却：{user['nickname']}")
            page = self.start()
        try:
            self._respect_request_rate()
            page.refresh()
            time.sleep(self.config.video_page_load_delay + 1)
            if self._page_has_service_error():
                self._open_page(user["homepage"], self.config.video_page_load_delay + 1)
        except Exception:
            self._open_page(user["homepage"], self.config.video_page_load_delay + 1)

    def _recover_from_rate_limit(self, user, wait_seconds):
        if self.rate_limit_streak >= 2:
            page = self.restart(wait_seconds)
        else:
            wait_with_progress(wait_seconds, f"抖音速率限制冷却：{user['nickname']}")
            page = self.start()
        try:
            self._open_page(user["homepage"], self.config.video_page_load_delay + 2)
        except Exception:
            page = self.restart(wait_seconds)
            self._open_page(user["homepage"], self.config.video_page_load_delay + 2)

    def _scroll_active_containers(self):
        self.start().run_js(
            """
            const scoreScrollable = (el) => {
                const style = getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return -999;
                if (el.scrollHeight <= el.clientHeight || el.clientHeight <= 150) return -999;
                if (style.overflowY === 'hidden') return -999;
                const rect = el.getBoundingClientRect();
                const text = (el.innerText || '').slice(0, 600);
                let score = 0;
                if (text.includes('搜索用户名称或抖音号')) score += 14;
                if (text.includes('综合排序')) score += 8;
                if (text.includes('列表')) score += 6;
                if (text.includes('正在直播')) score -= 6;
                if (rect.left < window.innerWidth * 0.45) score += 4;
                if (rect.width > 180 && rect.width < window.innerWidth * 0.5) score += 3;
                if (rect.height > 280) score += 2;
                return score;
            };

            const ranked = Array.from(document.querySelectorAll('*'))
                .map(el => ({ el, score: scoreScrollable(el) }))
                .filter(item => item.score > -999)
                .sort((a, b) => b.score - a.score);

            if (ranked.length && ranked[0].score > 0) {
                ranked[0].el.scrollTop += 1600;
            } else {
                let scrollables = Array.from(document.querySelectorAll('*')).filter(
                    el => el.scrollHeight > el.clientHeight && el.clientHeight > 150 &&
                          getComputedStyle(el).overflowY !== 'hidden'
                );
                scrollables.forEach(el => el.scrollTop += 1600);
            }
            window.scrollBy(0, 320);
            """
        )

    def _scroll_video_page_fast(self):
        for _ in range(self.config.video_scroll_steps_per_round):
            self.start().run_js(
                f"""
                let distance = {self.config.video_scroll_distance};
                let scrollables = Array.from(document.querySelectorAll('*')).filter(
                    el => el.scrollHeight > el.clientHeight && el.clientHeight > 150 &&
                          getComputedStyle(el).overflowY !== 'hidden'
                );
                scrollables.forEach(el => el.scrollTop += distance);
                window.scrollBy(0, distance);
                """
            )
            time.sleep(self.config.video_scroll_pause + random.uniform(0, 0.05))

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
            "like_count": parse_view_count(statistics.get("digg_count")),
            "view_count": parse_view_count(statistics.get("play_count")),
            "video_url": f"https://www.douyin.com/video/{aweme_id}",
        }

    def _update_user_profile_from_packet(self, user, data):
        follower_count = self._extract_follower_count(data)
        if follower_count:
            user["follower_count"] = follower_count

        aweme_count = self._extract_aweme_count(data)
        if aweme_count is not None:
            user["aweme_count"] = aweme_count

        total_favorited = self._extract_total_favorited(data)
        if total_favorited:
            user["total_favorited"] = total_favorited

        latest_publish_timestamp = self._extract_latest_publish_timestamp(data)
        if latest_publish_timestamp:
            user["latest_publish_timestamp"] = latest_publish_timestamp

        remark_name = self._extract_remark_name(data)
        if remark_name:
            user["remark_name"] = remark_name

    def _extract_follower_count(self, data):
        if not isinstance(data, dict):
            return None
        return self._find_numeric_value_from_profile_candidates(
            data,
            [
                "mplatform_followers_count",
                "follower_count",
                "fans_count",
                "followerCount",
                "fansCount",
            ],
        )

    def _extract_aweme_count(self, data):
        if not isinstance(data, dict):
            return None
        return self._find_numeric_value_from_profile_candidates(
            data,
            [
                "aweme_count",
                "awemeCount",
                "media_count",
                "mediaCount",
                "video_count",
                "videoCount",
            ],
        )

    def _extract_total_favorited(self, data):
        if not isinstance(data, dict):
            return None
        return self._find_numeric_value_from_profile_candidates(
            data,
            [
                "total_favorited",
                "totalFavorited",
                "total_favorited_count",
                "totalFavoritedCount",
                "favorited_count",
                "favoritedCount",
                "liked_count",
                "likedCount",
                "total_liked_count",
                "totalLikedCount",
                "favoriting_count",
                "favoritingCount",
            ],
        )

    def _extract_total_favorited_from_dom(self):
        body_text = self._page_body_text()
        match = re.search(r"\u83b7\u8d5e\s*([\d.]+\s*(?:\u4ebf|\u4e07|\u5343|w)?)", body_text, re.I)
        return parse_view_count(match.group(1)) if match else 0

    def _extract_following_count_from_dom(self):
        body_text = self._page_body_text()
        match = re.search(r"\u5173\u6ce8\s*([\d.]+\s*(?:\u4ebf|\u4e07|\u5343|w)?)", body_text, re.I)
        return parse_view_count(match.group(1)) if match else 0

    def _extract_latest_publish_timestamp(self, data):
        if not isinstance(data, dict):
            return 0
        timestamp = self._find_timestamp_value(
            data,
            {
                "latest_aweme_time",
                "latestAwemeTime",
                "last_aweme_time",
                "lastAwemeTime",
                "latest_publish_time",
                "latestPublishTime",
                "publish_time",
                "publishTime",
                "aweme_create_time",
                "awemeCreateTime",
                "item_create_time",
                "itemCreateTime",
                "create_time",
            },
        )
        return normalize_timestamp(timestamp)

    def _extract_remark_name(self, data):
        if not isinstance(data, dict):
            return ""
        return self._find_text_value_from_profile_candidates(
            data,
            [
                "remark_name",
                "remarkName",
                "remark",
                "mark_name",
                "markName",
                "note",
                "note_name",
                "noteName",
                "follow_remark_name",
                "followRemarkName",
            ],
        )

    def _find_numeric_value(self, data, key_candidates):
        if isinstance(data, dict):
            for key, value in data.items():
                if key in key_candidates:
                    parsed = parse_view_count(value)
                    if parsed:
                        return parsed
            for value in data.values():
                found = self._find_numeric_value(value, key_candidates)
                if found is not None:
                    return found
        elif isinstance(data, list):
            for item in data:
                found = self._find_numeric_value(item, key_candidates)
                if found is not None:
                    return found
        return None

    def _find_numeric_value_from_profile_candidates(self, data, key_candidates):
        for candidate in self._iter_profile_candidate_dicts(data):
            for key in key_candidates:
                parsed = parse_view_count(candidate.get(key))
                if parsed:
                    return parsed
        return None

    def _find_timestamp_value(self, data, key_candidates):
        if isinstance(data, dict):
            for key, value in data.items():
                if key in key_candidates:
                    normalized = normalize_timestamp(value)
                    if normalized:
                        return normalized
            for value in data.values():
                found = self._find_timestamp_value(value, key_candidates)
                if found:
                    return found
        elif isinstance(data, list):
            for item in data:
                found = self._find_timestamp_value(item, key_candidates)
                if found:
                    return found
        return 0

    def _find_text_value(self, data, key_candidates):
        if isinstance(data, dict):
            for key, value in data.items():
                if key in key_candidates and isinstance(value, str):
                    text = value.strip()
                    if text:
                        return text
            for value in data.values():
                found = self._find_text_value(value, key_candidates)
                if found:
                    return found
        elif isinstance(data, list):
            for item in data:
                found = self._find_text_value(item, key_candidates)
                if found:
                    return found
        return ""

    def _find_text_value_from_profile_candidates(self, data, key_candidates):
        for candidate in self._iter_profile_candidate_dicts(data):
            for key in key_candidates:
                value = candidate.get(key)
                if isinstance(value, str):
                    text = value.strip()
                    if text:
                        return text
        return ""

    @staticmethod
    def _iter_profile_candidate_dicts(data):
        if not isinstance(data, dict):
            return []

        preferred_child_keys = [
            "user",
            "user_info",
            "author",
            "author_user_info",
            "follow_user",
            "follow_info",
            "profile",
            "user_detail",
            "user_data",
            "card_data",
            "sec_user",
        ]

        candidates = []
        seen_ids = set()

        def add_candidate(candidate):
            if not isinstance(candidate, dict):
                return
            object_id = id(candidate)
            if object_id in seen_ids:
                return
            seen_ids.add(object_id)
            candidates.append(candidate)

        add_candidate(data)
        for key in preferred_child_keys:
            value = data.get(key)
            if isinstance(value, dict):
                add_candidate(value)
            elif isinstance(value, list):
                for item in value:
                    add_candidate(item)

        # 一些接口会把真正的用户对象包在 data/list 这种通用键下，这里只展开一层，
        # 避免像以前那样把整包响应里的无关统计值误识别成粉丝数。
        for key in ["data", "list"]:
            value = data.get(key)
            if isinstance(value, dict):
                add_candidate(value)
                for child_key in preferred_child_keys:
                    child = value.get(child_key)
                    if isinstance(child, dict):
                        add_candidate(child)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        add_candidate(item)
                        for child_key in preferred_child_keys:
                            child = item.get(child_key)
                            if isinstance(child, dict):
                                add_candidate(child)

        return candidates

    def unfollow_users_by_homepages(self, homepages, on_unfollowed=None):
        normalized_homepages = []
        seen_homepages = set()
        for homepage in homepages:
            normalized = self.normalize_homepage_url(homepage)
            if not normalized or normalized in seen_homepages:
                continue
            seen_homepages.add(normalized)
            normalized_homepages.append(normalized)

        results = []
        consecutive_failures = 0
        with create_progress(transient=True) as progress:
            task_id = progress.add_task("执行抖音取消关注", total=len(normalized_homepages))
            for index, homepage in enumerate(normalized_homepages, 1):
                progress.update(
                    task_id,
                    description=f"执行抖音取消关注 ({index}/{len(normalized_homepages)})",
                )
                try:
                    result = self.unfollow_user_by_homepage(homepage)
                except Exception as exc:
                    result = {
                        "homepage": homepage,
                        "status": "failed",
                        "message": str(exc),
                    }
                results.append(result)
                if result.get("status") == "unfollowed" and callable(on_unfollowed):
                    try:
                        on_unfollowed(homepage)
                    except Exception as exc:
                        print(f"   ⚠️  已取消关注，但更新名单文件失败: {exc}")

                status = result.get("status")
                if status in {"failed", "unknown"}:
                    consecutive_failures += 1
                    cooldown = self.rate_limiter.scaled_seconds(self.config.unfollow_failure_cooldown)
                    print(f"   ⚠️  检测到异常结果: {result.get('message', '未知原因')}")
                    wait_with_progress(cooldown, "抖音取消关注异常冷却中")
                else:
                    consecutive_failures = 0
                    base_interval = max(
                        self.config.unfollow_interval_seconds,
                        self.config.user_request_interval,
                        1.0,
                    )
                    time.sleep(self.rate_limiter.scaled_seconds(base_interval) + random.uniform(0.2, 0.8))

                if (
                    self.config.unfollow_batch_size > 0
                    and index % self.config.unfollow_batch_size == 0
                    and index < len(normalized_homepages)
                ):
                    cooldown = self.rate_limiter.scaled_seconds(self.config.unfollow_batch_cooldown)
                    wait_with_progress(cooldown, "抖音取消关注批次冷却中")

                if (
                    self.config.unfollow_restart_interval > 0
                    and index % self.config.unfollow_restart_interval == 0
                    and index < len(normalized_homepages)
                ):
                    print("   🔄 为降低风控概率，正在重启浏览器会话...")
                    self.restart(5)

                if consecutive_failures >= 2:
                    extra_cooldown = self.rate_limiter.scaled_seconds(self.config.unfollow_failure_cooldown)
                    print("   ⚠️  连续异常较多，准备额外冷却并重启会话...")
                    self.restart(extra_cooldown)
                    consecutive_failures = 0

                progress.advance(task_id)
        return results

    def unfollow_user_by_homepage(self, homepage):
        homepage = self.normalize_homepage_url(homepage)
        if not homepage:
            return {"homepage": homepage, "status": "invalid", "message": "主页链接无效"}

        self._open_page(homepage, self.config.page_load_delay + 1)

        status = self._detect_profile_follow_status()
        if status == "not_following":
            print("   ℹ️  当前未关注，跳过。")
            return {"homepage": homepage, "status": "skipped", "message": "当前未关注"}

        if status != "following":
            print("   ⚠️  未能稳定识别关注状态，跳过该博主。")
            return {"homepage": homepage, "status": "unknown", "message": "未能识别关注状态"}

        if not self._click_profile_action_button(["已关注", "互相关注", "相互关注"]):
            return {"homepage": homepage, "status": "failed", "message": "未找到已关注按钮"}

        time.sleep(2)

        final_status = self._detect_profile_follow_status()
        if final_status == "not_following":
            print("   ✅ 已成功取消关注。")
            return {"homepage": homepage, "status": "unfollowed", "message": "已取消关注"}

        print("   ⚠️  点击后仍然显示已关注，可能未触发取消。")
        return {"homepage": homepage, "status": "failed", "message": "取消后状态未变化"}

    @staticmethod
    def normalize_homepage_url(homepage):
        homepage = (homepage or "").strip()
        if not homepage:
            return ""
        if not homepage.startswith(("http://", "https://")):
            return homepage

        parsed = urlparse(homepage)
        if not parsed.netloc:
            return ""
        normalized_path = parsed.path.rstrip("/")
        if not normalized_path:
            return ""
        return f"https://{parsed.netloc}{normalized_path}"

    def _detect_profile_follow_status(self):
        page = self.start()
        result = page.run_js(
            """
            const candidates = Array.from(document.querySelectorAll('button, div, span, a'));
            const items = candidates
              .map(el => {
                const text = (el.innerText || el.textContent || '').trim();
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return { text, rect, display: style.display, visibility: style.visibility };
              })
              .filter(item =>
                item.text &&
                item.display !== 'none' &&
                item.visibility !== 'hidden' &&
                item.rect.width > 48 &&
                item.rect.height > 24 &&
                item.rect.top >= 0 &&
                item.rect.top < 420 &&
                item.rect.left > window.innerWidth * 0.5
              );

            const texts = items.map(item => item.text);
            if (texts.some(text => ['已关注', '互相关注', '相互关注'].includes(text))) {
              return 'following';
            }
            if (texts.some(text => text === '关注')) {
              return 'not_following';
            }
            return 'unknown';
            """
        )
        return result if isinstance(result, str) else "unknown"

    def _click_profile_action_button(self, text_candidates):
        page = self.start()
        encoded_candidates = json.dumps(list(text_candidates), ensure_ascii=False)
        script = """
            const texts = __TEXTS__;
            const candidates = Array.from(document.querySelectorAll('button, div, span, a'));
            const target = candidates.find(el => {
              const text = (el.innerText || el.textContent || '').trim();
              const rect = el.getBoundingClientRect();
              const style = window.getComputedStyle(el);
              return texts.includes(text) &&
                style.display !== 'none' &&
                style.visibility !== 'hidden' &&
                rect.width > 48 &&
                rect.height > 24 &&
                rect.top >= 0 &&
                rect.top < 420 &&
                rect.left > window.innerWidth * 0.5;
            });
            if (!target) return false;
            target.click();
            return true;
            """
        result = page.run_js(script.replace("__TEXTS__", encoded_candidates))
        return bool(result)
