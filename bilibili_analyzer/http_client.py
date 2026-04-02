import hashlib
import random
import re
import threading
import time
from urllib.parse import urlencode

import requests

from .logging_utils import smart_print as print


WBI_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


class RateLimitExceededError(Exception):
    pass


class BilibiliHttpClient:
    def __init__(self, config):
        self.config = config
        self.delay_lock = threading.Lock()
        self.wbi_lock = threading.Lock()
        self.session_lock = threading.Lock()
        self.thread_local = threading.local()
        self.current_request_delay = config.request_delay
        self.wbi_mixin_key = None

    def _build_session(self):
        session = requests.Session()
        session.headers.update(self.config.headers)
        pool_size = max(self.config.max_workers, self.config.video_analysis_workers) * 2
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=pool_size,
            pool_maxsize=pool_size,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _get_session(self):
        session = getattr(self.thread_local, "session", None)
        if session is None:
            session = self._build_session()
            self.thread_local.session = session
        return session

    def is_rate_limit_error(self, code, message):
        text = str(message or "")
        return code in (-352, -509, -799) or "请求过于频繁" in text or "稍后再试" in text

    def increase_request_delay(self):
        with self.delay_lock:
            self.current_request_delay = min(
                self.config.max_request_delay, self.current_request_delay + 3
            )

    def recover_request_delay(self):
        with self.delay_lock:
            if self.current_request_delay > self.config.request_delay:
                self.current_request_delay -= 1

    def get_request_delay(self):
        with self.delay_lock:
            return self.current_request_delay + random.uniform(0, 1)

    def reset_session(self):
        with self.session_lock:
            try:
                session = getattr(self.thread_local, "session", None)
                if session is not None:
                    session.close()
            except Exception:
                pass
            self.thread_local.session = self._build_session()

    def _handle_rate_limit_retry(self, request_name, retry_count):
        self.increase_request_delay()
        if retry_count > self.config.max_rate_limit_retries:
            raise RateLimitExceededError(f"{request_name} 连续触发频率限制")

        if retry_count % self.config.rate_limit_retry_before_long_cooldown == 0:
            wait_seconds = self.config.long_rate_limit_cooldown + random.uniform(0, 10)
            print(
                f"⚠️  {request_name} - 连续触发频率限制，进入冷却 "
                f"{wait_seconds:.0f} 秒后再试..."
            )
        else:
            wait_seconds = self.get_request_delay() + retry_count * 5

        print(
            f"⚠️  {request_name} - 请求过于频繁，第 {retry_count} 次重试，"
            f"等待 {wait_seconds:.0f} 秒后继续..."
        )
        time.sleep(wait_seconds)

    def get_json_with_retry(self, url, params=None, request_name="请求"):
        network_retry_count = 0
        rate_limit_retry_count = 0

        while True:
            try:
                response = self._get_session().get(url, params=params, timeout=(10, 20))
                response.raise_for_status()
                data = response.json()
            except requests.exceptions.HTTPError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                response_message = ""
                response_code = None

                if exc.response is not None:
                    try:
                        response_data = exc.response.json()
                        response_code = response_data.get("code")
                        response_message = response_data.get("message", "")
                    except ValueError:
                        response_message = (exc.response.text or "").strip()[:120]

                if status_code in (403, 412, 418, 429) or self.is_rate_limit_error(
                    response_code, response_message
                ):
                    rate_limit_retry_count += 1
                    print(
                        f"⚠️  {request_name} - 触发接口限制(HTTP {status_code})，"
                        f"第 {rate_limit_retry_count} 次重试..."
                    )
                    self._handle_rate_limit_retry(request_name, rate_limit_retry_count)
                    continue

                network_retry_count += 1
                if network_retry_count > self.config.network_retry_limit:
                    raise

                self.reset_session()
                wait_seconds = max(3, self.get_request_delay()) * network_retry_count
                print(
                    f"⚠️  {request_name} - HTTP异常(HTTP {status_code})，"
                    f"第 {network_retry_count} 次重试，{wait_seconds:.0f} 秒后继续..."
                )
                time.sleep(wait_seconds)
                continue
            except requests.exceptions.SSLError:
                network_retry_count += 1
                if network_retry_count > self.config.network_retry_limit:
                    raise

                self.reset_session()
                wait_seconds = max(5, self.get_request_delay()) * network_retry_count
                print(
                    f"⚠️  {request_name} - SSL连接异常，第 {network_retry_count} 次重试，"
                    f"{wait_seconds:.0f} 秒后继续..."
                )
                time.sleep(wait_seconds)
                continue
            except requests.exceptions.RequestException:
                network_retry_count += 1
                if network_retry_count > self.config.network_retry_limit:
                    raise

                self.reset_session()
                wait_seconds = max(3, self.get_request_delay()) * network_retry_count
                print(
                    f"⚠️  {request_name} - 网络波动，第 {network_retry_count} 次重试，"
                    f"{wait_seconds:.0f} 秒后继续..."
                )
                time.sleep(wait_seconds)
                continue

            code = data.get("code")
            message = data.get("message", "")

            if code == 0:
                self.recover_request_delay()
                return data

            if self.is_rate_limit_error(code, message):
                rate_limit_retry_count += 1
                self._handle_rate_limit_retry(request_name, rate_limit_retry_count)
                continue

            return data

    def get_wbi_mixin_key(self):
        with self.wbi_lock:
            if self.wbi_mixin_key:
                return self.wbi_mixin_key

            data = self.get_json_with_retry(self.config.nav_api, request_name="获取WBI签名信息")
            wbi_img = data.get("data", {}).get("wbi_img", {}) or {}
            img_url = wbi_img.get("img_url", "")
            sub_url = wbi_img.get("sub_url", "")
            if not img_url or not sub_url:
                raise ValueError("未能获取WBI签名图片信息")

            img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
            sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
            origin = img_key + sub_key
            self.wbi_mixin_key = "".join(origin[index] for index in WBI_MIXIN_KEY_ENC_TAB)[:32]
            return self.wbi_mixin_key

    def sign_wbi_params(self, params):
        mixin_key = self.get_wbi_mixin_key()
        signed_params = dict(params or {})
        signed_params["wts"] = int(time.time())
        signed_params = {
            key: re.sub(r"[!'()*]", "", str(value))
            for key, value in sorted(signed_params.items())
        }
        query = urlencode(signed_params)
        signed_params["w_rid"] = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
        return signed_params

    def get_wbi_json_with_retry(self, url, params=None, request_name="请求"):
        return self.get_json_with_retry(
            url, params=self.sign_wbi_params(params), request_name=request_name
        )
