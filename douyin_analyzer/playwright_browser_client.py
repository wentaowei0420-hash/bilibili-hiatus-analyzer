import random
import re
import time
from pathlib import Path

from loguru import logger

from bilibili_analyzer.logging_utils import create_progress, smart_print as print, wait_with_progress

from .browser_client import (
    DouyinBrowserClient,
    DouyinFullFetchValidationError,
    DouyinLoginExpiredError,
    DouyinRateLimitError,
    DouyinServiceError,
)
from .utils import normalize_timestamp, parse_view_count

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

    def _set_window_state_if_possible(self, window_state):
        if self.context is None or self.page is None:
            return
        try:
            session = self.context.new_cdp_session(self.page)
            window_info = session.send("Browser.getWindowForTarget")
            window_id = window_info.get("windowId")
            if window_id is not None:
                session.send(
                    "Browser.setWindowBounds",
                    {"windowId": window_id, "bounds": {"windowState": window_state}},
                )
        except Exception:
            pass

    def _maximize_window_if_possible(self):
        self._set_window_state_if_possible("maximized")

    def _prepare_window_after_launch(self):
        self._maximize_window_if_possible()
        time.sleep(0.8)
        self._minimize_window_if_possible()

    def start(self):
        if self.page is not None:
            return self.page

        if sync_playwright is None:
            raise RuntimeError(
                "Playwright backend requested, but playwright is not installed. "
                "Run `pip install playwright` first."
            )

        self._playwright = sync_playwright().start()
        cache_bytes = max(0, int(getattr(self.config, "browser_disk_cache_size_mb", 128) or 0)) * 1024 * 1024
        browser_args = [
            "--mute-audio",
            "--start-maximized",
            "--disable-blink-features=AutomationControlled",
        ]
        if cache_bytes:
            browser_args.extend([
                f"--disk-cache-size={cache_bytes}",
                f"--media-cache-size={cache_bytes}",
            ])

        launch_kwargs = {
            "user_data_dir": str(self.config.browser_user_data_path),
            "headless": False,
            "args": browser_args,
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
        self._install_run_js_adapter(self.page)
        self._install_automation_stealth_hooks()
        self._prepare_window_after_launch()
        return self.page

    @staticmethod
    def _install_run_js_adapter(page):
        if page is None or hasattr(page, "run_js"):
            return

        def run_js(script):
            return page.evaluate(f"() => {{\n{script}\n}}")

        try:
            setattr(page, "run_js", run_js)
        except Exception:
            pass

    def _install_automation_stealth_hooks(self):
        script = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        try:
            self.context.add_init_script(script)
        except Exception:
            pass
        try:
            self.page.add_init_script(script)
        except Exception:
            pass
        try:
            self.page.evaluate(script)
        except Exception:
            pass

    def close(self):
        if self.context is not None:
            try:
                self._flush_browser_storage_before_close()
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
        self._print_login_persistence_diagnostic("启动检查")
        needs_login = False
        try:
            needs_login = page.locator("text=登录").first.is_visible(timeout=2000)
        except PlaywrightTimeoutError:
            pass
        except Exception:
            pass
        needs_login = needs_login or self._page_has_login_dialog()
        if needs_login:
            print("⚠️  尚未登录抖音，请先在浏览器中完成扫码登录。程序将自动等待登录完成...")
            if not self._wait_until_login_dialog_gone():
                raise DouyinLoginExpiredError("抖音登录未完成，请在浏览器中完成扫码登录后重试。")
            time.sleep(1.0)
            self._print_login_persistence_diagnostic("登录后检查")
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

    def _flush_browser_storage_before_close(self):
        try:
            state_path = Path(getattr(self.config, "export_store_db")).parent / "douyin_browser_storage_state.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            self.context.storage_state(path=str(state_path))
        except Exception:
            pass
        time.sleep(1.0)

    def _current_browser_cookie_names(self):
        names = set()
        if self.context is not None:
            try:
                for cookie in self.context.cookies("https://www.douyin.com"):
                    name = str((cookie or {}).get("name") or "").strip()
                    if name:
                        names.add(name)
            except Exception:
                pass
        return names or super()._current_browser_cookie_names()

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

            expected_video_count = self._extract_profile_video_count_from_dom()
            self._annotate_empty_video_profile_state(user, expected_video_count)
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

            self._annotate_empty_video_profile_state(user, user.get("aweme_count"))
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
            full_mode = limit is None
            try:
                self._open_page(user["homepage"], self.config.video_page_load_delay)
                self._abort_if_mid_task_login_required(full_mode)
                if self._page_has_rate_limit():
                    raise RuntimeError("rate_limit")
                self._abort_if_full_mode_service_error(full_mode)
                if self._page_has_service_error():
                    raise RuntimeError("service_error")
                expected_video_count = self._extract_profile_video_count_from_dom()
                zero_video_page_confirmed = self._annotate_empty_video_profile_state(user, expected_video_count)
                total_favorited = self._extract_total_favorited_from_dom()
                if total_favorited:
                    user["total_favorited"] = total_favorited
                if full_mode:
                    if expected_video_count == 0 and zero_video_page_confirmed:
                        logger.info(
                            "Douyin full fetch detected empty video page | backend=playwright | uid={} | expected=0",
                            user.get("sec_uid"),
                        )

                videos_by_id = {}
                empty_rounds = 0
                stagnant_rounds = 0
                full_mode_guard_rounds = max(self.config.video_empty_round_limit * 4, 8)
                no_more_marker_seen = False

                while True:
                    if limit and len(videos_by_id) >= limit:
                        break
                    self._abort_if_mid_task_login_required(full_mode)
                    if self._page_has_rate_limit():
                        raise RuntimeError("rate_limit")
                    self._scroll_video_page_fast()
                    self._abort_if_mid_task_login_required(full_mode)
                    no_more_marker_seen = no_more_marker_seen or self._video_page_has_no_more_marker()
                    packets = self._drain_response_collector(collected, self.config.video_packet_timeout)
                    if not packets:
                        self._abort_if_mid_task_login_required(full_mode)
                        self._abort_if_full_mode_service_error(full_mode)
                        if self._page_has_service_error():
                            raise RuntimeError("service_error")
                        empty_rounds += 1
                        if not full_mode:
                            if empty_rounds >= self.config.video_empty_round_limit:
                                break
                        else:
                            no_more_marker_seen = no_more_marker_seen or self._video_page_has_no_more_marker()
                            if expected_video_count == 0 and len(videos_by_id) == 0:
                                zero_video_page_confirmed = zero_video_page_confirmed or self._page_has_empty_video_state(expected_video_count)
                                if zero_video_page_confirmed:
                                    break
                            if no_more_marker_seen:
                                break
                            if empty_rounds >= full_mode_guard_rounds:
                                raise DouyinFullFetchValidationError(
                                    f"抖音全量抓取未检测到底部结束标记：主页显示作品 {expected_video_count or '未知'} 个，"
                                    f"当前已抓取 {len(videos_by_id)} 个，页面未出现“暂时没有更多了”。"
                                    ,
                                    videos=sorted(
                                        videos_by_id.values(),
                                        key=lambda item: item.get("publish_timestamp") or 0,
                                        reverse=True,
                                    ),
                                    expected_count=expected_video_count,
                                    actual_count=len(videos_by_id),
                                    no_more_marker_seen=no_more_marker_seen,
                                )
                        continue

                    empty_rounds = 0
                    new_videos = 0
                    should_stop = False
                    received_has_more_false = False
                    for data in packets:
                        if self._packet_has_rate_limit(data):
                            raise RuntimeError("rate_limit")
                        if self._packet_has_service_error(data):
                            self._abort_full_mode_frequency(full_mode)
                            raise RuntimeError("service_error")
                        self._update_user_profile_from_packet(user, data)
                        for aweme in self._extract_awemes_from_packet_body(data):
                            video = self._build_video_row(user, aweme)
                            if video and video["aweme_id"] not in videos_by_id:
                                new_videos += 1
                                videos_by_id[video["aweme_id"]] = video
                            elif video:
                                videos_by_id[video["aweme_id"]] = video
                        zero_video_page_confirmed = zero_video_page_confirmed or self._annotate_empty_video_profile_state(
                            user,
                            user.get("aweme_count"),
                        )
                        if limit and len(videos_by_id) >= limit:
                            should_stop = True
                            break
                        if data.get("has_more") in (0, False):
                            received_has_more_false = True

                    stagnant_rounds = stagnant_rounds + 1 if new_videos == 0 else 0
                    if not full_mode:
                        if should_stop or stagnant_rounds >= self.config.video_empty_round_limit:
                            break
                    else:
                        no_more_marker_seen = no_more_marker_seen or self._video_page_has_no_more_marker()
                        if expected_video_count == 0 and len(videos_by_id) == 0:
                            zero_video_page_confirmed = zero_video_page_confirmed or self._page_has_empty_video_state(expected_video_count)
                            if zero_video_page_confirmed:
                                break
                        if no_more_marker_seen:
                            break
                        if received_has_more_false:
                            logger.info(
                                "Douyin full fetch received has_more=false but keeps scrolling until no-more marker | backend=playwright | uid={} | collected={} | expected={}",
                                user.get("sec_uid"),
                                len(videos_by_id),
                                expected_video_count or 0,
                            )
                        if stagnant_rounds >= full_mode_guard_rounds:
                            raise DouyinFullFetchValidationError(
                                f"抖音全量抓取未检测到底部结束标记：主页显示作品 {expected_video_count or '未知'} 个，"
                                f"当前已抓取 {len(videos_by_id)} 个，页面未出现“暂时没有更多了”。"
                                ,
                                videos=sorted(
                                    videos_by_id.values(),
                                    key=lambda item: item.get("publish_timestamp") or 0,
                                    reverse=True,
                                ),
                                expected_count=expected_video_count,
                                actual_count=len(videos_by_id),
                                no_more_marker_seen=no_more_marker_seen,
                            )

                videos = sorted(
                    videos_by_id.values(),
                    key=lambda item: item.get("publish_timestamp") or 0,
                    reverse=True,
                )
                if (not videos or (limit and len(videos) < limit)) and self.rate_limiter.current_fallback_max_ids() > 0:
                    self._abort_if_mid_task_login_required(full_mode)
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
                if full_mode:
                    expected_video_count = int(user.get("aweme_count") or 0)
                    if expected_video_count == 0 and len(videos) == 0 and zero_video_page_confirmed:
                        no_more_marker_seen = True
                    if not no_more_marker_seen:
                        raise DouyinFullFetchValidationError(
                            f"抖音全量抓取未滚动到底：主页显示作品 {expected_video_count or '未知'} 个，"
                            f"当前已抓取 {len(videos)} 个，页面未出现“暂时没有更多了”。"
                            ,
                            videos=videos,
                            expected_count=expected_video_count,
                            actual_count=len(videos),
                            no_more_marker_seen=no_more_marker_seen,
                        )
                    if expected_video_count > 0 and len(videos) > expected_video_count:
                        user["aweme_count"] = len(videos)
                        latest_video = videos[0] if videos else None
                        if latest_video:
                            user["latest_publish_timestamp"] = normalize_timestamp(
                                latest_video.get("publish_timestamp")
                            )
                        logger.info(
                            "Douyin full fetch accepted count newer than profile | backend=playwright | uid={} | profile_count={} | actual_count={}",
                            user.get("sec_uid"),
                            expected_video_count,
                            len(videos),
                        )
                    elif expected_video_count > 0 and 0 <= expected_video_count - len(videos) <= 10:
                        if len(videos) != expected_video_count:
                            user["aweme_count"] = len(videos)
                            latest_video = videos[0] if videos else None
                            if latest_video:
                                user["latest_publish_timestamp"] = normalize_timestamp(
                                    latest_video.get("publish_timestamp")
                                )
                            logger.info(
                                "Douyin full fetch accepted minor count mismatch | backend=playwright | uid={} | profile_count={} | actual_count={} | delta={}",
                                user.get("sec_uid"),
                                expected_video_count,
                                len(videos),
                                expected_video_count - len(videos),
                            )
                    elif expected_video_count > 0 and len(videos) < expected_video_count:
                        raise DouyinFullFetchValidationError(
                            f"抖音全量抓取作品数量校验失败：主页显示作品 {expected_video_count} 个，"
                            f"实际抓取到 {len(videos)} 个。"
                            ,
                            videos=videos,
                            expected_count=expected_video_count,
                            actual_count=len(videos),
                            no_more_marker_seen=no_more_marker_seen,
                        )
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

    def _profile_video_count_from_dom(self):
        script = r"""
        const worksText = '\u4f5c\u54c1';
        const parseCount = (raw) => {
          const text = String(raw || '').trim().toLowerCase();
          const match = text.match(/([\d.]+)\s*(\u4ebf|\u4e07|\u5343|w)?/i);
          if (!match) return 0;
          let value = Number(match[1]);
          if (!Number.isFinite(value)) return 0;
          const unit = match[2] || '';
          if (unit === '\u4ebf') value *= 100000000;
          if (unit === '\u4e07' || unit === 'w') value *= 10000;
          if (unit === '\u5343') value *= 1000;
          return Math.round(value);
        };
        const candidates = Array.from(document.querySelectorAll('*'))
          .map(el => {
            const rect = el.getBoundingClientRect();
            const text = (el.innerText || el.textContent || '').trim();
            const count = parseCount(text);
            return {rect, text, count};
          })
          .filter(item =>
            item.count > 0 &&
            item.text.includes(worksText) &&
            item.rect.width > 0 &&
            item.rect.height > 0 &&
            item.rect.left >= 0 &&
            item.rect.left < window.innerWidth * 0.6 &&
            item.rect.top >= 80 &&
            item.rect.top <= 420
          )
          .map(item => {
            let score = 0;
            if (item.text.includes(worksText)) score += 30;
            if (item.rect.top >= 120 && item.rect.top <= 260) score += 12;
            if (item.rect.left <= 420) score += 10;
            if (item.text.length <= 80) score += 8;
            if (item.text.includes('\u63a8\u8350')) score += 6;
            if (item.text.includes('\u559c\u6b22')) score += 6;
            return {...item, score};
          })
          .sort((a, b) => b.score - a.score);
        if (candidates.length) {
          return {
            count: candidates[0].count,
            text: candidates[0].text.slice(0, 80),
            rect: {
              left: Math.round(candidates[0].rect.left),
              top: Math.round(candidates[0].rect.top),
              width: Math.round(candidates[0].rect.width),
              height: Math.round(candidates[0].rect.height)
            }
          };
        }
        return {count: 0, source: 'not_found'};
        """
        try:
            result = self.start().evaluate(f"() => {{\n{script}\n}}")
            if isinstance(result, dict) and int(result.get("count") or 0) > 0:
                logger.info("Douyin profile video count DOM parse | backend=playwright | result={}", result)
                return int(result.get("count") or 0)
        except Exception as exc:
            logger.warning("Douyin profile video count DOM parse failed | backend=playwright | error={}", exc)

        body_text = self._page_body_text()
        match = re.search(r"\u4f5c\u54c1\s*([\d.]+\s*(?:\u4ebf|\u4e07|\u5343|w)?)", body_text, re.I)
        return parse_view_count(match.group(1)) if match else 0

    def _video_page_has_no_more_marker(self):
        script = r"""
        const noMoreText = '\u6682\u65f6\u6ca1\u6709\u66f4\u591a\u4e86';
        const candidates = Array.from(document.querySelectorAll('*'))
          .map(el => {
            const rect = el.getBoundingClientRect();
            const text = (el.innerText || el.textContent || '').trim();
            return {rect, text};
          })
          .filter(item =>
            item.text.includes(noMoreText) &&
            item.rect.width > 0 &&
            item.rect.height > 0 &&
            item.rect.top < window.innerHeight + 500 &&
            item.rect.bottom > -100
          )
          .sort((a, b) => b.rect.top - a.rect.top);
        if (candidates.length) {
          return {
            seen: true,
            text: candidates[0].text.slice(0, 40),
            top: Math.round(candidates[0].rect.top)
          };
        }
        return {seen: false};
        """
        try:
            result = self.start().evaluate(f"() => {{\n{script}\n}}")
            if isinstance(result, dict) and result.get("seen"):
                logger.info("Douyin video no-more marker detected | backend=playwright | result={}", result)
                return True
        except Exception as exc:
            logger.warning("Douyin video no-more marker check failed | backend=playwright | error={}", exc)
        return "\u6682\u65f6\u6ca1\u6709\u66f4\u591a\u4e86" in self._page_body_text()

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
        self.start().run_js(
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
        result = self.start().run_js(
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
