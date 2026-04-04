import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests

from .logging_utils import create_progress, smart_print as print


class FeishuUploader:
    def __init__(self, config):
        self.config = config
        self.uid_column = "UP主UID"

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

    def cleanup_sqlite_history(self, conn, table_name):
        retention_days = int(getattr(self.config, "history_retention_days", 0) or 0)
        if retention_days <= 0:
            return 0

        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        if cursor.fetchone() is None:
            return 0

        cutoff = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d %H:%M:%S")
        try:
            cursor.execute(f'DELETE FROM "{table_name}" WHERE "抓取时间" < ?', (cutoff,))
        except sqlite3.OperationalError as exc:
            if "抓取时间" in str(exc):
                return 0
            raise
        deleted_rows = conn.execute("SELECT changes()").fetchone()[0]
        if deleted_rows:
            conn.commit()
            print(f"-> 已清理 {deleted_rows} 条超出 {retention_days} 天保留期的历史数据。")
        return deleted_rows

    def vacuum_sqlite(self):
        try:
            with sqlite3.connect(self.config.db_path) as conn:
                conn.execute("VACUUM")
            print("-> SQLite VACUUM 完成，已回收历史库空间。")
        except Exception as exc:
            print(f"⚠️  执行 SQLite VACUUM 失败，但不影响本次上传: {exc}")

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
            self.config.upload_state_json.parent.mkdir(parents=True, exist_ok=True)
            with self.config.upload_state_json.open("w", encoding="utf-8") as state_file:
                json.dump(payload, state_file, ensure_ascii=False, indent=2)
        except Exception as exc:
            print(f"⚠️  保存飞书上传状态失败，但不影响本次上传: {exc}")

    @staticmethod
    def calculate_content_hash(values):
        payload = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _column_letter(index):
        result = ""
        current = index
        while current > 0:
            current, remainder = divmod(current - 1, 26)
            result = chr(65 + remainder) + result
        return result

    @staticmethod
    def _normalize_cell(value):
        if pd.isna(value):
            return ""
        return str(value)

    def _write_range(self, token, sheet_id, start_row, rows):
        if not rows:
            return

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        end_col = self._column_letter(len(rows[0]))
        end_row = start_row + len(rows) - 1
        url = (
            "https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/"
            f"{self.config.spreadsheet_token}/values"
        )
        payload = {
            "valueRange": {
                "range": f"{sheet_id}!A{start_row}:{end_col}{end_row}",
                "values": rows,
            }
        }
        response = requests.put(url, headers=headers, data=json.dumps(payload))
        result = response.json()
        if result.get("code") != 0:
            raise RuntimeError(f"飞书写入失败: {result}")

    def _fetch_existing_sheet_values(self, token, sheet_id, column_count, row_limit):
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        end_col = self._column_letter(column_count)
        value_range = f"{sheet_id}!A1:{end_col}{row_limit}"
        encoded_range = quote(value_range, safe="")
        url = (
            "https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/"
            f"{self.config.spreadsheet_token}/values/{encoded_range}"
        )
        response = requests.get(url, headers=headers)
        result = response.json()
        if result.get("code") != 0:
            raise RuntimeError(f"读取飞书现有表格失败: {result}")

        data = result.get("data", {})
        value_range_data = data.get("valueRange", {})
        values = value_range_data.get("values", [])
        return values if isinstance(values, list) else []

    def _group_contiguous_rows(self, row_map):
        if not row_map:
            return []

        grouped = []
        sorted_items = sorted(row_map.items())
        current_start = None
        current_rows = []
        previous_row = None

        for row_number, row_values in sorted_items:
            if current_start is None or previous_row is None or row_number != previous_row + 1:
                if current_rows:
                    grouped.append((current_start, current_rows))
                current_start = row_number
                current_rows = [row_values]
            else:
                current_rows.append(row_values)
            previous_row = row_number

        if current_rows:
            grouped.append((current_start, current_rows))
        return grouped

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
        deleted_rows = 0
        with sqlite3.connect(self.config.db_path) as conn:
            self.ensure_sqlite_table_schema(conn, "up_daily_stats", df_merged)
            deleted_rows = self.cleanup_sqlite_history(conn, "up_daily_stats")
            df_merged.to_sql("up_daily_stats", conn, if_exists="append", index=False)
        print("✅ 历史数据入库成功（所有数据已安全沉淀到本地数据库）")
        if deleted_rows:
            self.vacuum_sqlite()

        self.config.file_merged_output.parent.mkdir(parents=True, exist_ok=True)
        df_merged.to_csv(self.config.file_merged_output, index=False, encoding="utf-8-sig")

        target_cols = [
            "UP主UID",
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

    def overwrite_feishu_sheets(self, token, sheet_id, all_values, previous_row_count=0, chunk_size=2000):
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        empty_row = [""] * len(all_values[0])
        actual_row_count = max(len(all_values) - 1, 0)
        rows_to_clear = max(previous_row_count, actual_row_count) + 100
        padded_total_rows = max(rows_to_clear + 1, len(all_values))
        padded_values = all_values + [empty_row] * (padded_total_rows - len(all_values))
        total_chunks = (len(padded_values) + chunk_size - 1) // chunk_size
        print(
            f"-> 准备覆盖写入飞书，真实数据 {actual_row_count} 行，"
            f"按 {padded_total_rows - 1} 行范围清理旧数据..."
        )

        with create_progress(transient=True) as progress:
            task_id = progress.add_task("覆盖写入飞书并清理旧数据", total=total_chunks)
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
                    progress.advance(task_id)
                else:
                    raise RuntimeError(f"批次 {index // chunk_size + 1} 写入失败: {result}")

    def incremental_update_feishu_sheets(self, token, sheet_id, all_values, previous_row_count=0, prune_missing=True):
        header = all_values[0]
        rows = all_values[1:]
        if self.uid_column not in header:
            raise RuntimeError("当前飞书上传数据缺少 UP主UID，无法按 UID 增量更新。")

        row_limit = max(previous_row_count, len(rows), 5000) + 500
        existing_values = self._fetch_existing_sheet_values(token, sheet_id, len(header), row_limit)
        existing_header = existing_values[0] if existing_values else []

        if existing_header != header:
            print("⚠️  飞书表头与当前导出结构不一致，将先执行一次整表覆盖对齐结构。")
            self.overwrite_feishu_sheets(
                token,
                sheet_id,
                all_values,
                previous_row_count=previous_row_count,
            )
            return

        uid_index = header.index(self.uid_column)
        empty_row = [""] * len(header)

        existing_uid_to_row = {}
        existing_uid_to_values = {}
        for row_number, row_values in enumerate(existing_values[1:], start=2):
            normalized_row = list(row_values) + [""] * (len(header) - len(row_values))
            normalized_row = normalized_row[: len(header)]
            uid_value = self._normalize_cell(normalized_row[uid_index]).strip()
            if not uid_value:
                continue
            existing_uid_to_row[uid_value] = row_number
            existing_uid_to_values[uid_value] = [self._normalize_cell(value) for value in normalized_row]

        updates = {}
        append_rows = []
        current_row_count = len(existing_values)
        seen_uids = set()

        for row in rows:
            normalized_row = [self._normalize_cell(value) for value in row]
            uid_value = self._normalize_cell(normalized_row[uid_index]).strip()
            if not uid_value:
                continue
            seen_uids.add(uid_value)
            existing_row_number = existing_uid_to_row.get(uid_value)
            if existing_row_number is None:
                append_rows.append(normalized_row)
                continue
            if existing_uid_to_values.get(uid_value) != normalized_row:
                updates[existing_row_number] = normalized_row

        stale_uids = []
        if prune_missing:
            stale_uids = [uid for uid in existing_uid_to_row if uid not in seen_uids]
            for uid in stale_uids:
                updates[existing_uid_to_row[uid]] = list(empty_row)

        grouped_updates = self._group_contiguous_rows(updates)
        total_steps = len(grouped_updates) + (1 if append_rows else 0)
        if total_steps:
            with create_progress(transient=True) as progress:
                task_id = progress.add_task("按 UID 增量同步飞书", total=total_steps)
                for start_row, grouped_rows in grouped_updates:
                    self._write_range(token, sheet_id, start_row, grouped_rows)
                    progress.advance(task_id)

                if append_rows:
                    append_start_row = current_row_count + 1
                    self._write_range(token, sheet_id, append_start_row, append_rows)
                    progress.advance(task_id)
        elif append_rows:
            append_start_row = current_row_count + 1
            self._write_range(token, sheet_id, append_start_row, append_rows)

        print(
            f"✅ 飞书增量更新完成：更新 {len(updates) - len(stale_uids)} 行，"
            f"新增 {len(append_rows)} 行，清理 {len(stale_uids)} 行。"
        )

        if prune_missing and stale_uids:
            print("🧹 检测到飞书表中存在已清空旧行，正在执行紧凑整理以移除空行...")
            compact_previous_row_count = max(previous_row_count, current_row_count)
            self.overwrite_feishu_sheets(
                token,
                sheet_id,
                all_values,
                previous_row_count=compact_previous_row_count,
            )

    def run(self, prune_missing=True):
        sheets_data = self.prepare_data_and_save_to_db()
        content_hash = self.calculate_content_hash(sheets_data)
        upload_state = self.load_upload_state()
        if upload_state.get("content_hash") == content_hash:
            print("ℹ️  本次飞书视图数据与上次上传完全一致，已跳过重复覆盖上传。")
            return

        access_token = self.get_tenant_access_token()
        print("✅ 成功获取飞书 API Token")
        sheet_id = self.get_target_sheet_id(access_token)
        previous_row_count = int(upload_state.get("row_count") or 0)
        self.incremental_update_feishu_sheets(
            access_token,
            sheet_id,
            sheets_data,
            previous_row_count=previous_row_count,
            prune_missing=prune_missing,
        )
        self.save_upload_state(content_hash, len(sheets_data) - 1)
        print("🎀 全部任务执行完毕！请刷新你的飞书电子表格查看。")
