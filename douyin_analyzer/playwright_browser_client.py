import random
import time

from loguru import logger

from bilibili_analyzer.logging_utils import create_progress, smart_print as print, wait_with_progress

from .browser_client import DouyinBrowserClient, DouyinRateLimitError, DouyinServiceError
from .utils import parse_view_count

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - optional dependency
    PlaywrightError = Exception
    PlaywrightTimeoutError = Exception
    sync_playwright = None


class PlaywrightDouyinBrowserClient(DouyinBrowserClient):
    def __init__(self, config):
        super().__init__(config)
        self._playwright = None
        self.context = None

    def _minimize_window_if_possible(self):
        if self.context is None or self.page is None:
            return
        try:
            session = self.context.new_cdp_session(self.page)
            window_info = session.send("Browser.getWindowForTarget")
            window_id = window_info.get("windowId")
            if window_id is not None:
                session.send(
                    "Browser.setWindowBounds",
                    {"windowId": window_id, "bounds": {"windowState": "minimized"}},
                )
        except Exception:
            pass

    def start(self):
        if self.page is not None:
            return self.page

        if sync_playwright is None:
            raise RuntimeError(
                "Playwright backend requested, but playwright is not installed. "
                "Run `pip install playwright` first."
            )

        self._playwright = sync_playwright().start()
        launch_kwargs = {
            "user_data_dir": str(self.config.browser_user_data_path),
            "headless": False,
            "args": ["--mute-audio", "--start-minimized"],
        }
        if getattr(self.config, "browser_binary_path", None):
            launch_kwargs["executable_path"] = str(self.config.browser_binary_path)
        else:
            channel_map = {"edge": "msedge", "chrome": "chrome"}
            channel = channel_map.get((self.config.browser_name or "").strip().lower())
            if channel:
                launch_kwargs["channel"] = channel

        self.context = self._playwright.chromium.launch_persistent_context(**launch_kwargs)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self._minimize_window_if_possible()
        return self.page

    def close(self):
        if self.context is not None:
            try:
                self.context.close()
            except Exception:
                pass
        self.context = None
        self.page = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._playwright = None

    def _open_page(self, url, load_delay=None):
        self._respect_request_rate()
        page = self.start()
        page.goto(url, wait_until="domcontentloaded")
        delay = self.config.page_load_delay if load_delay is None else load_delay
        if delay > 0:
            time.sleep(delay)
        return page

    def _current_url(self):
        try:
            return str(self.start().url or "")
        except Exception:
            return ""

    def _native_click_at(self, x, y):
        try:
            x = float(x)
            y = float(y)
        except (TypeError, ValueError):
            return False
        try:
            self.start().mouse.click(x, y)
            logger.info("Douyin native click dispatched | backend=playwright | x={} | y={}", round(x), round(y))
            return True
        except Exception as exc:
            logger.warning(
                "Douyin native click failed | backend=playwright | x={} | y={} | error={}",
                x,
                y,
                exc,
            )
            return False

    def ensure_login(self):
        page = self._open_page(self.config.home_url, self.config.page_load_delay)
        try:
            if page.locator("text=登录").first.is_visible(timeout=2000):
                print("⚠️  尚未登录抖音，请先在浏览器中完成扫码登录。")
                input("登录成功并刷新页面后，按回车继续...")
        except PlaywrightTimeoutError:
            pass
        except Exception:
            pass
        print("✅ 抖音登录状态已确认。")

    def _create_response_collector(self, patterns):
        page = self.start()
        collected = []

        def handle_response(response):
            try:
                url = response.url or ""
                if not any(pattern in url for pattern in patterns if pattern):
                    return
                body = response.json()
                if isinstance(body, dict):
                    collected.append(body)
            except Exception:
                return

        page.on("response", handle_response)
        return collected, handle_response

    def _create_following_response_collector(self):
        page = self.start()
        collected = []
        stats = {
            "skipped_non_primary": 0,
            "accepted_unrecognized": 0,
            "skipped_samples": [],
            "accepted_samples": [],
        }

        def handle_response(response):
            try:
                url = response.url or ""
                lowered = str(url).lower()
                if "following/list" not in lowered:
                    return
                if self._is_blocked_following_list_url(url):
                    stats["skipped_non_primary"] += 1
                    if len(stats["skipped_samples"]) < 5:
                        stats["skipped_samples"].append(url)
                    return
                body = response.json()
                body_has_followings = self._packet_has_followings(body)
                if not self._is_primary_following_list_url(url) and not body_has_followings:
                    stats["skipped_non_primary"] += 1
                    if len(stats["skipped_samples"]) < 5:
                        stats["skipped_samples"].append(url)
                    return
                if not self._is_primary_following_list_url(url) and body_has_followings:
                    stats["accepted_unrecognized"] += 1
                    if len(stats["accepted_samples"]) < 5:
                        stats["accepted_samples"].append(url)
                if isinstance(body, dict):
                    collected.append(body)
            except Exception:
                return

        page.on("response", handle_response)
        return collected, handle_response, stats

    def _remove_response_collector(self, handler):
        try:
            self.start().remove_listener("response", handler)
        except Exception:
            pass

    def _drain_response_collector(self, collected, timeout):
        deadline = time.monotonic() + timeout
        last_count = len(collected)
        stable_rounds = 0
        while time.monotonic() < deadline:
            time.sleep(0.15)
            current_count = len(collected)
            if current_count == last_count:
                stable_rounds += 1
                if current_count > 0 and stable_rounds >= 2:
                    break
            else:
                stable_rounds = 0
                last_count = current_count
        packets = list(collected)
        collected.clear()
        return packets

    def _page_body_text(self):
        try:
            return self.start().locator("body").inner_text(timeout=1500) or ""
        except Exception:
            return ""

    def _page_has_service_error(self):
        body_text = self._page_body_text()
        return "服务异常" in body_text and "拉取数据" in body_text

    def _page_has_rate_limit(self):
        body_text = self._page_body_text()
        return "触发速率限制" in body_text

    def _extract_total_favorited_from_dom(self):
        try:
            body_text = self.start().locator("body").inner_text(timeout=1500) or ""
        except Exception:
            return 0
        import re

        match = re.search(r"\u83b7\u8d5e\s*([\d.]+\s*(?:\u4ebf|\u4e07|\u5343|w)?)", body_text, re.I)
        return parse_view_count(match.group(1)) if match else 0

    def refresh_user_profile_from_homepage(self, user):
        collected, handler = self._create_response_collector(
            [self.config.post_api_pattern, self.config.video_detail_api_pattern]
        )
        try:
            self._open_page(user["homepage"], self.config.video_page_load_delay)
            if self._page_has_rate_limit():
                raise DouyinRateLimitError("抖音主页触发速率限制")
            if self._page_has_service_error():
                raise DouyinServiceError("抖音主页出现服务异常")

            total_favorited = self._extract_total_favorited_from_dom()
            if total_favorited:
                user["total_favorited"] = total_favorited

            packets = self._drain_response_collector(collected, self.config.video_packet_timeout)
            for data in packets:
                if self._packet_has_rate_limit(data):
                    raise DouyinRateLimitError("抖音主页接口触发速率限制")
                if self._packet_has_service_error(data):
                    raise DouyinServiceError("抖音主页接口出现服务异常")
                self._update_user_profile_from_packet(user, data)

            total_favorited = self._extract_total_favorited_from_dom()
            if total_favorited:
                user["total_favorited"] = total_favorited

            time.sleep(self.rate_limiter.scaled_seconds(self.config.user_request_interval) + random.uniform(0, 0.2))
            return user
        finally:
            self._remove_response_collector(handler)

    def get_followings(self):
        print("📜 正在抓取抖音关注列表...")
        collected, handler, collector_stats = self._create_following_response_collector()
        self._open_page(self.config.self_user_url, self.config.page_load_delay)
        expected_following_count = self._extract_following_count_from_dom()
        print(
            f"🧭 关注数量校验基准 | 主页显示={expected_following_count or '未知'} | "
            f"监听接口={self.config.following_api_pattern}"
        )
        if self._page_has_rate_limit():
            raise DouyinRateLimitError("抖音关注列表页触发速率限制")

        logger.info("Douyin following route direct open | url=https://www.douyin.com/follow | backend=playwright")
        if not self._open_following_route_fallback("https://www.douyin.com/follow"):
            raise RuntimeError("抖音关注列表页未成功打开：直接跳转 /follow 后没有检测到关注列表面板。")
        if self._page_has_rate_limit():
            raise DouyinRateLimitError("抖音关注列表页触发速率限制")

        try:
            list_tab = self.start().locator("text=\u5217\u8868").first
            if list_tab.is_visible(timeout=2000):
                list_tab.click()
                time.sleep(0.8)
        except Exception:
            pass

        self._focus_following_list_after_live()

        try:
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

                    if not followings:
                        self._focus_following_list_after_live()
                    self._scroll_active_containers()
                    packets = self._drain_response_collector(collected, self.config.packet_timeout)
                    if not packets:
                        empty_rounds += 1
                        progress.update(
                            task_id,
                            total=dynamic_total,
                            completed=len(followings),
                            description=f"抓取抖音关注列表 | 已获取 {len(followings)} 位 | 本轮无新增 ({empty_rounds}/{self.config.empty_round_limit})",
                        )
                        continue

                    empty_rounds = 0
                    new_users = 0
                    for data in packets:
                        if self._packet_has_rate_limit(data):
                            raise DouyinRateLimitError("抖音关注列表接口触发速率限制")
                        for user in self._extract_following_users(data):
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

                    stagnant_rounds = stagnant_rounds + 1 if new_users == 0 else 0
                    if expected_following_count:
                        dynamic_total = max(dynamic_total, expected_following_count)
                    else:
                        dynamic_total = max(dynamic_total, len(followings) + 50)
                    progress.update(
                        task_id,
                        total=dynamic_total,
                        completed=len(followings),
                        description=f"抓取抖音关注列表 | 已获取 {len(followings)} 位 | 本轮新增 {new_users} 位",
                    )
                    if stagnant_rounds >= self.config.empty_round_limit:
                        break

            self.rate_limiter.record_success()
            print(
                f"🧭 关注列表抓取汇总 | 主页显示={expected_following_count or '未知'} | "
                f"主接口去重后={len(followings)} | "
                f"过滤非主包={collector_stats.get('skipped_non_primary', 0)} | "
                f"结构兜底接受={collector_stats.get('accepted_unrecognized', 0)}"
            )
            logger.info(
                "Douyin followings packet summary | backend=playwright | expected={} | collected={} | skipped={} | accepted_by_body={} | skipped_samples={} | accepted_samples={}",
                expected_following_count,
                len(followings),
                collector_stats.get("skipped_non_primary", 0),
                collector_stats.get("accepted_unrecognized", 0),
                collector_stats.get("skipped_samples", []),
                collector_stats.get("accepted_samples", []),
            )
            if collector_stats.get("skipped_non_primary"):
                print(
                    f"🧹 已过滤 {collector_stats['skipped_non_primary']} 个非主关注列表数据包（如直播关注流）。"
                )
            if expected_following_count and len(followings) < expected_following_count:
                raise RuntimeError(
                    f"抖音关注列表抓取失败：主页显示关注 {expected_following_count} 位，实际仅抓取到 {len(followings)} 位。"
                )
            if expected_following_count and len(followings) > expected_following_count:
                raise RuntimeError(
                    f"抖音关注列表抓取异常：主页显示关注 {expected_following_count} 位，但主接口抓取到 {len(followings)} 位。"
                )
            print(f"✅ 成功获取 {len(followings)} 位抖音关注博主")
            return followings
        finally:
            self._remove_response_collector(handler)

    def _collect_videos_for_user(self, user, limit=None):
        for attempt in range(1, self.config.video_page_retry_count + 1):
            collected, handler = self._create_response_collector(
                [self.config.post_api_pattern, self.config.video_detail_api_pattern]
            )
            try:
                self._open_page(user["homepage"], self.config.video_page_load_delay)
                if self._page_has_rate_limit():
                    raise RuntimeError("rate_limit")
                if self._page_has_service_error():
                    raise RuntimeError("service_error")
                total_favorited = self._extract_total_favorited_from_dom()
                if total_favorited:
                    user["total_favorited"] = total_favorited

                videos_by_id = {}
                empty_rounds = 0
                stagnant_rounds = 0

                while empty_rounds < self.config.video_empty_round_limit:
                    if self._page_has_rate_limit():
                        raise RuntimeError("rate_limit")
                    self._scroll_video_page_fast()
                    packets = self._drain_response_collector(collected, self.config.video_packet_timeout)
                    if not packets:
                        if self._page_has_service_error():
                            raise RuntimeError("service_error")
                        empty_rounds += 1
                        continue

                    empty_rounds = 0
                    new_videos = 0
                    should_stop = False
                    for data in packets:
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

                    stagnant_rounds = stagnant_rounds + 1 if new_videos == 0 else 0
                    if should_stop or stagnant_rounds >= self.config.video_empty_round_limit:
                        break

                videos = sorted(
                    videos_by_id.values(),
                    key=lambda item: item.get("publish_timestamp") or 0,
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
                        key=lambda item: item.get("publish_timestamp") or 0,
                        reverse=True,
                    )

                if limit:
                    videos = videos[:limit]
                self.service_error_streak = 0
                self.rate_limit_streak = 0
                self.rate_limiter.record_success()
                time.sleep(
                    self.rate_limiter.scaled_seconds(self.config.user_request_interval)
                    + random.uniform(0, 0.2)
                )
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
                self._remove_response_collector(handler)

    def _fetch_video_detail_by_aweme_id(self, user, aweme_id):
        collected, handler = self._create_response_collector(
            [self.config.video_detail_api_pattern, self.config.post_api_pattern]
        )
        try:
            self._open_page(f"https://www.douyin.com/video/{aweme_id}", self.config.video_page_load_delay)
            packets = self._drain_response_collector(collected, self.config.video_packet_timeout)
            for data in packets:
                self._update_user_profile_from_packet(user, data)
                for aweme in self._extract_awemes_from_packet_body(data):
                    if str(aweme.get("aweme_id") or "") == str(aweme_id):
                        return self._build_video_row(user, aweme)
        finally:
            self._remove_response_collector(handler)
        return None

    def _collect_visible_aweme_ids_from_dom(self, limit=None):
        result = self.start().evaluate(
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
                time.sleep(
                    self.rate_limiter.scaled_seconds(min(self.config.user_request_interval, 0.8))
                    + random.uniform(0.1, 0.3)
                )
        if recovered:
            print(f"   ↪ 浏览器兜底补回 {user['nickname']} 的 {len(recovered)} 个视频详情")
        return recovered

    def _scroll_active_containers(self):
        self.start().evaluate(
            """
            const scoreScrollable = (el) => {
                const style = getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden') return -999;
                if (el.scrollHeight <= el.clientHeight || el.clientHeight <= 150) return -999;
                if (style.overflowY === 'hidden') return -999;
                const rect = el.getBoundingClientRect();
                const text = (el.innerText || '').slice(0, 1200);
                let score = 0;
                if (text.includes('搜索用户名称或抖音号')) score += 18;
                if (text.includes('我的关注')) score += 18;
                if (text.includes('综合排序')) score += 8;
                if (text.includes('列表')) score += 6;
                if (text.includes('正在直播')) score += 24;
                if (rect.left >= 110 && rect.left < 460) score += 16;
                if (rect.width > 160 && rect.width < 380) score += 12;
                if (rect.height > 250) score += 3;
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
            self.start().evaluate(
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

    def _recover_from_service_error(self, user, wait_seconds):
        if self.service_error_streak >= 2:
            self.restart(wait_seconds)
        else:
            wait_with_progress(
                self.rate_limiter.scaled_seconds(wait_seconds),
                f"抖音服务异常恢复冷却：{user['nickname']}",
            )
        try:
            self._open_page(user["homepage"], self.config.video_page_load_delay + 1)
        except Exception:
            self.restart(wait_seconds)
            self._open_page(user["homepage"], self.config.video_page_load_delay + 1)

    def _recover_from_rate_limit(self, user, wait_seconds):
        if self.rate_limit_streak >= 2:
            self.restart(wait_seconds)
        else:
            wait_with_progress(
                self.rate_limiter.scaled_seconds(wait_seconds),
                f"抖音速率限制冷却：{user['nickname']}",
            )
        try:
            self._open_page(user["homepage"], self.config.video_page_load_delay + 2)
        except Exception:
            self.restart(wait_seconds)
            self._open_page(user["homepage"], self.config.video_page_load_delay + 2)

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

    def _detect_profile_follow_status(self):
        result = self.start().evaluate(
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
        result = self.start().evaluate(
            """
            ({ texts }) => {
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
            }
            """,
            {"texts": list(text_candidates)},
        )
        return bool(result)
