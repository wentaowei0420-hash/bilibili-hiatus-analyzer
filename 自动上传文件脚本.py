import requests
import json
import pandas as pd
import numpy as np
from dotenv import load_dotenv
import os
load_dotenv()
# ================= 配置区 =================
APP_ID = os.getenv("FEISHU_APP_ID", "")
APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
SPREADSHEET_TOKEN = os.getenv("FEISHU_SPREADSHEET_TOKEN", "")

# 本地文件路径
FILE_HIATUS = os.getenv("FILE_HIATUS_PATH", r"D:\pycharm_pro\bilibili-hiatus-analyzer\bilibili_hiatus_ranking.csv")
FILE_DURATION = os.getenv("FILE_DURATION_PATH", r"D:\pycharm_pro\bilibili-hiatus-analyzer\bilibili_video_duration_analysis.csv")
FILE_MERGED_OUTPUT = os.getenv("FILE_MERGED_OUTPUT_PATH", r"D:\pycharm_pro\bilibili-hiatus-analyzer\merged_bilibili_data.csv")

# ==========================================

def get_tenant_access_token():
    """1. 获取飞书 API 调用凭证"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": APP_ID, "app_secret": APP_SECRET}
    response = requests.post(url, json=payload)
    data = response.json()
    if data.get("code") == 0:
        return data.get("tenant_access_token")
    else:
        raise Exception(f"获取 Token 失败: {data}")


def get_first_sheet_id(token, spreadsheet_token):
    """2. 自动获取表格中第一个子表的真实 ID"""
    url = f"https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}/sheets/query"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }
    print("-> 正在呼叫飞书，自动查询子表 ID...")
    response = requests.get(url, headers=headers)
    result = response.json()

    if result.get("code") == 0:
        sheet_id = result["data"]["sheets"][0]["sheet_id"]
        title = result["data"]["sheets"][0]["title"]
        print(f"✅ 成功找到子表！名称: '{title}', 底层 ID: '{sheet_id}'")
        return sheet_id
    else:
        raise Exception(f"自动获取 Sheet ID 失败: {result}")


def prepare_sheets_data():
    """3. 读取两个 CSV 并合并，直接提取指定的 14 列核心数据"""
    print("-> 正在读取并合并本地数据...")
    df1 = pd.read_csv(FILE_HIATUS, encoding='utf-8')
    df2 = pd.read_csv(FILE_DURATION, encoding='utf-8')

    # 剔除重复列
    cols_to_use = df2.columns.difference(df1.columns).tolist() + ['UP主姓名']
    df2_clean = df2[cols_to_use]

    # 外连接合并
    df_merged = pd.merge(df1, df2_clean, on='UP主姓名', how='outer')

    # 🎯 核心筛选：只保留你需要的 14 列
    target_cols = [
        'UP主姓名', 'UP主主页链接', '发布视频数量', '未更新天数', '平均时长',
        '短视频数量(0~30s)', '短视频占比', '中视频数量(30~60s)', '中视频占比',
        '中长视频数量(60~240s)', '中长视频占比', '长视频数量(240s+)', '长视频占比',
        '关注分组名称'
    ]

    final_cols = [c for c in target_cols if c in df_merged.columns]
    df_merged = df_merged[final_cols]

    # 清理空值
    df_merged = df_merged.replace({np.nan: "", pd.NaT: ""})
    df_merged.to_csv(FILE_MERGED_OUTPUT, index=False, encoding='utf-8-sig')
    print(f"✅ 合并完成！已精准提取 {len(final_cols)} 列核心数据，共 {len(df_merged)} 条数据。")

    header = df_merged.columns.tolist()
    values = df_merged.values.tolist()
    return [header] + values


def overwrite_feishu_sheets(token, spreadsheet_token, sheet_id, all_values, chunk_size=2000):
    """4. 分批覆盖写入电子表格，并自动清理历史残留数据"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8"
    }

    # 🧹 橡皮擦逻辑：补 1000 行空数据覆盖旧数据
    empty_row = [""] * len(all_values[0])
    padded_values = all_values + [empty_row] * 1000

    total_chunks = (len(padded_values) + chunk_size - 1) // chunk_size
    print(f"-> 准备覆盖写入，真实数据 {len(all_values)} 行，附带清理空行，共分为 {total_chunks} 批上传...")

    for i in range(0, len(padded_values), chunk_size):
        chunk_data = padded_values[i:i + chunk_size]
        start_row = i + 1
        end_row = i + len(chunk_data)

        url = f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values"
        payload = {
            "valueRange": {
                "range": f"{sheet_id}!A{start_row}:Z{end_row}",
                "values": chunk_data
            }
        }

        response = requests.put(url, headers=headers, data=json.dumps(payload))
        result = response.json()

        if result.get("code") == 0:
            print(f"  👉 批次 {i // chunk_size + 1}/{total_chunks} 覆盖写入并清理成功！")
        else:
            print(f"  ❌ 批次 {i // chunk_size + 1} 写入失败: {result}")
            break


if __name__ == "__main__":
    try:
        # 步骤 1: 准备合并数据
        sheets_data = prepare_sheets_data()

        # 步骤 2: 获取 Token
        access_token = get_tenant_access_token()
        print("✅ 成功获取飞书 API Token！")

        # 步骤 3: 自动获取 ID（这步刚才被我漏掉了）
        auto_sheet_id = get_first_sheet_id(access_token, SPREADSHEET_TOKEN)

        # 步骤 4: 覆盖推送数据（把获取到的 auto_sheet_id 传进去）
        overwrite_feishu_sheets(access_token, SPREADSHEET_TOKEN, auto_sheet_id, sheets_data)
        print("🎉 全部任务执行完毕！请刷新你的飞书电子表格查看（旧数据已被完美覆盖）。")

    except Exception as e:
        print(f"程序运行出错: {e}")
