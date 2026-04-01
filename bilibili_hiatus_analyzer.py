import subprocess
import os
import requests
import time
import json
from datetime import datetime
import csv
import sys
import random
import hashlib
import re
from urllib.parse import urlencode
from dotenv import load_dotenv
from loguru import logger
load_dotenv()
# ===========================
# 🚀 工业级日志系统配置 (Loguru)
# ===========================
# 1. 移除默认的控制台输出格式
logger.remove()

# 2. 添加带颜色的标准控制台输出
logger.add(sys.stdout, colorize=True,
           format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>")

# 3. 添加文件输出：每天零点自动切割生成新文件，最多保留 30 天
if not os.path.exists("logs"):
    os.makedirs("logs")
logger.add("logs/bilibili_app_{time:YYYY-MM-DD}.log", rotation="00:00", retention="30 days", encoding="utf-8", level="INFO")


def smart_print(*args, **kwargs):
    """智能日志分发器：劫持旧的 print，并根据文本自动分配严重级别"""
    text = kwargs.get('sep', ' ').join(str(arg) for arg in args)
    if not text.strip():  # 忽略纯换行的空打印
        return

    # 依靠你原本精心设计的 Emoji 自动判断日志级别！
    if "❌" in text or "错误" in text or "失败" in text:
        logger.error(text)
    elif "⚠️" in text or "异常" in text or "风控" in text or "重试队列" in text:
        logger.warning(text)
    elif "✅" in text or "成功" in text or "🎉" in text or "🏆" in text:
        logger.success(text)
    else:
        logger.info(text)

# 魔法劫持：将全局的 print 函数替换为我们的智能日志器
print = smart_print

COOKIE = os.getenv("BILIBILI_COOKIE", "")
# 请求头配置 - 模拟真实浏览器
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Origin': 'https://www.bilibili.com',
    'Referer': 'https://www.bilibili.com',
    'Connection': 'keep-alive',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-site',
    'Cookie': COOKIE
}

# API端点配置
FOLLOWINGS_API = "https://api.bilibili.com/x/relation/followings"
FOLLOWING_TAGS_API = "https://api.bilibili.com/x/relation/tags"
SPACE_DYNAMIC_API = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
SPACE_WBI_ARC_SEARCH_API = "https://api.bilibili.com/x/space/wbi/arc/search"
NAV_API = "https://api.bilibili.com/x/web-interface/nav"

# 输出文件名
OUTPUT_CSV = "bilibili_hiatus_ranking.csv"
PROGRESS_JSON = "bilibili_hiatus_progress.json"
ALL_VIDEOS_CSV = "bilibili_all_videos.csv"
VIDEO_DURATION_ANALYSIS_CSV = "bilibili_video_duration_analysis.csv"
VIDEO_DURATION_REPORT_MD = "bilibili_video_duration_report.md"
VIDEO_DURATION_PROGRESS_JSON = "bilibili_video_duration_progress.json"

# 分析模式:
# precise - 精确抓取每个UP主最近一个视频动态的发布时间
# fallback - 精确抓取失败时，才回退到关注列表活跃时间
ANALYSIS_MODE = "precise"

# 是否额外生成全量视频时长分析报告
ENABLE_VIDEO_DURATION_ANALYSIS = True

# 请求延时（秒）- 遵守君子协议
REQUEST_DELAY = 2
MAX_REQUEST_DELAY = 20
NETWORK_RETRY_LIMIT = 3
PRECISE_CACHE_MAX_AGE_HOURS = 12
VIDEO_DURATION_CACHE_MAX_AGE_HOURS = 24
VIDEO_ANALYSIS_START_DELAY = 8
BATCH_SIZE = 25
BATCH_COOLDOWN = 10
LONG_RATE_LIMIT_COOLDOWN = 90
RATE_LIMIT_RETRY_BEFORE_LONG_COOLDOWN = 3
MAX_RATE_LIMIT_RETRIES = 5
FAILED_RETRY_COOLDOWN = 180
MAX_FAILED_RETRY_ROUNDS = 2
MAX_DYNAMIC_PAGES = 8
VIDEO_LIST_PAGE_SIZE = 50
VIDEO_ANALYSIS_BATCH_SIZE = 5
VIDEO_ANALYSIS_BATCH_COOLDOWN = 12

WBI_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52
]

session = requests.Session()
session.headers.update(HEADERS)
current_request_delay = REQUEST_DELAY
wbi_mixin_key = None


# ===========================
# 核心功能函数
# ===========================

def check_cookie():
    """
    检查Cookie是否有效
    """
    if COOKIE == "在这里粘贴你的Cookie" or not COOKIE.strip():
        print("❌ 错误: 请先配置Cookie!")
        print("请在脚本开头的 COOKIE 变量中填入你的B站Cookie")
        sys.exit(1)


class RateLimitExceededError(Exception):
    """
    连续触发频率限制，当前请求暂时放弃
    """
    pass


def is_rate_limit_error(code, message):
    """
    判断是否触发了B站频率限制
    """
    message = str(message or "")
    return code in (-352, -509, -799) or "请求过于频繁" in message or "稍后再试" in message


def increase_request_delay():
    """
    触发风控后，提高后续请求间隔
    """
    global current_request_delay
    current_request_delay = min(MAX_REQUEST_DELAY, current_request_delay + 3)


def recover_request_delay():
    """
    请求恢复正常后，逐步回落到基础延时
    """
    global current_request_delay
    if current_request_delay > REQUEST_DELAY:
        current_request_delay -= 1


def get_request_delay():
    """
    获取当前请求间隔，并增加轻微随机抖动
    """
    return current_request_delay + random.uniform(0, 1)


def reset_session():
    """
    重建HTTP会话，缓解连接池或TLS状态异常
    """
    global session
    try:
        session.close()
    except Exception:
        pass

    session = requests.Session()
    session.headers.update(HEADERS)


def get_json_with_retry(url, params=None, request_name="请求"):
    """
    请求B站API，遇到风控时自动等待并重试
    """
    network_retry_count = 0
    rate_limit_retry_count = 0

    while True:
        try:
            response = session.get(url, params=params, timeout=(10, 20))
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.SSLError as e:
            network_retry_count += 1
            if network_retry_count > NETWORK_RETRY_LIMIT:
                raise

            reset_session()
            wait_seconds = max(5, current_request_delay) * network_retry_count
            print(
                f"⚠️  {request_name} - SSL连接异常，第 {network_retry_count} 次重试，"
                f"{wait_seconds:.0f} 秒后继续..."
            )
            time.sleep(wait_seconds)
            continue
        except requests.exceptions.RequestException as e:
            network_retry_count += 1
            if network_retry_count > NETWORK_RETRY_LIMIT:
                raise

            reset_session()
            wait_seconds = max(3, current_request_delay) * network_retry_count
            print(f"⚠️  {request_name} - 网络波动，第 {network_retry_count} 次重试，{wait_seconds:.0f} 秒后继续...")
            time.sleep(wait_seconds)
            continue

        code = data.get('code')
        message = data.get('message', '')

        if code == 0:
            recover_request_delay()
            return data

        if is_rate_limit_error(code, message):
            rate_limit_retry_count += 1
            increase_request_delay()
            if rate_limit_retry_count > MAX_RATE_LIMIT_RETRIES:
                raise RateLimitExceededError(f"{request_name} 连续触发频率限制")
            if rate_limit_retry_count % RATE_LIMIT_RETRY_BEFORE_LONG_COOLDOWN == 0:
                wait_seconds = LONG_RATE_LIMIT_COOLDOWN + random.uniform(0, 10)
                print(
                    f"⚠️  {request_name} - 连续触发频率限制，"
                    f"进入冷却 {wait_seconds:.0f} 秒后再试..."
                )
            else:
                wait_seconds = get_request_delay() + rate_limit_retry_count * 5
            print(
                f"⚠️  {request_name} - 请求过于频繁，"
                f"第 {rate_limit_retry_count} 次重试，等待 {wait_seconds:.0f} 秒后继续..."
            )
            time.sleep(wait_seconds)
            continue

        return data


def get_wbi_mixin_key():
    """
    获取WBI签名所需的mixin key
    """
    global wbi_mixin_key
    if wbi_mixin_key:
        return wbi_mixin_key

    data = get_json_with_retry(NAV_API, request_name="获取WBI签名信息")
    wbi_img = data.get('data', {}).get('wbi_img', {}) or {}
    img_url = wbi_img.get('img_url', '')
    sub_url = wbi_img.get('sub_url', '')

    if not img_url or not sub_url:
        raise ValueError("未能获取WBI签名图片信息")

    img_key = img_url.rsplit('/', 1)[-1].split('.')[0]
    sub_key = sub_url.rsplit('/', 1)[-1].split('.')[0]
    origin = img_key + sub_key
    wbi_mixin_key = ''.join(origin[index] for index in WBI_MIXIN_KEY_ENC_TAB)[:32]
    return wbi_mixin_key


def sign_wbi_params(params):
    """
    为WBI接口参数添加签名
    """
    mixin_key = get_wbi_mixin_key()
    signed_params = dict(params or {})
    signed_params['wts'] = int(time.time())
    signed_params = {
        key: re.sub(r"[!'()*]", '', str(value))
        for key, value in sorted(signed_params.items())
    }
    query = urlencode(signed_params)
    signed_params['w_rid'] = hashlib.md5((query + mixin_key).encode('utf-8')).hexdigest()
    return signed_params


def get_wbi_json_with_retry(url, params=None, request_name="请求"):
    """
    请求需要WBI签名的B站API
    """
    return get_json_with_retry(url, params=sign_wbi_params(params), request_name=request_name)


def load_progress():
    """
    加载已成功获取的结果，便于断点续跑
    """
    try:
        with open(PROGRESS_JSON, 'r', encoding='utf-8') as progress_file:
            data = json.load(progress_file)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"⚠️  读取进度文件失败，将从头开始: {e}")
        return {}

    raw_results_by_mid = data.get('results_by_mid', {})
    if not isinstance(raw_results_by_mid, dict):
        return {}

    results_by_mid = {}
    for mid, result in raw_results_by_mid.items():
        if not isinstance(result, dict):
            continue

        # 丢弃早期错误缓存：视频接口成功但日期被解析成“未知日期”
        if result.get('data_source') == 'video_api' and result.get('upload_date') == '未知日期':
            continue

        results_by_mid[mid] = result

    return results_by_mid


def save_progress(results_by_mid):
    """
    保存已成功获取的结果
    """
    try:
        payload = {
            'saved_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'results_by_mid': results_by_mid
        }
        with open(PROGRESS_JSON, 'w', encoding='utf-8') as progress_file:
            json.dump(payload, progress_file, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️  保存进度文件失败: {e}")


def load_video_duration_progress():
    """
    加载全量视频时长分析进度
    """
    try:
        with open(VIDEO_DURATION_PROGRESS_JSON, 'r', encoding='utf-8') as progress_file:
            data = json.load(progress_file)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"⚠️  读取视频时长分析进度失败，将重新抓取: {e}")
        return {}

    ups = data.get('ups', {})
    if not isinstance(ups, dict):
        return {}

    return ups


def save_video_duration_progress(progress):
    """
    保存全量视频时长分析进度
    """
    try:
        payload = {
            'saved_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'ups': progress
        }
        with open(VIDEO_DURATION_PROGRESS_JSON, 'w', encoding='utf-8') as progress_file:
            json.dump(payload, progress_file, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️  保存视频时长分析进度失败: {e}")


def is_cache_expired(cached_at, max_age_hours):
    """
    判断缓存是否已经超过最大允许存活时间
    """
    cached_timestamp = normalize_timestamp(cached_at)
    if not cached_timestamp:
        return True

    max_age_seconds = max_age_hours * 3600
    return time.time() - cached_timestamp >= max_age_seconds


def should_refresh_precise_cache(following, cached_result):
    """
    判断某个UP主的“最后一个视频”缓存是否需要重新抓取
    """
    if not isinstance(cached_result, dict):
        return True

    data_source = cached_result.get('data_source')
    if data_source not in ('video_api', 'no_video'):
        return True

    if is_cache_expired(cached_result.get('cached_at'), PRECISE_CACHE_MAX_AGE_HOURS):
        return True

    following_mtime = normalize_timestamp(following.get('mtime'))
    if not following_mtime:
        return False

    if data_source == 'video_api':
        cached_upload_timestamp = normalize_timestamp(cached_result.get('upload_timestamp'))
        if not cached_upload_timestamp:
            return True
        return following_mtime > cached_upload_timestamp

    cached_at = normalize_timestamp(cached_result.get('cached_at'))
    if not cached_at:
        return True
    return following_mtime > cached_at


def should_refresh_video_duration_cache(following, progress_entry):
    """
    判断某个UP主的全量视频时长分析缓存是否需要重新抓取
    """
    if not isinstance(progress_entry, dict):
        return True

    summary = progress_entry.get('summary', {})
    if not isinstance(summary, dict) or not summary:
        return True

    if is_cache_expired(progress_entry.get('cached_at'), VIDEO_DURATION_CACHE_MAX_AGE_HOURS):
        return True

    following_mtime = normalize_timestamp(following.get('mtime'))
    if not following_mtime:
        return False

    latest_publish_timestamp = normalize_timestamp(summary.get('latest_publish_timestamp'))
    if not latest_publish_timestamp:
        return True

    return following_mtime > latest_publish_timestamp


def refresh_result_runtime_fields(result):
    """
    每次运行时重新计算依赖当前时间的字段，避免直接复用旧天数
    """
    if not isinstance(result, dict):
        return result

    data_source = result.get('data_source')
    if data_source == 'video_api':
        upload_timestamp = normalize_timestamp(result.get('upload_timestamp'))
        if upload_timestamp:
            result['upload_date'] = timestamp_to_date(upload_timestamp)
            days_since = calculate_days_since(upload_timestamp)
            result['days_since_update'] = days_since
            result['days_since_last_video'] = days_since
    elif data_source == 'followings_mtime':
        activity_timestamp = normalize_timestamp(result.get('activity_timestamp'))
        if activity_timestamp:
            result['upload_date'] = timestamp_to_date(activity_timestamp)
            days_since = calculate_days_since(activity_timestamp)
            result['days_since_update'] = days_since
            result['days_since_last_video'] = days_since

    return result


def parse_view_count(value):
    """
    解析播放量文本，兼容 3002 / 1.2万 / -- 等格式
    """
    text = str(value or '').strip()
    if not text or text == '--':
        return 0

    text = text.replace(',', '')
    if text.endswith('万'):
        try:
            return int(float(text[:-1]) * 10000)
        except ValueError:
            return 0

    try:
        return int(float(text))
    except ValueError:
        return 0


def parse_duration_to_seconds(duration_text):
    """
    将时长文本转换为秒，兼容 MM:SS / HH:MM:SS
    """
    text = str(duration_text or '').strip()
    if not text:
        return 0

    parts = text.split(':')
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return 0

    if len(numbers) == 2:
        minutes, seconds = numbers
        return minutes * 60 + seconds
    if len(numbers) == 3:
        hours, minutes, seconds = numbers
        return hours * 3600 + minutes * 60 + seconds

    return 0


def categorize_duration(duration_seconds):
    """
    根据秒数划分视频时长类别
    """
    if duration_seconds <= 30:
        return '短视频(0~30s)'
    if duration_seconds <= 60:
        return '中视频(30~60s)'
    if duration_seconds <= 240:
        return '中长视频(60~240s)'
    return '长视频(240s+)'


def format_ratio(count, total):
    """
    格式化占比
    """
    if total <= 0:
        return '0.00%'
    return f"{count / total * 100:.2f}%"


def build_homepage_url(mid):
    """
    构建UP主主页链接
    """
    return f"https://space.bilibili.com/{mid}"


def normalize_group_ids(group_ids):
    """
    规范化关注分组ID列表
    """
    if not group_ids:
        return []
    if isinstance(group_ids, list):
        return [int(group_id) for group_id in group_ids]
    return [int(group_ids)]


def format_group_ids(group_ids):
    """
    格式化分组ID列表为文本
    """
    normalized_ids = normalize_group_ids(group_ids)
    if not normalized_ids:
        return '0'
    return ','.join(str(group_id) for group_id in normalized_ids)


def resolve_group_names(group_ids, tag_name_map):
    """
    将分组ID解析为分组名称列表
    """
    normalized_ids = normalize_group_ids(group_ids)
    if not normalized_ids:
        return ['默认分组']

    group_names = []
    for group_id in normalized_ids:
        group_names.append(tag_name_map.get(group_id, f'未知分组({group_id})'))
    return group_names


def format_group_names(group_ids, tag_name_map):
    """
    格式化分组名称为文本
    """
    return ','.join(resolve_group_names(group_ids, tag_name_map))


def normalize_timestamp(value):
    """
    规范化时间戳，兼容字符串、毫秒时间戳等格式
    """
    if value in (None, '', 0, '0'):
        return 0

    try:
        timestamp = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0

    # 毫秒时间戳转秒
    if timestamp > 10**12:
        timestamp //= 1000

    return max(timestamp, 0)


def build_result_item(video_info):
    """
    将视频信息转换为最终输出结构
    """
    days_since = calculate_days_since(video_info['upload_timestamp'])
    video_url = f"https://www.bilibili.com/video/{video_info['bvid']}"

    return {
        'uploader_name': video_info['uploader_name'],
        'uploader_id': video_info['uploader_id'],
        'uploader_homepage': build_homepage_url(video_info['uploader_id']),
        'following_group_ids': '',
        'following_group_names': '',
        'published_video_count': 0,
        'latest_video_title': video_info['video_title'],
        'upload_timestamp': normalize_timestamp(video_info['upload_timestamp']),
        'upload_date': timestamp_to_date(video_info['upload_timestamp']),
        'days_since_update': days_since,
        'days_since_last_video': days_since,
        'view_count': video_info['view_count'],
        'video_url': video_info.get('video_url') or video_url,
        'data_source': 'video_api'
    }


def build_following_result_item(following):
    """
    基于关注列表接口的mtime生成稳定结果
    """
    mid = following.get('mid')
    uname = following.get('uname', '未知UP主')
    activity_timestamp = following.get('mtime') or 0

    return {
        'uploader_name': uname,
        'uploader_id': mid,
        'uploader_homepage': build_homepage_url(mid),
        'following_group_ids': following.get('group_id_text', ''),
        'following_group_names': following.get('group_name_text', '默认分组'),
        'published_video_count': 0,
        'latest_video_title': '未抓取视频详情（回退模式，基于关注列表活跃时间）',
        'activity_timestamp': normalize_timestamp(activity_timestamp),
        'upload_date': timestamp_to_date(activity_timestamp),
        'days_since_update': calculate_days_since(activity_timestamp),
        'days_since_last_video': calculate_days_since(activity_timestamp),
        'view_count': 0,
        'video_url': '',
        'data_source': 'followings_mtime'
    }


def build_no_video_result_item(following):
    """
    记录确认没有公开视频的UP主
    """
    return {
        'uploader_name': following.get('uname', '未知UP主'),
        'uploader_id': following.get('mid'),
        'uploader_homepage': build_homepage_url(following.get('mid')),
        'following_group_ids': following.get('group_id_text', ''),
        'following_group_names': following.get('group_name_text', '默认分组'),
        'published_video_count': 0,
        'latest_video_title': '暂无公开视频',
        'upload_date': '未知日期',
        'days_since_update': 0,
        'days_since_last_video': 0,
        'view_count': 0,
        'video_url': '',
        'data_source': 'no_video'
    }


def get_user_mid():
    """
    获取当前登录用户的mid（用户ID）
    """
    try:
        url = 'https://api.bilibili.com/x/web-interface/nav'
        data = get_json_with_retry(url, request_name="获取当前用户信息")
        if data.get('code') == 0:
            return data.get('data', {}).get('mid')
        else:
            print(f"❌ 获取用户信息失败: {data.get('message')}")
            return None
    except Exception as e:
        print(f"❌ 获取用户信息出错: {e}")
        return None


def get_following_tag_map():
    """
    获取关注分组ID到分组名称的映射
    """
    try:
        data = get_json_with_retry(FOLLOWING_TAGS_API, request_name="获取关注分组列表")
        if data.get('code') != 0:
            print(f"⚠️  获取关注分组失败: {data.get('message', '未知错误')}")
            return {}

        tag_map = {}
        for item in data.get('data', []) or []:
            tag_id = item.get('tagid')
            name = item.get('name')
            if tag_id is None or not name:
                continue
            tag_map[int(tag_id)] = name

        return tag_map
    except Exception as e:
        print(f"⚠️  获取关注分组出错: {e}")
        return {}


def get_followings_list():
    """
    获取关注列表中的所有UP主
    返回: UP主信息列表，每个元素包含 mid, uname 等信息
    """
    print("📥 正在获取关注列表...")

    # 先获取当前用户的mid
    user_mid = get_user_mid()
    if not user_mid:
        return None

    tag_name_map = get_following_tag_map()

    all_followings = []
    page = 1
    page_size = 50  # 每页获取数量

    while True:
        try:
            params = {
                'vmid': user_mid,  # 添加用户ID参数
                'pn': page,
                'ps': page_size,
                'order': 'desc'
            }

            data = get_json_with_retry(
                FOLLOWINGS_API,
                params=params,
                request_name=f"获取关注列表第 {page} 页"
            )

            # 检查响应状态
            if data.get('code') != 0:
                print(f"❌ API返回错误: {data.get('message', '未知错误')}")
                print(f"   错误代码: {data.get('code')}")
                print(f"   完整响应: {data}")
                if data.get('code') == -101:
                    print("提示: Cookie可能已过期，请重新获取")
                elif data.get('code') == -352:
                    print("提示: 触发风控，请稍后重试或更换网络环境")
                return None

            followings = data.get('data', {}).get('list', [])

            if not followings:
                break

            for following in followings:
                group_ids = normalize_group_ids(following.get('tag'))
                following['group_ids'] = group_ids
                following['group_id_text'] = format_group_ids(group_ids)
                following['group_name_text'] = format_group_names(group_ids, tag_name_map)

            all_followings.extend(followings)
            print(f"   已获取 {len(all_followings)} 位UP主...")

            # 检查是否还有更多数据
            total = data.get('data', {}).get('total', 0)
            if len(all_followings) >= total:
                break

            page += 1
            time.sleep(get_request_delay())

        except requests.exceptions.RequestException as e:
            print(f"❌ 网络请求失败: {e}")
            return None
        except Exception as e:
            print(f"❌ 解析数据失败: {e}")
            return None

    print(f"✅ 成功获取 {len(all_followings)} 位关注的UP主\n")
    return all_followings


def get_all_videos_for_up(mid, uname):
    """
    获取指定UP主的全部投稿视频信息
    """
    all_videos = []
    page_no = 1
    total_count = None

    while True:
        data = get_wbi_json_with_retry(
            SPACE_WBI_ARC_SEARCH_API,
            params={
                'mid': mid,
                'ps': VIDEO_LIST_PAGE_SIZE,
                'pn': page_no,
                'order': 'pubdate'
            },
            request_name=f"获取 {uname} 的全部视频第 {page_no} 页"
        )

        if data.get('code') != 0:
            print(f"   ⚠️  {uname} - 获取全部视频失败: {data.get('message', '未知错误')}")
            return None

        payload = data.get('data', {}) or {}
        total_count = total_count or ((payload.get('page') or {}).get('count') or 0)
        video_list = ((payload.get('list') or {}).get('vlist') or [])

        if not video_list:
            break

        for video in video_list:
            duration_text = video.get('length', '')
            duration_seconds = parse_duration_to_seconds(duration_text)
            jump_url = video.get('jump_url') or ''
            if jump_url.startswith('//'):
                jump_url = f"https:{jump_url}"
            elif not jump_url and video.get('bvid'):
                jump_url = f"https://www.bilibili.com/video/{video.get('bvid')}"

            all_videos.append({
                'uploader_name': uname,
                'uploader_id': mid,
                'video_title': video.get('title', '未知标题'),
                'bvid': video.get('bvid', ''),
                'publish_date': timestamp_to_date(video.get('created')),
                'publish_timestamp': normalize_timestamp(video.get('created')),
                'duration_text': duration_text,
                'duration_seconds': duration_seconds,
                'duration_category': categorize_duration(duration_seconds),
                'view_count': parse_view_count(video.get('play', 0)),
                'video_url': jump_url
            })

        if total_count and len(all_videos) >= total_count:
            break

        page_no += 1
        time.sleep(get_request_delay())

    return all_videos


def build_video_duration_summary(following, videos):
    """
    基于单个UP主全部视频构建时长分析汇总
    """
    total_videos = len(videos)
    short_count = sum(1 for video in videos if video['duration_category'] == '短视频(0~30s)')
    medium_count = sum(1 for video in videos if video['duration_category'] == '中视频(30~60s)')
    medium_long_count = sum(1 for video in videos if video['duration_category'] == '中长视频(60~240s)')
    long_count = sum(1 for video in videos if video['duration_category'] == '长视频(240s+)')
    total_duration_seconds = sum(video['duration_seconds'] for video in videos)
    average_duration_seconds = int(total_duration_seconds / total_videos) if total_videos else 0
    latest_publish_timestamp = max(
        (normalize_timestamp(video.get('publish_timestamp')) for video in videos),
        default=0
    )

    return {
        'uploader_name': following.get('uname', '未知UP主'),
        'uploader_id': following.get('mid'),
        'total_videos': total_videos,
        'latest_publish_timestamp': latest_publish_timestamp,
        'total_duration_seconds': total_duration_seconds,
        'average_duration_seconds': average_duration_seconds,
        'average_duration_text': seconds_to_duration_text(average_duration_seconds),
        'short_video_count': short_count,
        'short_video_ratio': format_ratio(short_count, total_videos),
        'medium_video_count': medium_count,
        'medium_video_ratio': format_ratio(medium_count, total_videos),
        'medium_long_video_count': medium_long_count,
        'medium_long_video_ratio': format_ratio(medium_long_count, total_videos),
        'long_video_count': long_count,
        'long_video_ratio': format_ratio(long_count, total_videos)
    }


def seconds_to_duration_text(duration_seconds):
    """
    将秒数格式化为 HH:MM:SS 或 MM:SS
    """
    seconds = max(int(duration_seconds or 0), 0)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    remain_seconds = seconds % 60

    if hours:
        return f"{hours:02d}:{minutes:02d}:{remain_seconds:02d}"
    return f"{minutes:02d}:{remain_seconds:02d}"


def enrich_results_with_profile_and_counts(results, duration_progress=None, followings=None):
    """
    为排行结果补充UP主主页链接、关注分组与发布视频数量
    """
    progress = duration_progress or {}
    following_map = {}
    for following in followings or []:
        following_map[str(following.get('mid'))] = following

    for result in results:
        uploader_id = result.get('uploader_id')
        result['uploader_homepage'] = build_homepage_url(uploader_id)
        following = following_map.get(str(uploader_id), {})
        if following:
            result['following_group_ids'] = following.get('group_id_text', '0')
            result['following_group_names'] = following.get('group_name_text', '默认分组')
        else:
            result['following_group_ids'] = result.get('following_group_ids') or '0'
            result['following_group_names'] = result.get('following_group_names') or '默认分组'

        progress_entry = progress.get(str(uploader_id), {})
        summary = progress_entry.get('summary', {}) if isinstance(progress_entry, dict) else {}
        if summary:
            result['published_video_count'] = summary.get('total_videos', result.get('published_video_count', 0))
        else:
            result.setdefault('published_video_count', 0)

    return results


def save_precise_result(mid, result_item, results_by_mid, cached_video_results):
    """
    保存单个UP主的精确抓取结果
    """
    mid_str = str(mid)
    result_item['cached_at'] = int(time.time())
    results_by_mid[mid_str] = result_item
    cached_video_results[mid_str] = result_item
    save_progress(cached_video_results)


def handle_precise_video_result(following, video_info, results_by_mid, cached_video_results):
    """
    处理精确抓取的返回结果
    返回 True 表示已处理完成，False 表示需要稍后重试
    """
    mid = following.get('mid')
    uname = following.get('uname', '未知UP主')

    if video_info:
        result_item = build_result_item(video_info)
        result_item['following_group_ids'] = following.get('group_id_text', '')
        result_item['following_group_names'] = following.get('group_name_text', '默认分组')
        save_precise_result(mid, result_item, results_by_mid, cached_video_results)
        print(
            f"   ✅ 最后视频发布于 {result_item['upload_date']}，"
            f"距离现在 {result_item['days_since_last_video']} 天"
        )
        return True

    if video_info is False:
        result_item = build_no_video_result_item(following)
        save_precise_result(mid, result_item, results_by_mid, cached_video_results)
        print(f"   📭 {uname} - 暂无公开视频")
        return True

    print(f"   ⚠️  {uname} - 本次请求未拿到有效结果，稍后重试")
    return False


def run_precise_fetch_round(followings, label, results_by_mid, cached_video_results):
    """
    执行一轮精确抓取，返回仍需重试的UP主列表
    """
    remaining_followings = []

    for idx, following in enumerate(followings, 1):
        mid = following.get('mid')
        uname = following.get('uname', '未知UP主')
        print(f"[{label} {idx}/{len(followings)}] 正在获取 {uname} 的最后一个视频...")

        try:
            video_info = get_latest_video(mid, uname)
        except RateLimitExceededError:
            print(f"   ⏭️  {uname} - 当前风控较严，先加入稍后重试队列")
            remaining_followings.append(following)
            continue

        if not handle_precise_video_result(following, video_info, results_by_mid, cached_video_results):
            remaining_followings.append(following)

        if idx < len(followings):
            time.sleep(get_request_delay())

    return remaining_followings


def extract_video_info_from_dynamic_item(item, uname, mid):
    """
    从动态条目中提取视频信息
    """
    if item.get('type') != 'DYNAMIC_TYPE_AV':
        return None

    modules = item.get('modules', {})
    module_author = modules.get('module_author', {}) or {}
    module_dynamic = modules.get('module_dynamic', {}) or {}
    major = module_dynamic.get('major', {}) or {}
    archive = major.get('archive', {}) or {}

    if not archive:
        return None

    upload_timestamp = normalize_timestamp(module_author.get('pub_ts'))
    jump_url = archive.get('jump_url') or ''
    if jump_url.startswith('//'):
        jump_url = f"https:{jump_url}"

    return {
        'uploader_name': uname,
        'uploader_id': mid,
        'video_title': archive.get('title', '未知标题'),
        'bvid': archive.get('bvid', ''),
        'upload_timestamp': upload_timestamp,
        'view_count': parse_view_count((archive.get('stat') or {}).get('play', 0)),
        'video_url': jump_url
    }


def get_latest_video(mid, uname):
    """
    获取指定UP主的最新视频信息

    参数:
        mid: UP主的用户ID
        uname: UP主的用户名

    返回:
        字典，包含最新视频的详细信息，如果没有视频则返回None
    """
    try:
        offset = ''

        for page_no in range(1, MAX_DYNAMIC_PAGES + 1):
            data = get_json_with_retry(
                SPACE_DYNAMIC_API,
                params={'host_mid': mid, 'offset': offset},
                request_name=f"获取 {uname} 的视频动态第 {page_no} 页"
            )

            if data.get('code') != 0:
                print(f"   ⚠️  {uname} - API返回错误: {data.get('message', '未知错误')}")
                return None

            payload = data.get('data', {}) or {}
            items = payload.get('items', []) or []

            page_video_candidates = []
            for item in items:
                video_info = extract_video_info_from_dynamic_item(item, uname, mid)
                if video_info:
                    page_video_candidates.append(video_info)

            if page_video_candidates:
                return max(page_video_candidates, key=lambda x: x.get('upload_timestamp', 0))

            has_more = payload.get('has_more')
            offset = payload.get('offset', '')
            if not has_more or not offset:
                break

            time.sleep(get_request_delay())

        print(f"   📭 {uname} - 最近动态中未找到视频")
        return False

    except RateLimitExceededError:
        raise
    except requests.exceptions.RequestException as e:
        print(f"   ❌ {uname} - 网络请求失败: {e}")
        return None
    except Exception as e:
        print(f"   ❌ {uname} - 解析数据失败: {e}")
        return None


def timestamp_to_date(timestamp):
    """
    将Unix时间戳转换为日期字符串

    参数:
        timestamp: Unix时间戳

    返回:
        日期字符串 (YYYY-MM-DD HH:MM:SS)
    """
    try:
        timestamp = normalize_timestamp(timestamp)
        if not timestamp or timestamp <= 0:
            return "未知日期"
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except:
        return "未知日期"


def calculate_days_since(timestamp):
    """
    计算从指定时间戳到现在经过了多少天

    参数:
        timestamp: Unix时间戳

    返回:
        天数（整数）
    """
    try:
        timestamp = normalize_timestamp(timestamp)
        if not timestamp or timestamp <= 0:
            return 0
        video_date = datetime.fromtimestamp(timestamp)
        current_date = datetime.now()
        delta = current_date - video_date
        return delta.days
    except:
        return 0


def analyze_hiatus():
    """
    主分析函数：获取数据、分析、生成排行榜
    """
    # 检查Cookie配置
    check_cookie()

    print("=" * 60)
    print("🎯 B站催更分析器 - 寻找你关注的UP主中的「鸽王」")
    print("=" * 60)
    print()

    # 获取关注列表
    followings = get_followings_list()

    if not followings:
        print("❌ 无法获取关注列表，程序退出")
        return

    cached_video_results = load_progress()
    if cached_video_results:
        print(f"♻️  已加载 {len(cached_video_results)} 条历史抓取缓存。")

    results_by_mid = {}
    pending_followings = []
    for following in followings:
        mid = str(following.get('mid'))
        cached_result = cached_video_results.get(mid)
        if cached_result and not should_refresh_precise_cache(following, cached_result):
            refreshed_result = dict(cached_result)
            refresh_result_runtime_fields(refreshed_result)
            results_by_mid[mid] = refreshed_result
        else:
            pending_followings.append(following)

    print("🔍 正在精确抓取每位UP主最后一个视频时间...")
    if ANALYSIS_MODE == "fallback":
        print("   如遇到无法补抓的UP主，将回退到关注列表活跃时间。")
    else:
        print("   当前为精确模式：仅接受视频动态时间作为最终结果。")
    print()

    failed_followings = []
    if pending_followings:
        print(f"🎬 仍有 {len(pending_followings)} 位UP主需要精确抓取。")
        print(f"⏸️  先冷却 {VIDEO_ANALYSIS_START_DELAY} 秒，降低进入视频动态接口时立刻触发风控的概率...")
        time.sleep(VIDEO_ANALYSIS_START_DELAY)
        for start in range(0, len(pending_followings), BATCH_SIZE):
            batch = pending_followings[start:start + BATCH_SIZE]
            batch_label = f"{start + 1}-{start + len(batch)}"
            failed_followings.extend(
                run_precise_fetch_round(batch, batch_label, results_by_mid, cached_video_results)
            )
            if start + BATCH_SIZE < len(pending_followings):
                cooldown = BATCH_COOLDOWN + random.uniform(0, 5)
                print(f"⏸️  已完成 {start + len(batch)} 位UP主，批次冷却 {cooldown:.0f} 秒后继续...")
                time.sleep(cooldown)

    for retry_round in range(1, MAX_FAILED_RETRY_ROUNDS + 1):
        if not failed_followings:
            break

        cooldown = FAILED_RETRY_COOLDOWN * retry_round + random.uniform(0, 10)
        print()
        print(f"🔁  第 {retry_round} 轮补抓开始，先冷却 {cooldown:.0f} 秒...")
        time.sleep(cooldown)

        remaining_followings = []
        current_round_followings = failed_followings
        failed_followings = []
        remaining_followings.extend(
            run_precise_fetch_round(
                current_round_followings,
                f"补抓第{retry_round}轮",
                results_by_mid,
                cached_video_results
            )
        )

        failed_followings = remaining_followings

    if ANALYSIS_MODE == "fallback" and failed_followings:
        print(f"\n↩️  仍有 {len(failed_followings)} 位UP主未完成精确抓取，回退到关注列表活跃时间。")
        for following in failed_followings:
            mid = str(following.get('mid'))
            if mid not in results_by_mid:
                results_by_mid[mid] = build_following_result_item(following)

    duration_progress = load_video_duration_progress()
    results = list(results_by_mid.values())
    enrich_results_with_profile_and_counts(results, duration_progress, followings)
    if not results:
        print("\n❌ 未能获取到任何视频数据")
        return

    if ANALYSIS_MODE == "precise" and failed_followings:
        print(f"\n⚠️  仍有 {len(failed_followings)} 位UP主因频率限制未获取成功。")
        print(f"   下次运行会自动复用已保存进度，继续补抓剩余UP主。")

    # 按未更新天数降序排序（找出鸽王）
    results.sort(key=lambda x: x['days_since_update'], reverse=True)

    print("\n" + "=" * 60)
    print("🏆 B站鸽王排行榜 - Top 10")
    print("=" * 60)
    print()

    # 显示前10名
    for idx, result in enumerate(results[:10], 1):
        print(f"第 {idx} 名: {result['uploader_name']}")
        print(f"   ⏰ 已鸽 {result['days_since_update']} 天")
        print(f"   ⌛ 距离最后一个视频发布: {result.get('days_since_last_video', result['days_since_update'])} 天")
        print(f"   🏠 主页: {result.get('uploader_homepage', '暂无')}")
        print(f"   🗂️  关注分组ID: {result.get('following_group_ids', '') or '无'}")
        print(f"   🏷️  关注分组: {result.get('following_group_names', '') or '默认分组'}")
        print(f"   🎞️  发布视频数量: {result.get('published_video_count', 0)}")
        print(f"   📺 最新视频: {result['latest_video_title']}")
        print(f"   📅 发布日期: {result['upload_date']}")
        print(f"   👁️  播放量: {result['view_count']:,}")
        print(f"   🧭 数据来源: {result.get('data_source', 'unknown')}")
        print(f"   🔗 链接: {result['video_url'] or '暂无'}")
        print()

    # 保存到CSV文件
    save_to_csv(results)
    duration_progress = analyze_video_durations(followings)
    if duration_progress:
        enrich_results_with_profile_and_counts(results, duration_progress, followings)
        save_to_csv(results)


def save_to_csv(results):
    """
    将结果保存为CSV文件

    参数:
        results: 包含所有UP主数据的列表
    """
    try:
        with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8-sig') as csvfile:
            fieldnames = [
                'uploader_name',
                'uploader_id',
                'uploader_homepage',
                'following_group_ids',
                'following_group_names',
                'published_video_count',
                'latest_video_title',
                'upload_date',
                'days_since_update',
                'days_since_last_video',
                'view_count',
                'video_url',
                'data_source'
            ]

            chinese_headers = {
                'uploader_name': 'UP主姓名',
                'uploader_id': 'UP主UID',
                'uploader_homepage': 'UP主主页链接',
                'following_group_ids': '关注分组ID',
                'following_group_names': '关注分组名称',
                'published_video_count': '发布视频数量',
                'latest_video_title': '最新视频标题',
                'upload_date': '最后活跃/发布日期',
                'days_since_update': '未更新天数',
                'days_since_last_video': '距离最后一个视频发布(天)',
                'view_count': '最新视频播放量',
                'video_url': '视频链接',
                'data_source': '数据来源'
            }

            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore')
            writer.writerow(chinese_headers)
            for result in results:
                writer.writerow(result)

        print("=" * 60)
        print(f"✅ 排行榜已保存到文件: {OUTPUT_CSV}")
        print(f"📊 共分析了 {len(results)} 位UP主")
        print("=" * 60)

    except Exception as e:
        print(f"❌ 保存CSV文件失败: {e}")


def save_all_videos_to_csv(video_rows):
    """
    保存所有UP主的视频明细
    """
    try:
        with open(ALL_VIDEOS_CSV, 'w', newline='', encoding='utf-8-sig') as csvfile:
            fieldnames = [
                'uploader_name',
                'uploader_id',
                'video_title',
                'bvid',
                'publish_date',
                'publish_timestamp',
                'duration_text',
                'duration_seconds',
                'duration_category',
                'view_count',
                'video_url'
            ]
            chinese_headers = {
                'uploader_name': 'UP主姓名',
                'uploader_id': 'UP主UID',
                'video_title': '视频标题',
                'bvid': 'BVID',
                'publish_date': '发布日期',
                'publish_timestamp': '发布时间戳',
                'duration_text': '视频时长',
                'duration_seconds': '视频时长(秒)',
                'duration_category': '时长分类',
                'view_count': '播放量',
                'video_url': '视频链接'
            }
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore')
            writer.writerow(chinese_headers)
            for row in video_rows:
                writer.writerow(row)
    except Exception as e:
        print(f"❌ 保存视频明细CSV失败: {e}")


def save_video_duration_analysis_to_csv(summary_rows):
    """
    保存每个UP主的视频时长分析汇总
    """
    try:
        with open(VIDEO_DURATION_ANALYSIS_CSV, 'w', newline='', encoding='utf-8-sig') as csvfile:
            fieldnames = [
                'uploader_name',
                'uploader_id',
                'total_videos',
                'total_duration_seconds',
                'average_duration_seconds',
                'average_duration_text',
                'short_video_count',
                'short_video_ratio',
                'medium_video_count',
                'medium_video_ratio',
                'medium_long_video_count',
                'medium_long_video_ratio',
                'long_video_count',
                'long_video_ratio'
            ]
            chinese_headers = {
                'uploader_name': 'UP主姓名',
                'uploader_id': 'UP主UID',
                'total_videos': '视频总数',
                'total_duration_seconds': '总时长(秒)',
                'average_duration_seconds': '平均时长(秒)',
                'average_duration_text': '平均时长',
                'short_video_count': '短视频数量(0~30s)',
                'short_video_ratio': '短视频占比',
                'medium_video_count': '中视频数量(30~60s)',
                'medium_video_ratio': '中视频占比',
                'medium_long_video_count': '中长视频数量(60~240s)',
                'medium_long_video_ratio': '中长视频占比',
                'long_video_count': '长视频数量(240s+)',
                'long_video_ratio': '长视频占比'
            }
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore')
            writer.writerow(chinese_headers)
            for row in summary_rows:
                writer.writerow(row)
    except Exception as e:
        print(f"❌ 保存视频时长分析CSV失败: {e}")


def save_video_duration_report(summary_rows, total_video_count):
    """
    生成Markdown分析报告
    """
    try:
        total_up_count = len(summary_rows)
        short_total = sum(row['short_video_count'] for row in summary_rows)
        medium_total = sum(row['medium_video_count'] for row in summary_rows)
        medium_long_total = sum(row['medium_long_video_count'] for row in summary_rows)
        long_total = sum(row['long_video_count'] for row in summary_rows)

        report_lines = [
            "# B站关注UP视频时长分析报告",
            "",
            f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 分析UP主数量: {total_up_count}",
            f"- 分析视频总数: {total_video_count}",
            "",
            "## 全局视频类型占比",
            "",
            f"- 短视频(0~30s): {short_total} ({format_ratio(short_total, total_video_count)})",
            f"- 中视频(30~60s): {medium_total} ({format_ratio(medium_total, total_video_count)})",
            f"- 中长视频(60~240s): {medium_long_total} ({format_ratio(medium_long_total, total_video_count)})",
            f"- 长视频(240s+): {long_total} ({format_ratio(long_total, total_video_count)})",
            "",
            "## 长视频占比 Top 20",
            "",
            "| 排名 | UP主 | 视频总数 | 长视频数量 | 长视频占比 | 平均时长 |",
            "| --- | --- | --- | --- | --- | --- |"
        ]

        sorted_rows = sorted(
            summary_rows,
            key=lambda row: (
                float(str(row['long_video_ratio']).rstrip('%')),
                row['total_videos']
            ),
            reverse=True
        )

        for idx, row in enumerate(sorted_rows[:20], 1):
            report_lines.append(
                f"| {idx} | {row['uploader_name']} | {row['total_videos']} | "
                f"{row['long_video_count']} | {row['long_video_ratio']} | {row['average_duration_text']} |"
            )

        with open(VIDEO_DURATION_REPORT_MD, 'w', encoding='utf-8') as report_file:
            report_file.write('\n'.join(report_lines))
    except Exception as e:
        print(f"❌ 保存视频时长分析报告失败: {e}")


def analyze_video_durations(followings):
    """
    获取所有关注UP主的全部视频并生成时长分析报告
    """
    if not ENABLE_VIDEO_DURATION_ANALYSIS:
        return {}

    print()
    print("=" * 60)
    print("📊 正在分析所有关注UP主的全部视频时长...")
    print("=" * 60)

    duration_progress = load_video_duration_progress()
    if duration_progress:
        print(f"♻️  已加载 {len(duration_progress)} 条视频时长分析缓存。")

    pending_followings = [
        following for following in followings
        if should_refresh_video_duration_cache(
            following,
            duration_progress.get(str(following.get('mid')))
        )
    ]

    failed_followings = []
    for idx, following in enumerate(pending_followings, 1):
        mid = following.get('mid')
        uname = following.get('uname', '未知UP主')
        print(f"[{idx}/{len(pending_followings)}] 正在获取 {uname} 的全部视频...")

        try:
            videos = get_all_videos_for_up(mid, uname)
        except RateLimitExceededError:
            print(f"   ⏭️  {uname} - 当前风控较严，先加入稍后重试队列")
            failed_followings.append(following)
            continue
        except requests.exceptions.RequestException as e:
            print(f"   ⚠️  {uname} - 网络异常: {e.__class__.__name__}，先加入稍后重试队列")
            failed_followings.append(following)
            continue

        if videos is None:
            failed_followings.append(following)
            continue

        summary = build_video_duration_summary(following, videos)
        duration_progress[str(mid)] = {
            'uploader_name': uname,
            'uploader_id': mid,
            'cached_at': int(time.time()),
            'videos': videos,
            'summary': summary
        }
        save_video_duration_progress(duration_progress)
        print(
            f"   ✅ 共获取 {summary['total_videos']} 个视频，"
            f"长视频占比 {summary['long_video_ratio']}"
        )

        if idx < len(pending_followings):
            time.sleep(get_request_delay())

        if idx < len(pending_followings) and idx % VIDEO_ANALYSIS_BATCH_SIZE == 0:
            cooldown = VIDEO_ANALYSIS_BATCH_COOLDOWN + random.uniform(0, 5)
            print(f"⏸️  视频分析已完成 {idx} 位UP主，批次冷却 {cooldown:.0f} 秒后继续...")
            time.sleep(cooldown)

    all_video_rows = []
    summary_rows = []
    for following in followings:
        mid = str(following.get('mid'))
        entry = duration_progress.get(mid)
        if not entry:
            continue
        all_video_rows.extend(entry.get('videos', []))
        summary_rows.append(entry.get('summary', {}))

    if not summary_rows:
        print("⚠️  未生成任何视频时长分析结果。")
        return duration_progress

    save_all_videos_to_csv(all_video_rows)
    save_video_duration_analysis_to_csv(summary_rows)
    save_video_duration_report(summary_rows, len(all_video_rows))

    print("✅ 视频明细已保存到文件:", ALL_VIDEOS_CSV)
    print("✅ 视频时长分析已保存到文件:", VIDEO_DURATION_ANALYSIS_CSV)
    print("✅ 视频时长报告已保存到文件:", VIDEO_DURATION_REPORT_MD)
    if failed_followings:
        print(f"⚠️  仍有 {len(failed_followings)} 位UP主未完成全量视频分析，下次运行会继续补抓。")
    return duration_progress


# ===========================
# 主程序入口
# ===========================

if __name__ == "__main__":
    try:
        # 第一步：执行数据抓取与分析
        analyze_hiatus()

        # ==========================================
        # ⬇️ 新增功能：无缝衔接，自动调用飞书上传 API ⬇️
        # ==========================================


        print("\n" + "=" * 60)
        print("🚀 B站数据抓取与分析已结束！正在自动触发飞书上传程序...")
        print("=" * 60)

        # 获取当前运行的 Python 解释器路径 (防止虚拟环境报错)
        python_exe = sys.executable
        # 自动定位同目录下的上传脚本
        script_dir = os.path.dirname(os.path.abspath(__file__))
        upload_script = os.path.join(script_dir, "自动上传文件脚本.py")

        # 相当于系统自动帮你执行了: python 自动上传文件脚本.py
        result = subprocess.run([python_exe, upload_script])

        if result.returncode == 0:
            print("\n🎉 终极自动化流水线执行完毕！数据已完美同步至飞书。")
        else:
            print("\n❌ 抓取已成功，但上传阶段出现异常，请检查上方的报错提示。")

    except KeyboardInterrupt:
        print("\n\n⚠️  程序被用户中断")
    except Exception as e:
        print(f"\n❌ 程序运行出错: {e}")
        import traceback

        traceback.print_exc()

