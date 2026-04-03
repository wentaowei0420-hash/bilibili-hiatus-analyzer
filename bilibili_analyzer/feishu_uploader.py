import hashlib
import json
import sqlite3
from datetime import datetime

import numpy as np
import pandas as pd
import requests

from .logging_utils import smart_print as print


class FeishuUploader:
    def __init__(self, config):
        self.config = config

    @staticmethod
    def _determine_merge_keys(df_hiatus, df_duration):
        preferred_keys = [
            "UP主UID",
            "UP主主页链接",
            "UP主姓名",
        ]
        for key in preferred_keys:
            if key in df_hiatus.columns and key in df_duration.columns:
                return [key]
        return []

    @staticmethod
    def _deduplicate_by_keys(dataframe, merge_keys):
        if not merge_keys:
            return dataframe
        return dataframe.drop_duplicates(subset=merge_keys, keep="first")

    @staticmethod
    def _sqlite_type_for_series(series):
        if pd.api.types.is_integer_dtype(series.dtype) or pd.api.types.is_bool_dtype(series.dtype):
            return "INTEGER"
        if pd.api.types.is_float_dtype(series.dtype):
            return "REAL"
        return "TEXT"

    def ensure_sqlite_table_schema(self, conn, table_name, dataframe):
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        if cursor.fetchone() is None:
            return

        cursor.execute(f'PRAGMA table_info("{table_name}")')
        existing_columns = {row[1] for row in cursor.fetchall()}
        missing_columns = [column for column in dataframe.columns if column not in existing_columns]

        for column in missing_columns:
            sqlite_type = self._sqlite_type_for_series(dataframe[column])
            cursor.execute(f'ALTER TABLE "{table_name}" ADD COLUMN "{column}" {sqlite_type}')

        if missing_columns:
            conn.commit()
            print("-> 检测到 SQLite 历史表缺少新字段，已自动补齐: " + ", ".join(missing_columns))

    def get_tenant_access_token(self):
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        payload = {"app_id": self.config.app_id, "app_secret": self.config.app_secret}
        response = requests.post(url, json=payload)
        data = response.json()
        if data.get("code") == 0:
            return data.get("tenant_access_token")
        raise RuntimeError(f"获取 Token 失败: {data}")

    def get_target_sheet_id(self, token):
        url = (
            "https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/"
            f"{self.config.spreadsheet_token}/sheets/query"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        print("-> 正在调用飞书，自动查询子表 ID...")
        response = requests.get(url, headers=headers)
        result = response.json()
        if result.get("code") != 0:
            raise RuntimeError(f"自动获取 Sheet ID 失败: {result}")

        sheets = result["data"]["sheets"]
        target_title = getattr(self.config, "sheet_title", "").strip()
        target_index = int(getattr(self.config, "sheet_index", 0))

        if target_title:
            for sheet in sheets:
                if sheet.get("title") == target_title:
                    print(
                        f"✅ 成功找到目标子表，名称: '{sheet['title']}', 底层 ID: '{sheet['sheet_id']}'"
                    )
                    return sheet["sheet_id"]

        if 0 <= target_index < len(sheets):
            sheet = sheets[target_index]
            print(
                f"⚠️  未找到指定名称的子表，已按序号选择 '{sheet['title']}' "
                f"(index={target_index})"
            )
            return sheet["sheet_id"]

        available_titles = ", ".join(sheet.get("title", "") for sheet in sheets)
        raise RuntimeError(
            f"未找到指定的飞书子表。名称: {target_title or '未配置'}, "
            f"序号: {target_index}, 可用子表: {available_titles}"
        )

    def load_upload_state(self):
        try:
            with self.config.upload_state_json.open("r", encoding="utf-8") as state_file:
                data = json.load(state_file)
        except FileNotFoundError:
            return {}
        except Exception as exc:
            print(f"⚠️  读取飞书上传状态失败，将继续执行上传: {exc}")
            return {}

        return data if isinstance(data, dict) else {}

    def save_upload_state(self, content_hash, row_count):
        payload = {
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content_hash": content_hash,
            "row_count": row_count,
        }
        try:
            with self.config.upload_state_json.open("w", encoding="utf-8") as state_file:
                json.dump(payload, state_file, ensure_ascii=False, indent=2)
        except Exception as exc:
            print(f"⚠️  保存飞书上传状态失败，但不影响本次上传: {exc}")

    @staticmethod
    def calculate_content_hash(values):
        payload = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def prepare_data_and_save_to_db(self):
        print("-> 正在读取并合并本地数据...")
        df_hiatus = pd.read_csv(self.config.file_hiatus, encoding="utf-8-sig")
        df_duration = pd.read_csv(self.config.file_duration, encoding="utf-8-sig")

        merge_keys = self._determine_merge_keys(df_hiatus, df_duration)
        if not merge_keys:
            raise RuntimeError("无法找到可用于合并飞书数据的稳定字段，请至少保留 UP主UID 或 UP主主页链接。")

        df_hiatus = self._deduplicate_by_keys(df_hiatus, merge_keys)
        df_duration = self._deduplicate_by_keys(df_duration, merge_keys)

        cols_to_use = df_duration.columns.difference(df_hiatus.columns).tolist() + merge_keys
        df_duration_clean = df_duration[cols_to_use]
        df_merged = pd.merge(df_hiatus, df_duration_clean, on=merge_keys, how="outer")
        df_merged = self._deduplicate_by_keys(df_merged, merge_keys)

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        df_merged["抓取时间"] = current_time

        print(f"-> 正在将本次抓取的全量数据备份至 SQLite 数据库: {self.config.db_path} ...")
        with sqlite3.connect(self.config.db_path) as conn:
            self.ensure_sqlite_table_schema(conn, "up_daily_stats", df_merged)
            df_merged.to_sql("up_daily_stats", conn, if_exists="append", index=False)
        print("✅ 历史数据入库成功（所有数据已安全沉淀到本地数据库）")

        df_merged.to_csv(self.config.file_merged_output, index=False, encoding="utf-8-sig")

        target_cols = [
            "备注",
            "UP主姓名",
            "UP主主页链接",
            "粉丝数",
            "发布视频数量",
            "未更新天数",
            "平均点赞数",
            "平均几天一更",
            "平均时长",
            "短视频数量(0~30s)",
            "短视频占比",
            "中视频数量(30~60s)",
            "中视频占比",
            "中长视频数量(60~240s)",
            "中长视频占比",
            "长视频数量(240s+)",
            "长视频占比",
            "关注分组名称",
        ]
        final_cols = [column for column in target_cols if column in df_merged.columns]
        df_feishu = df_merged[final_cols].replace({np.nan: "", pd.NaT: ""})
        print(f"✅ 飞书视图数据提取完成，共 {len(final_cols)} 列，{len(df_feishu)} 条数据。")

        header = df_feishu.columns.tolist()
        values = df_feishu.values.tolist()
        return [header] + values

    def overwrite_feishu_sheets(self, token, sheet_id, all_values, chunk_size=2000):
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        empty_row = [""] * len(all_values[0])
        padded_values = all_values + [empty_row] * 1000
        total_chunks = (len(padded_values) + chunk_size - 1) // chunk_size
        print(f"-> 准备覆盖写入飞书，真实数据 {len(all_values)} 行，附带清理空行...")

        for index in range(0, len(padded_values), chunk_size):
            chunk_data = padded_values[index:index + chunk_size]
            start_row = index + 1
            end_row = index + len(chunk_data)
            url = (
                "https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/"
                f"{self.config.spreadsheet_token}/values"
            )
            payload = {
                "valueRange": {
                    "range": f"{sheet_id}!A{start_row}:Z{end_row}",
                    "values": chunk_data,
                }
            }
            response = requests.put(url, headers=headers, data=json.dumps(payload))
            result = response.json()
            if result.get("code") == 0:
                print(f"  👉 批次 {index // chunk_size + 1}/{total_chunks} 覆盖写入并清理成功！")
            else:
                raise RuntimeError(f"批次 {index // chunk_size + 1} 写入失败: {result}")

    def run(self):
        sheets_data = self.prepare_data_and_save_to_db()
        content_hash = self.calculate_content_hash(sheets_data)
        upload_state = self.load_upload_state()
        if upload_state.get("content_hash") == content_hash:
            print("ℹ️  本次飞书视图数据与上次上传完全一致，已跳过重复覆盖上传。")
            return

        access_token = self.get_tenant_access_token()
        print("✅ 成功获取飞书 API Token")
        sheet_id = self.get_target_sheet_id(access_token)
        self.overwrite_feishu_sheets(access_token, sheet_id, sheets_data)
        self.save_upload_state(content_hash, len(sheets_data) - 1)
        print("🎀 全部任务执行完毕！请刷新你的飞书电子表格查看（旧数据已被覆盖）。")
