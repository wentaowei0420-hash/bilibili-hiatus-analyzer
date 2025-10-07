"""
B站催更分析器 - 找出你关注的UP主中的"鸽王"
作者: AI Assistant
功能: 自动获取B站关注列表，分析UP主更新频率，生成"鸽王"排行榜
"""

import requests
import time
import json
from datetime import datetime
import csv
import sys
import random

# ===========================
# 配置区域 - 请在这里填入你的信息
# ===========================

# Cookie配置
# 请从浏览器中复制完整的Cookie字符串粘贴到下方引号中
# 获取方法：
# 1. 登录B站 (www.bilibili.com)
# 2. 打开浏览器开发者工具 (F12)
# 3. 切换到 Network (网络) 标签
# 4. 刷新页面，点击任意请求
# 5. 在 Headers 中找到 Cookie，复制完整内容
COOKIE = "在这里粘贴你的Cookie"

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
SPACE_VIDEO_API = "https://api.bilibili.com/x/space/arc/search"

# 输出文件名
OUTPUT_CSV = "bilibili_hiatus_ranking.csv"

# 请求延时（秒）- 遵守君子协议
REQUEST_DELAY = 5


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


def get_user_mid():
    """
    获取当前登录用户的mid（用户ID）
    """
    try:
        url = 'https://api.bilibili.com/x/web-interface/nav'
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        if data.get('code') == 0:
            return data.get('data', {}).get('mid')
        else:
            print(f"❌ 获取用户信息失败: {data.get('message')}")
            return None
    except Exception as e:
        print(f"❌ 获取用户信息出错: {e}")
        return None


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
            
            response = requests.get(FOLLOWINGS_API, headers=HEADERS, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
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
            
            all_followings.extend(followings)
            print(f"   已获取 {len(all_followings)} 位UP主...")
            
            # 检查是否还有更多数据
            total = data.get('data', {}).get('total', 0)
            if len(all_followings) >= total:
                break
            
            page += 1
            time.sleep(REQUEST_DELAY)
            
        except requests.exceptions.RequestException as e:
            print(f"❌ 网络请求失败: {e}")
            return None
        except Exception as e:
            print(f"❌ 解析数据失败: {e}")
            return None
    
    print(f"✅ 成功获取 {len(all_followings)} 位关注的UP主\n")
    return all_followings


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
        params = {
            'mid': mid,
            'ps': 1,  # 只获取1个视频（最新的）
            'tid': 0,
            'pn': 1,
            'order': 'pubdate'  # 按发布时间排序
        }
        
        response = requests.get(SPACE_VIDEO_API, headers=HEADERS, params=params, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        
        # 检查响应状态
        if data.get('code') != 0:
            print(f"   ⚠️  {uname} - API返回错误: {data.get('message', '未知错误')}")
            return None
        
        # 获取视频列表
        videos = data.get('data', {}).get('list', {}).get('vlist', [])
        
        if not videos:
            print(f"   📭 {uname} - 暂无视频")
            return None
        
        latest_video = videos[0]
        
        # 提取视频信息
        video_info = {
            'uploader_name': uname,
            'uploader_id': mid,
            'video_title': latest_video.get('title', '未知标题'),
            'bvid': latest_video.get('bvid', ''),
            'upload_timestamp': latest_video.get('created', 0),
            'view_count': latest_video.get('play', 0)
        }
        
        return video_info
        
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
    
    # 分析每位UP主的最新视频
    print("🔍 正在分析每位UP主的最新视频...")
    print()
    
    results = []
    
    for idx, following in enumerate(followings, 1):
        mid = following.get('mid')
        uname = following.get('uname', '未知UP主')
        
        print(f"[{idx}/{len(followings)}] 正在获取 {uname} 的最新视频...")
        
        video_info = get_latest_video(mid, uname)
        
        if video_info:
            # 计算未更新天数
            days_since = calculate_days_since(video_info['upload_timestamp'])
            
            # 构建完整的视频链接
            video_url = f"https://www.bilibili.com/video/{video_info['bvid']}"
            
            # 添加到结果列表
            result_item = {
                'uploader_name': video_info['uploader_name'],
                'uploader_id': video_info['uploader_id'],
                'latest_video_title': video_info['video_title'],
                'upload_date': timestamp_to_date(video_info['upload_timestamp']),
                'days_since_update': days_since,
                'view_count': video_info['view_count'],
                'video_url': video_url
            }
            
            results.append(result_item)
            print(f"   ✅ 最后更新: {days_since} 天前")
        
        # 遵守君子协议，延时请求（添加随机延时更像真实用户）
        if idx < len(followings):
            time.sleep(REQUEST_DELAY + random.uniform(0, 1))
    
    if not results:
        print("\n❌ 未能获取到任何视频数据")
        return
    
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
        print(f"   📺 最新视频: {result['latest_video_title']}")
        print(f"   📅 发布日期: {result['upload_date']}")
        print(f"   👁️  播放量: {result['view_count']:,}")
        print(f"   🔗 链接: {result['video_url']}")
        print()
    
    # 保存到CSV文件
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
                'latest_video_title',
                'upload_date',
                'days_since_update',
                'view_count',
                'video_url'
            ]
            
            # 中文表头映射
            chinese_headers = {
                'uploader_name': 'UP主姓名',
                'uploader_id': 'UP主UID',
                'latest_video_title': '最新视频标题',
                'upload_date': '最新视频发布日期',
                'days_since_update': '未更新天数',
                'view_count': '最新视频播放量',
                'video_url': '视频链接'
            }
            
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            # 写入中文表头
            writer.writerow(chinese_headers)
            
            # 写入数据
            for result in results:
                writer.writerow(result)
        
        print("=" * 60)
        print(f"✅ 排行榜已保存到文件: {OUTPUT_CSV}")
        print(f"📊 共分析了 {len(results)} 位UP主")
        print("=" * 60)
        
    except Exception as e:
        print(f"❌ 保存CSV文件失败: {e}")


# ===========================
# 主程序入口
# ===========================

if __name__ == "__main__":
    try:
        analyze_hiatus()
    except KeyboardInterrupt:
        print("\n\n⚠️  程序被用户中断")
    except Exception as e:
        print(f"\n❌ 程序运行出错: {e}")
        import traceback
        traceback.print_exc()

