import json
import random
import re
import shutil
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse

from DrissionPage import ChromiumOptions, ChromiumPage
from loguru import logger

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


class DouyinLoginExpiredError(RuntimeError):
    pass


class DouyinFullModeFrequencyError(RuntimeError):
    pass


AUTH_COOKIE_NAMES = {
    "sessionid",
    "sid_guard",
    "sid_tt",
    "uid_tt",
    "uid_tt_ss",
    "passport_auth_status",
    "passport_auth_status_default",
    "passport_auth_status_ss",
    "passport_auth_status_ss_default",
    "passport_assist_user",
    "passport_fe_beating_status",
    "n_mh",
}


class DouyinFullFetchValidationError(RuntimeError):
    def __init__(
        self,
        message,
        *,
        videos=None,
        expected_count=0,
        actual_count=0,
        no_more_marker_seen=False,
    ):
        super().__init__(message)
        self.videos = list(videos or [])
        self.expected_count = int(expected_count or 0)
        self.actual_count = int(actual_count or 0)
        self.no_more_marker_seen = bool(no_more_marker_seen)


class DouyinBrowserClient:
    def __init__(self, config):
        self.config = config
        self.page = None
        self.service_error_streak = 0
        self.rate_limit_streak = 0
        self.rate_limiter = DouyinRateLimiter(config)
        self._last_following_click_result = {}

    def _minimize_window_if_possible(self):
        if self.page is None:
            return
        try:
            self.page.set.window.mini()
        except Exception:
            pass

    def _maximize_window_if_possible(self):
        if self.page is None:
            return
        try:
            window = self.page.set.window
        except Exception:
            return
        for method_name in ("max", "full", "maximize", "fullscreen"):
            try:
                method = getattr(window, method_name, None)
                if callable(method):
                    method()
                    return
            except Exception:
                continue

    def _prepare_window_after_launch(self):
        self._maximize_window_if_possible()
        time.sleep(0.8)
        self._minimize_window_if_possible()

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
        co.set_argument("--start-maximized")
        co.set_argument("--disable-blink-features=AutomationControlled")
        cache_bytes = max(0, int(getattr(self.config, "browser_disk_cache_size_mb", 128) or 0)) * 1024 * 1024
        if cache_bytes:
            co.set_argument(f"--disk-cache-size={cache_bytes}")
            co.set_argument(f"--media-cache-size={cache_bytes}")
        co.set_user_data_path(str(self.config.browser_user_data_path))
        self.page = ChromiumPage(co)
        self._prepare_window_after_launch()
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
                self._flush_browser_storage_before_close()
                self.page.quit()
            except Exception:
                pass
            self.page = None

    def restart(self, wait_seconds=0):
        self.close()
        if wait_seconds > 0:
            wait_with_progress(wait_seconds, "抖音浏览器会话重启冷却中")
        page = self.start()
        return page

    def ensure_login(self):
        page = self._open_page(self.config.home_url, self.config.page_load_delay)
        self._print_login_persistence_diagnostic("启动检查")
        if page.ele("text=登录", timeout=2) or self._page_has_login_dialog():
            print("⚠️  尚未登录抖音，请先在浏览器中完成扫码登录。")
            input("登录成功并刷新页面后，按回车继续...")
            self._wait_until_login_dialog_gone()
            time.sleep(1.0)
            self._print_login_persistence_diagnostic("登录后检查")
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

    @staticmethod
    def _extract_packet_url(packet):
        candidates = [
            getattr(packet, "url", None),
            getattr(getattr(packet, "request", None), "url", None),
            getattr(getattr(packet, "response", None), "url", None),
            getattr(packet, "target", None),
        ]
        for value in candidates:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _is_blocked_following_list_url(url):
        lowered = str(url or "").strip().lower()
        if "aweme/v1/web/user/following/list" in lowered or "user/following/list" in lowered:
            return False
        path = urlparse(lowered).path
        blocked_keywords = ("/webcast/", "/live/", "live_info", "/room/", "webcast_room")
        return any(keyword in path for keyword in blocked_keywords)

    @staticmethod
    def _is_primary_following_list_url(url):
        lowered = str(url or "").strip().lower()
        if not lowered:
            return False
        if "following/list" not in lowered:
            return False
        if DouyinBrowserClient._is_blocked_following_list_url(lowered):
            return False
        return "aweme/v1/web/user/following/list" in lowered or "user/following/list" in lowered

    @staticmethod
    def _extract_following_users(data):
        if not isinstance(data, dict):
            return []
        candidates = [data.get("followings")]
        inner_data = data.get("data")
        if isinstance(inner_data, dict):
            candidates.append(inner_data.get("followings"))
        for candidate in candidates:
            if isinstance(candidate, list):
                return candidate
        return []

    @classmethod
    def _packet_has_followings(cls, data):
        return any(
            isinstance(user, dict) and user.get("sec_uid")
            for user in cls._extract_following_users(data)
        )

    def _current_url(self):
        try:
            return str(getattr(self.start(), "url", "") or "")
        except Exception:
            return ""

    def _native_click_at(self, x, y):
        try:
            x = float(x)
            y = float(y)
        except (TypeError, ValueError):
            return False
        try:
            page = self.start()
            page.run_cdp("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
            page.run_cdp(
                "Input.dispatchMouseEvent",
                type="mousePressed",
                x=x,
                y=y,
                button="left",
                clickCount=1,
            )
            page.run_cdp(
                "Input.dispatchMouseEvent",
                type="mouseReleased",
                x=x,
                y=y,
                button="left",
                clickCount=1,
            )
            logger.info("Douyin native click dispatched | x={} | y={}", round(x), round(y))
            return True
        except Exception as exc:
            logger.warning("Douyin native click failed | x={} | y={} | error={}", x, y, exc)
            return False

    def _open_following_route_fallback(self, href=""):
        candidates = []
        href = str(href or "").strip()
        if href and "/follow" in href and "following/list" not in href:
            candidates.append(href)
        candidates.extend(
            [
                "https://www.douyin.com/follow",
                "https://www.douyin.com/following",
            ]
        )

        seen = set()
        for url in candidates:
            if not url or url in seen:
                continue
            seen.add(url)
            logger.warning("Douyin following route fallback | url={}", url)
            self._open_page(url, max(2.5, self.config.page_load_delay))
            if self._page_has_rate_limit():
                raise DouyinRateLimitError("抖音关注列表页触发速率限制")
            if self._wait_for_following_panel_ready(timeout_seconds=6.0):
                return True
        return False

    def _click_self_following_entry(self):
        script = r"""
        const keyword = '\u5173\u6ce8';
        const liveText = '\u6b63\u5728\u76f4\u64ad';
        const elements = Array.from(document.querySelectorAll('a,button,div,span'));
        const visible = elements
          .map(el => {
            const rect = el.getBoundingClientRect();
            const text = (el.innerText || el.textContent || '').trim();
            const parentText = (el.parentElement && (el.parentElement.innerText || '')) || '';
            return {el, rect, text, parentText};
          })
          .filter(item =>
            item.text.includes(keyword) &&
            item.rect.width > 0 &&
            item.rect.height > 0 &&
            item.rect.left >= 0 &&
            item.rect.left < 190 &&
            item.rect.top > 70 &&
            item.rect.top < window.innerHeight - 80 &&
            !item.text.includes(liveText) &&
            !item.parentText.includes(liveText)
          )
          .map(item => {
            let score = 0;
            if (item.text === keyword) score += 40;
            if (item.text.length <= 8) score += 12;
            if (item.rect.left < 120) score += 18;
            if (item.rect.top > 180 && item.rect.top < 330) score += 16;
            if (item.parentText.includes('\u670b\u53cb')) score += 3;
            if (item.parentText.includes('\u6211\u7684')) score += 3;
            if (item.parentText.includes('\u7c89\u4e1d')) score -= 30;
            if (item.parentText.includes('\u83b7\u8d5e')) score -= 30;
            if (item.parentText.includes('\u4f5c\u54c1')) score -= 12;
            if (item.parentText.length > 120) score -= 15;
            return {...item, score};
          })
          .sort((a, b) => b.score - a.score);
        if (!visible.length) {
          return {clicked: false, reason: 'not_found'};
        }
        const target = visible[0];
        let clickTarget = target.el.closest('a,button,[role="button"]');
        if (!clickTarget) {
          let current = target.el;
          while (current && current.parentElement) {
            const rect = current.getBoundingClientRect();
            const text = (current.innerText || current.textContent || '').trim();
            if (rect.left >= 0 && rect.left < 190 && rect.width > 70 && text.includes(keyword) && text.length < 80) {
              clickTarget = current;
            }
            current = current.parentElement;
          }
        }
        clickTarget = clickTarget || target.el;
        const linkTarget = target.el.closest('a[href]') || clickTarget.closest('a[href]');
        const clickRect = clickTarget.getBoundingClientRect();
        const x = clickRect.left + Math.min(Math.max(clickRect.width / 2, 20), Math.max(clickRect.width - 5, 20));
        const y = clickRect.top + clickRect.height / 2;
        for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
          clickTarget.dispatchEvent(new MouseEvent(type, {
            bubbles: true,
            cancelable: true,
            view: window,
            clientX: x,
            clientY: y
          }));
        }
        return {
          clicked: true,
          reason: 'left_nav_following',
          text: target.text,
          parentText: target.parentText.slice(0, 80),
          href: linkTarget ? linkTarget.href : '',
          clickX: Math.round(x),
          clickY: Math.round(y),
          rect: {
            left: Math.round(target.rect.left),
            top: Math.round(target.rect.top),
            width: Math.round(target.rect.width),
            height: Math.round(target.rect.height)
          },
          score: target.score
        };
        """
        try:
            page = self.start()
            if hasattr(page, "run_js"):
                result = page.run_js(script)
            elif hasattr(page, "evaluate"):
                result = page.evaluate(script)
            else:
                result = {"clicked": False, "reason": "unsupported_page_backend"}
            logger.info("Douyin left nav following click | result={}", result)
            self._last_following_click_result = result if isinstance(result, dict) else {}
            if isinstance(result, dict) and result.get("clicked"):
                self._native_click_at(result.get("clickX"), result.get("clickY"))
                return True
            return False
        except Exception as exc:
            logger.warning("Douyin left nav following click failed | error={}", exc)
            return False

    def _is_following_panel_ready(self):
        script = r"""
        const searchText = '\u641c\u7d22\u7528\u6237\u540d\u6216\u6296\u97f3\u53f7';
        const liveText = '\u6b63\u5728\u76f4\u64ad';
        const myFollowingText = '\u6211\u7684\u5173\u6ce8';
        const listText = '\u5217\u8868';
        const sortText = '\u7efc\u5408\u6392\u5e8f';
        const url = location.href || '';
        const candidates = Array.from(document.querySelectorAll('*')).map(el => {
          const rect = el.getBoundingClientRect();
          const text = (el.innerText || el.textContent || '').trim();
          return {rect, text};
        }).filter(item =>
          item.rect.width > 0 &&
          item.rect.height > 0 &&
          item.rect.left >= 100 &&
          item.rect.left < 470 &&
          item.rect.top >= 40 &&
          item.rect.height > 100
        );
        const panel = candidates
          .map(item => {
            let score = 0;
            if (item.text.includes(searchText)) score += 30;
            if (item.text.includes(liveText)) score += 20;
            if (item.text.includes(myFollowingText)) score += 25;
            if (item.text.includes(listText)) score += 10;
            if (item.text.includes(sortText)) score += 10;
            return {...item, score};
          })
          .sort((a, b) => b.score - a.score)[0];
        const ready = Boolean(panel && panel.score >= 20);
        return {
          ready,
          url,
          score: panel ? panel.score : 0,
          text: panel ? panel.text.slice(0, 120) : ''
        };
        """
        try:
            page = self.start()
            if hasattr(page, "run_js"):
                result = page.run_js(script)
            elif hasattr(page, "evaluate"):
                result = page.evaluate(script)
            else:
                result = {"ready": False, "reason": "unsupported_page_backend"}
            logger.info("Douyin following panel ready check | result={}", result)
            return bool(isinstance(result, dict) and result.get("ready"))
        except Exception as exc:
            logger.warning("Douyin following panel ready check failed | error={}", exc)
            return False

    def _wait_for_following_panel_ready(self, timeout_seconds=8.0, poll_interval=0.5):
        deadline = time.time() + max(0.5, float(timeout_seconds or 0))
        while time.time() < deadline:
            if self._page_has_rate_limit():
                raise DouyinRateLimitError("抖音关注列表页触发速率限制")
            if self._is_following_panel_ready():
                time.sleep(2.0)
                return True
            time.sleep(max(0.2, float(poll_interval or 0.5)))
        return False

    def _focus_following_list_after_live(self):
        script = r"""
        const followTitle = '\u6211\u7684\u5173\u6ce8';
        const liveTitle = '\u6b63\u5728\u76f4\u64ad';
        const isScrollable = (el) => {
          if (!el) return false;
          const style = getComputedStyle(el);
          return el.scrollHeight > el.clientHeight + 20 &&
                 el.clientHeight > 120 &&
                 style.display !== 'none' &&
                 style.visibility !== 'hidden' &&
                 style.overflowY !== 'hidden';
        };
        const nearestScrollable = (el) => {
          let current = el;
          while (current && current !== document.body) {
            if (isScrollable(current)) return current;
            current = current.parentElement;
          }
          return null;
        };
        const textOf = (el) => (el.innerText || el.textContent || '').trim();
        const visible = Array.from(document.querySelectorAll('*'))
          .map(el => ({el, text: textOf(el), rect: el.getBoundingClientRect()}))
          .filter(item =>
            item.text.includes(followTitle) &&
            item.text.length <= 80 &&
            item.rect.width > 0 &&
            item.rect.height > 0 &&
            item.rect.left < window.innerWidth * 0.45
          )
          .sort((a, b) => a.rect.top - b.rect.top);

        if (visible.length) {
          const target = visible[0];
          const container = nearestScrollable(target.el);
          if (container) {
            const targetRect = target.el.getBoundingClientRect();
            const containerRect = container.getBoundingClientRect();
            container.scrollTop += targetRect.top - containerRect.top - 40;
            return {
              focused: true,
              reason: 'found_following_title',
              title: target.text.slice(0, 40),
              scrollTop: Math.round(container.scrollTop),
              containerText: textOf(container).slice(0, 80)
            };
          }
        }

        const candidates = Array.from(document.querySelectorAll('*'))
          .filter(isScrollable)
          .map(el => {
            const rect = el.getBoundingClientRect();
            const text = textOf(el).slice(0, 1000);
            let score = 0;
            if (text.includes(liveTitle)) score += 24;
            if (text.includes(followTitle)) score += 20;
            if (text.includes('\u641c\u7d22\u7528\u6237\u540d\u6216\u6296\u97f3\u53f7')) score += 18;
            if (text.includes('\u7efc\u5408\u6392\u5e8f')) score += 8;
            if (text.includes('\u5217\u8868')) score += 6;
            if (rect.left >= 110 && rect.left < 460) score += 16;
            if (rect.width > 160 && rect.width < 380) score += 12;
            if (rect.height > 250) score += 3;
            return {el, rect, text, score};
          })
          .sort((a, b) => b.score - a.score);
        if (candidates.length && candidates[0].score > 0) {
          const container = candidates[0].el;
          container.scrollTop += Math.max(700, Math.round(container.clientHeight * 0.9));
          return {
            focused: false,
            reason: 'scroll_following_column_until_my_following',
            score: candidates[0].score,
            scrollTop: Math.round(container.scrollTop),
            containerText: textOf(container).slice(0, 80)
          };
        }
        return {focused: false, reason: 'container_not_found'};
        """
        try:
            page = self.start()
            if hasattr(page, "run_js"):
                result = page.run_js(script)
            elif hasattr(page, "evaluate"):
                result = page.evaluate(script)
            else:
                result = {"focused": False, "reason": "unsupported_page_backend"}
            logger.info("Douyin following list focus | result={}", result)
            return bool(isinstance(result, dict) and result.get("focused"))
        except Exception as exc:
            logger.warning("Douyin following list focus failed | error={}", exc)
            return False

    def get_followings(self):
        page = self.start()
        print("📜 正在抓取抖音关注列表...")
        listen_patterns = list(
            dict.fromkeys(
                [
                    "following/list",
                    self.config.following_api_pattern,
                    "aweme/v1/web/user/following/list",
                ]
            )
        )
        page.listen.start(listen_patterns)
        self._open_page(self.config.self_user_url, self.config.page_load_delay)
        expected_following_count = self._extract_following_count_from_dom()
        print(
            f"🧭 关注数量校验基准 | 主页显示={expected_following_count or '未知'} | "
            f"监听接口={listen_patterns}"
        )
        if self._page_has_rate_limit():
            raise DouyinRateLimitError("抖音关注列表页触发速率限制")

        logger.info("Douyin following route direct open | url=https://www.douyin.com/follow")
        if not self._open_following_route_fallback("https://www.douyin.com/follow"):
            raise RuntimeError("抖音关注列表页未成功打开：直接跳转 /follow 后没有检测到关注列表面板。")
        if self._page_has_rate_limit():
            raise DouyinRateLimitError("抖音关注列表页触发速率限制")

        try:
            list_tab = page.ele("text:\u5217\u8868", timeout=2)
            if list_tab:
                list_tab.click()
                time.sleep(0.8)
        except Exception:
            pass

        self._focus_following_list_after_live()

        followings = []
        seen_sec_uids = set()
        empty_rounds = 0
        stagnant_rounds = 0
        has_more = True
        skipped_non_primary_packets = 0
        accepted_unrecognized_packets = 0
        skipped_url_samples = []
        accepted_url_samples = []

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
                    packet_url = self._extract_packet_url(packet)
                    if packet_url and self._is_blocked_following_list_url(packet_url):
                        skipped_non_primary_packets += 1
                        if len(skipped_url_samples) < 5:
                            skipped_url_samples.append(packet_url)
                        continue
                    data = self._extract_packet_body(packet)
                    body_has_followings = self._packet_has_followings(data)
                    if packet_url and not self._is_primary_following_list_url(packet_url) and not body_has_followings:
                        skipped_non_primary_packets += 1
                        if len(skipped_url_samples) < 5:
                            skipped_url_samples.append(packet_url)
                        continue
                    if packet_url and not self._is_primary_following_list_url(packet_url) and body_has_followings:
                        accepted_unrecognized_packets += 1
                        if len(accepted_url_samples) < 5:
                            accepted_url_samples.append(packet_url)
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
        print(
            f"🧭 关注列表抓取汇总 | 主页显示={expected_following_count or '未知'} | "
            f"主接口去重后={len(followings)} | 过滤非主包={skipped_non_primary_packets} | "
            f"结构兜底接受={accepted_unrecognized_packets}"
        )
        logger.info(
            "Douyin followings packet summary | expected={} | collected={} | skipped={} | accepted_by_body={} | skipped_samples={} | accepted_samples={}",
            expected_following_count,
            len(followings),
            skipped_non_primary_packets,
            accepted_unrecognized_packets,
            skipped_url_samples,
            accepted_url_samples,
        )
        if skipped_non_primary_packets:
            print(f"🧹 已过滤 {skipped_non_primary_packets} 个非主关注列表数据包（如直播关注流）。")
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

    def get_all_videos_for_user(self, user):
        return self._collect_videos_for_user(user, limit=None)

    def get_recent_videos_for_user(self, user, limit):
        return self._collect_videos_for_user(user, limit=max(1, int(limit or 1)))

    def refresh_user_profile_from_homepage(self, user):
        page = self.start()
        page.listen.start([self.config.post_api_pattern, self.config.video_detail_api_pattern])
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

            packets = self._drain_listen_packets(timeout=self.config.video_packet_timeout)
            for packet in packets:
                data = self._extract_packet_body(packet)
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
            try:
                page.listen.stop()
            except Exception:
                pass

    def _collect_videos_for_user(self, user, limit=None):
        page = self.start()
        for attempt in range(1, self.config.video_page_retry_count + 1):
            videos_by_id = {}
            empty_rounds = 0
            stagnant_rounds = 0
            full_mode = limit is None
            full_mode_guard_rounds = max(self.config.video_empty_round_limit * 4, 8)
            no_more_marker_seen = False
            zero_video_page_confirmed = False
            page.listen.start([self.config.post_api_pattern, self.config.video_detail_api_pattern])
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
                            "Douyin full fetch detected empty video page | uid={} | expected=0",
                            user.get("sec_uid"),
                        )

                while True:
                    if limit and len(videos_by_id) >= limit:
                        break
                    self._abort_if_mid_task_login_required(full_mode)
                    if self._page_has_rate_limit():
                        raise RuntimeError("rate_limit")
                    self._scroll_video_page_fast()
                    self._abort_if_mid_task_login_required(full_mode)
                    no_more_marker_seen = no_more_marker_seen or self._video_page_has_no_more_marker()
                    packets = self._drain_listen_packets(timeout=self.config.video_packet_timeout)
                    if packets:
                        empty_rounds = 0
                        new_videos = 0
                        should_stop = False
                        received_has_more_false = False
                        for packet in packets:
                            data = self._extract_packet_body(packet)
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

                        if new_videos == 0:
                            stagnant_rounds += 1
                        else:
                            stagnant_rounds = 0

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
                                    "Douyin full fetch received has_more=false but keeps scrolling until no-more marker | uid={} | collected={} | expected={}",
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
                                        key=lambda item: normalize_timestamp(item.get("publish_timestamp")),
                                        reverse=True,
                                    ),
                                    expected_count=expected_video_count,
                                    actual_count=len(videos_by_id),
                                    no_more_marker_seen=no_more_marker_seen,
                                )
                    else:
                        self._abort_if_mid_task_login_required(full_mode)
                        if self._page_has_rate_limit():
                            raise RuntimeError("rate_limit")
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
                                        key=lambda item: normalize_timestamp(item.get("publish_timestamp")),
                                        reverse=True,
                                    ),
                                    expected_count=expected_video_count,
                                    actual_count=len(videos_by_id),
                                    no_more_marker_seen=no_more_marker_seen,
                                )

                videos = sorted(
                    videos_by_id.values(),
                    key=lambda item: normalize_timestamp(item.get("publish_timestamp")),
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
                        key=lambda item: normalize_timestamp(item.get("publish_timestamp")),
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
                            "Douyin full fetch accepted count newer than profile | uid={} | profile_count={} | actual_count={}",
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
                                "Douyin full fetch accepted minor count mismatch | uid={} | profile_count={} | actual_count={} | delta={}",
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

    def _flush_browser_storage_before_close(self):
        time.sleep(0.5)

    def _profile_cookie_db_path(self):
        user_data_path = getattr(self.config, "browser_user_data_path", None)
        if not user_data_path:
            return None
        user_data_path = Path(user_data_path)
        candidates = [
            user_data_path / "Default" / "Network" / "Cookies",
            user_data_path / "Default" / "Cookies",
        ]
        return next((path for path in candidates if path.exists()), candidates[0])

    def _profile_cookie_names(self):
        cookie_db = self._profile_cookie_db_path()
        if not cookie_db or not cookie_db.exists():
            return set()
        return self._read_cookie_names_from_db(cookie_db)

    def _read_cookie_names_from_db(self, cookie_db):
        def read_from(path):
            with sqlite3.connect(str(path), timeout=1) as conn:
                rows = conn.execute(
                    """
                    SELECT DISTINCT name
                    FROM cookies
                    WHERE host_key LIKE '%douyin.com%'
                       OR host_key LIKE '%iesdouyin.com%'
                       OR host_key LIKE '%snssdk.com%'
                       OR host_key LIKE '%toutiao.com%'
                    """
                ).fetchall()
            return {str(row[0] or "").strip() for row in rows if str(row[0] or "").strip()}

        try:
            return read_from(cookie_db)
        except Exception:
            pass

        try:
            snapshot_dir = Path(getattr(self.config, "export_store_db")).parent
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            snapshot_path = snapshot_dir / "_douyin_cookie_diagnostic.sqlite"
            shutil.copy2(cookie_db, snapshot_path)
            try:
                return read_from(snapshot_path)
            finally:
                try:
                    snapshot_path.unlink(missing_ok=True)
                except Exception:
                    pass
        except Exception:
            return None

    def _current_browser_cookie_names(self):
        return self._profile_cookie_names()

    def _login_cookie_summary(self):
        names = self._current_browser_cookie_names()
        if names is None:
            names = set()
            unknown = True
        else:
            unknown = False
        auth_names = sorted(name for name in names if name in AUTH_COOKIE_NAMES)
        return {
            "cookie_count": len(names),
            "auth_cookie_count": len(auth_names),
            "auth_cookie_names": auth_names,
            "profile_cookie_db": str(self._profile_cookie_db_path() or ""),
            "unknown": unknown,
        }

    def _print_login_persistence_diagnostic(self, stage):
        summary = self._login_cookie_summary()
        auth_names = summary.get("auth_cookie_names") or []
        if summary.get("unknown"):
            print(
                f"ℹ️  抖音登录态{stage}: profile={self.config.browser_user_data_path} | "
                "cookie 库当前被浏览器占用，无法离线读取；运行中将使用浏览器内 cookie 检查。"
            )
        elif auth_names:
            print(
                f"🔐 抖音登录态{stage}: profile={self.config.browser_user_data_path} | "
                f"cookie={summary['cookie_count']} | 登录态={','.join(auth_names)}"
            )
        else:
            print(
                f"⚠️  抖音登录态{stage}: profile={self.config.browser_user_data_path} | "
                f"cookie={summary['cookie_count']} | 未发现稳定登录态 cookie。"
            )

    def _wait_until_login_dialog_gone(self, timeout_seconds=180):
        deadline = time.time() + max(5, float(timeout_seconds or 0))
        while time.time() < deadline:
            if not self._page_has_login_dialog():
                return True
            time.sleep(1.0)
        print("⚠️  登录弹窗仍存在，可能登录未完成或未刷新成功，继续前请确认浏览器内已登录。")
        return False

    def _page_has_login_dialog(self):
        body_text = self._page_body_text()
        login_markers = (
            "\u767b\u5f55\u540e\u514d\u8d39\u7545\u4eab\u66f4\u591a\u7cbe\u5f69\u89c6\u9891",
            "\u626b\u7801\u767b\u5f55",
            "\u9a8c\u8bc1\u7801\u767b\u5f55",
            "\u5bc6\u7801\u767b\u5f55",
            "\u83b7\u53d6\u77ed\u4fe1\u9a8c\u8bc1\u7801",
        )
        if any(marker in body_text for marker in login_markers):
            return True

        script = r"""
        const loginMarkers = [
          '\u767b\u5f55\u540e\u514d\u8d39\u7545\u4eab\u66f4\u591a\u7cbe\u5f69\u89c6\u9891',
          '\u626b\u7801\u767b\u5f55',
          '\u9a8c\u8bc1\u7801\u767b\u5f55',
          '\u5bc6\u7801\u767b\u5f55',
          '\u83b7\u53d6\u77ed\u4fe1\u9a8c\u8bc1\u7801'
        ];
        const visible = Array.from(document.querySelectorAll('body *'))
          .map(el => {
            const rect = el.getBoundingClientRect();
            const style = getComputedStyle(el);
            const text = (el.innerText || el.textContent || '').trim();
            return {rect, style, text};
          })
          .filter(item =>
            item.text &&
            item.rect.width > 0 &&
            item.rect.height > 0 &&
            item.style.display !== 'none' &&
            item.style.visibility !== 'hidden'
          );
        const hit = visible.find(item => loginMarkers.some(marker => item.text.includes(marker)));
        return Boolean(hit);
        """
        try:
            page = self.start()
            if hasattr(page, "run_js"):
                return bool(page.run_js(script))
            if hasattr(page, "evaluate"):
                return bool(page.evaluate(script))
        except Exception:
            return False
        return False

    def _abort_if_mid_task_login_required(self, full_mode=False):
        if full_mode and self._page_has_login_dialog():
            raise DouyinLoginExpiredError(
                "\u6296\u97f3 full \u6a21\u5f0f\u6267\u884c\u4e2d\u51fa\u73b0\u767b\u5f55\u5f39\u7a97\uff0c"
                "\u5224\u5b9a\u4e3a\u4f1a\u8bdd\u5931\u6548\uff0c\u5df2\u4e2d\u6b62\u4efb\u52a1\u3002"
            )

    def _annotate_empty_video_profile_state(self, user, expected_video_count=None):
        if not isinstance(user, dict):
            return False

        normalized_count = expected_video_count
        try:
            if normalized_count in (None, ""):
                normalized_count = user.get("aweme_count")
            if normalized_count not in (None, ""):
                normalized_count = int(normalized_count)
        except (TypeError, ValueError):
            normalized_count = None

        if normalized_count == 0:
            user["aweme_count"] = 0
        elif isinstance(normalized_count, int) and normalized_count > 0:
            user["aweme_count"] = normalized_count

        confirmed = bool(normalized_count == 0 and self._page_has_empty_video_state(normalized_count))
        user["_empty_video_page_confirmed"] = confirmed
        return confirmed

    def _page_has_empty_video_state(self, expected_video_count=None):
        if expected_video_count not in (None, "", 0):
            return False
        body_text = self._page_body_text()
        empty_markers = (
            "暂无作品",
            "还没有作品",
            "暂时没有作品",
            "没有作品",
        )
        return any(marker in body_text for marker in empty_markers)

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

    def _abort_if_full_mode_service_error(self, full_mode=False):
        if full_mode and self._page_has_service_error():
            self._abort_full_mode_frequency(full_mode)

    @staticmethod
    def _abort_full_mode_frequency(full_mode=False):
        if full_mode:
            raise DouyinFullModeFrequencyError(
                "抖音 full 模式进入 UP 主页后出现“服务异常，重新刷新拉取数据”，"
                "判断为抓取过于频繁，已终止本轮任务。"
            )

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

    def _extract_profile_video_count_from_dom(self):
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
            const match = text.match(/\u4f5c\u54c1\s*([\d.]+\s*(?:\u4ebf|\u4e07|\u5343|w)?)/i);
            return {rect, text, count: match ? parseCount(match[1]) : 0};
          })
          .filter(item =>
            item.count > 0 &&
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
            page = self.start()
            if hasattr(page, "run_js"):
                result = page.run_js(script)
            elif hasattr(page, "evaluate"):
                result = page.evaluate(script)
            else:
                result = {"count": 0, "source": "unsupported_page_backend"}
            if isinstance(result, dict) and int(result.get("count") or 0) > 0:
                logger.info("Douyin profile video count DOM parse | result={}", result)
                return int(result.get("count") or 0)
        except Exception as exc:
            logger.warning("Douyin profile video count DOM parse failed | error={}", exc)

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
            page = self.start()
            if hasattr(page, "run_js"):
                result = page.run_js(script)
            elif hasattr(page, "evaluate"):
                result = page.evaluate(script)
            else:
                result = {"seen": False}
            if isinstance(result, dict) and result.get("seen"):
                logger.info("Douyin video no-more marker detected | result={}", result)
                return True
        except Exception as exc:
            logger.warning("Douyin video no-more marker check failed | error={}", exc)
        return "\u6682\u65f6\u6ca1\u6709\u66f4\u591a\u4e86" in self._page_body_text()

    def _extract_following_count_from_dom(self):
        script = r"""
        const followingText = '\u5173\u6ce8';
        const fansText = '\u7c89\u4e1d';
        const likedText = '\u83b7\u8d5e';
        const liveText = '\u6b63\u5728\u76f4\u64ad';
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
            const match = text.match(/\u5173\u6ce8\s*([\d.]+\s*(?:\u4ebf|\u4e07|\u5343|w)?)/i);
            return {el, rect, text, count: match ? parseCount(match[1]) : 0};
          })
          .filter(item =>
            item.count > 0 &&
            item.rect.width > 0 &&
            item.rect.height > 0 &&
            item.rect.top >= 60 &&
            item.rect.top <= 260 &&
            item.rect.left > 120 &&
            item.rect.left < window.innerWidth * 0.75 &&
            !item.text.includes(liveText)
          )
          .map(item => {
            let score = 0;
            if (item.text.includes(followingText)) score += 8;
            if (item.text.includes(fansText)) score += 20;
            if (item.text.includes(likedText)) score += 20;
            if (item.rect.top >= 90 && item.rect.top <= 210) score += 12;
            if (item.rect.left >= 250 && item.rect.left <= 760) score += 10;
            if (item.text.length < 180) score += 6;
            return {...item, score};
          })
          .sort((a, b) => b.score - a.score);
        if (candidates.length) {
          return {
            count: candidates[0].count,
            source: 'profile_stats_dom',
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
            page = self.start()
            if hasattr(page, "run_js"):
                result = page.run_js(script)
            elif hasattr(page, "evaluate"):
                result = page.evaluate(script)
            else:
                result = {"count": 0, "source": "unsupported_page_backend"}
            if isinstance(result, dict) and int(result.get("count") or 0) > 0:
                logger.info("Douyin following count DOM parse | result={}", result)
                return int(result.get("count") or 0)
        except Exception as exc:
            logger.warning("Douyin following count DOM parse failed | error={}", exc)

        body_text = self._page_body_text()
        matches = re.findall(r"\u5173\u6ce8\s*([\d.]+\s*(?:\u4ebf|\u4e07|\u5343|w)?)", body_text, re.I)
        counts = [parse_view_count(match) for match in matches]
        counts = [count for count in counts if count > 0]
        return max(counts) if counts else 0

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
