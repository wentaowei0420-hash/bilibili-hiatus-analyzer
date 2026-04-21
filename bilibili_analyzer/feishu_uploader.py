import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from common.export_store import (
    read_latest_snapshot_to_dataframe,
    read_table_to_dataframe,
    write_dataframe_to_table,
)

from .logging_utils import create_progress, create_summary_panel, get_console, wait_with_progress


class FeishuUploader:
    @staticmethod
    def _build_session():
        session = requests.Session()
        retry = Retry(
            total=5,
            connect=5,
            read=5,
            backoff_factor=1.2,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PUT", "DELETE"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session
    def __init__(self, config):
        self.config = config
        self.session = self._build_session()
        self.uid_column = "UP主UID"

    def _request_with_retry(self, method, url, request_name, timeout=30, **kwargs):
        last_error = None
        for attempt in range(1, 4):
            try:
                response = self.session.request(method=method, url=url, timeout=timeout, **kwargs)
                response.raise_for_status()
                return response
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
                last_error = exc
                if attempt >= 3:
                    break
                cooldown = min(5 * attempt, 12)
                wait_with_progress(cooldown, f"{request_name} 网络恢复重试中")
            except requests.exceptions.RequestException as exc:
                last_error = exc
                break
        raise RuntimeError(f"{request_name} 失败: {last_error}")

    @staticmethod
    def _determine_merge_keys(df_hiatus, df_duration):
        preferred_keys = ["UP主UID", "UP主主页链接", "UP主姓名"]
        for key in preferred_keys:
            if key in df_hiatus.columns and key in df_duration.columns:
                return [key]
        return []

    @staticmethod
    def _deduplicate_by_keys(dataframe, merge_keys):
        if dataframe is None or dataframe.empty or not merge_keys:
            return dataframe
        return dataframe.drop_duplicates(subset=merge_keys, keep="first")

    @staticmethod
    def _normalize_cell(value):
        if pd.isna(value):
            return ""
        return str(value)

    def _sanitize_feishu_dataframe(self, dataframe):
        cleaned = dataframe.copy()
        cleaned = cleaned.replace({np.nan: "", pd.NaT: ""})

        for column in cleaned.columns:
            cleaned[column] = cleaned[column].map(self._normalize_cell)

        if self.uid_column in cleaned.columns:
            cleaned[self.uid_column] = cleaned[self.uid_column].astype(str).str.strip()
            cleaned = cleaned[cleaned[self.uid_column] != ""]
            cleaned = cleaned.drop_duplicates(subset=[self.uid_column], keep="first")
            cleaned = cleaned.sort_values(by=[self.uid_column], kind="stable")

        non_empty_mask = cleaned.apply(
            lambda row: any(str(value).strip() for value in row.tolist()),
            axis=1,
        )
        cleaned = cleaned[non_empty_mask]
        return cleaned.reset_index(drop=True)

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
            return []

        cursor.execute(f'PRAGMA table_info("{table_name}")')
        existing_columns = {row[1] for row in cursor.fetchall()}
        missing_columns = [column for column in dataframe.columns if column not in existing_columns]

        for column in missing_columns:
            sqlite_type = self._sqlite_type_for_series(dataframe[column])
            cursor.execute(f'ALTER TABLE "{table_name}" ADD COLUMN "{column}" {sqlite_type}')

        if missing_columns:
            conn.commit()
        return missing_columns

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
        except sqlite3.OperationalError:
            return 0
        deleted_rows = conn.execute("SELECT changes()").fetchone()[0]
        if deleted_rows:
            conn.commit()
        return deleted_rows

    def vacuum_sqlite(self):
        self.config.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.config.db_path) as conn:
            conn.execute("VACUUM")

    def get_tenant_access_token(self):
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        payload = {"app_id": self.config.app_id, "app_secret": self.config.app_secret}
        response = self._request_with_retry(
            "POST",
            url,
            "获取飞书 Token",
            json=payload,
            timeout=30,
        )
        data = response.json()
        if data.get("code") == 0:
            return data.get("tenant_access_token")
        raise RuntimeError(f"获取飞书 Token 失败: {data}")

    def get_target_sheet_id(self, token, sheet_title=None, sheet_index=None):
        url = (
            "https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/"
            f"{self.config.spreadsheet_token}/sheets/query"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        response = self._request_with_retry(
            "GET",
            url,
            "查询飞书子表",
            headers=headers,
            timeout=30,
        )
        result = response.json()
        if result.get("code") != 0:
            raise RuntimeError(f"自动获取 Sheet ID 失败: {result}")

        sheets = result.get("data", {}).get("sheets", [])
        target_title = (sheet_title if sheet_title is not None else getattr(self.config, "sheet_title", "")).strip()
        target_index = int(sheet_index if sheet_index is not None else getattr(self.config, "sheet_index", 0))

        if target_title:
            for sheet in sheets:
                if sheet.get("title") == target_title:
                    return sheet["sheet_id"], sheet.get("title", "")

        if 0 <= target_index < len(sheets):
            sheet = sheets[target_index]
            return sheet["sheet_id"], sheet.get("title", "")

        available_titles = ", ".join(sheet.get("title", "") for sheet in sheets)
        raise RuntimeError(
            f"未找到指定的飞书子表。名称: {target_title or '未配置'}, 序号: {target_index}, 可用子表: {available_titles}"
        )

    def load_upload_state(self, state_path=None):
        path = Path(state_path or self.config.upload_state_json)
        try:
            with path.open("r", encoding="utf-8") as state_file:
                data = json.load(state_file)
        except FileNotFoundError:
            return {}
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def save_upload_state(self, content_hash, row_count, state_path=None):
        path = Path(state_path or self.config.upload_state_json)
        payload = {
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "content_hash": content_hash,
            "row_count": row_count,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as state_file:
            json.dump(payload, state_file, ensure_ascii=False, indent=2)

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
        response = self._request_with_retry(
            "PUT",
            url,
            "写入飞书表格",
            headers=headers,
            data=json.dumps(payload),
            timeout=60,
        )
        result = response.json()
        if result.get("code") != 0:
            raise RuntimeError(f"飞书写入失败: {result}")

    def _delete_row_range(self, token, sheet_id, start_index, end_index):
        if end_index <= start_index:
            return

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        url = (
            "https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/"
            f"{self.config.spreadsheet_token}/dimension_range"
        )

        max_chunk_size = 3000
        current_end = end_index
        while current_end > start_index:
            current_start = max(start_index, current_end - max_chunk_size)
            while current_end > current_start:
                payload = {
                    "dimension": {
                        "sheetId": sheet_id,
                        "majorDimension": "ROWS",
                        "startIndex": current_start,
                        "endIndex": current_end,
                    }
                }
                response = self._request_with_retry(
                    "DELETE",
                    url,
                    "删除飞书多余行",
                    headers=headers,
                    data=json.dumps(payload),
                    timeout=60,
                )
                result = response.json()
                if result.get("code") == 0:
                    break

                message = str(result.get("msg", ""))
                if result.get("code") == 90202 and "endIndex" in message:
                    current_end -= 1
                    continue
                if result.get("code") == 90202 and "dimension length" in message:
                    current_start = current_end - max(1, (current_end - current_start) // 2)
                    continue
                raise RuntimeError(f"飞书删除多余行失败: {result}")

            current_end = current_start

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
        response = self._request_with_retry(
            "GET",
            url,
            "读取飞书现有表格",
            headers=headers,
            timeout=60,
        )
        result = response.json()
        if result.get("code") != 0:
            raise RuntimeError(f"读取飞书现有表格失败: {result}")

        values = result.get("data", {}).get("valueRange", {}).get("values", [])
        return values if isinstance(values, list) else []

    def _probe_existing_sheet_row_count(self, token, sheet_id, column_count, baseline_row_count):
        probe_limit = max(int(baseline_row_count or 0), 5000) + 5000
        existing_values = self._fetch_existing_sheet_values(token, sheet_id, column_count, probe_limit)
        if not existing_values:
            return 0

        last_non_empty_row = 0
        for row_number, row_values in enumerate(existing_values, start=1):
            normalized_row = [self._normalize_cell(value).strip() for value in row_values]
            if any(normalized_row):
                last_non_empty_row = row_number
        return last_non_empty_row

    @staticmethod
    def _advance_stage(progress, task_id, description):
        progress.update(task_id, description=description)
        progress.advance(task_id)

    def _load_dataframe_from_store_or_csv(self, table_name, csv_path):
        dataframe = read_latest_snapshot_to_dataframe(self.config.export_store_db, table_name)
        if dataframe is not None:
            return dataframe
        dataframe = read_table_to_dataframe(self.config.export_store_db, table_name)
        if dataframe is not None:
            return dataframe
        if csv_path and Path(csv_path).exists():
            dataframe = pd.read_csv(csv_path, encoding="utf-8-sig")
            write_dataframe_to_table(self.config.export_store_db, table_name, dataframe)
            return dataframe
        return pd.DataFrame()

    def _desired_main_columns(self, dataframe):
        desired = [
            "UP主UID",
            "备注",
            "UP主姓名",
            "UP主主页链接",
            "关注分组ID",
            "关注分组名称",
            "粉丝数",
            "发布视频数量",
            "未更新天数",
            "平均点赞数",
            "平均几天一更",
            "平均时长",
            "视频总数",
            "短视频占比",
            "中视频数量(30~60s)",
            "中视频占比",
            "中长视频数量(60~240s)",
            "中长视频占比",
            "长视频数量(240s+)",
            "长视频占比",
        ]
        return [column for column in desired if column in dataframe.columns]

    def prepare_data_and_save_to_db(self):
        df_hiatus = self._load_dataframe_from_store_or_csv(
            self.config.export_main_table,
            self.config.file_hiatus,
        )
        df_duration = self._load_dataframe_from_store_or_csv(
            self.config.export_analysis_table,
            self.config.file_duration,
        )

        if df_hiatus.empty and df_duration.empty:
            raise RuntimeError("没有可上传的数据，主表和分析表快照都为空。")
        if df_hiatus.empty:
            df_hiatus = pd.DataFrame(columns=list(df_duration.columns))
        if df_duration.empty:
            df_duration = pd.DataFrame(columns=list(df_hiatus.columns))

        merge_keys = self._determine_merge_keys(df_hiatus, df_duration)
        if not merge_keys:
            raise RuntimeError("无法找到可用于合并飞书数据的稳定字段，请至少保留 UP主UID。")

        df_hiatus = self._deduplicate_by_keys(df_hiatus, merge_keys)
        df_duration = self._deduplicate_by_keys(df_duration, merge_keys)

        duration_cols = df_duration.columns.difference(df_hiatus.columns).tolist() + merge_keys
        df_duration_clean = df_duration[duration_cols]
        df_merged = pd.merge(df_hiatus, df_duration_clean, on=merge_keys, how="outer")
        df_merged = self._deduplicate_by_keys(df_merged, merge_keys)
        df_merged["抓取时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        deleted_rows = 0
        added_columns = []
        self.config.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.config.db_path) as conn:
            added_columns = self.ensure_sqlite_table_schema(conn, "up_daily_stats", df_merged)
            deleted_rows = self.cleanup_sqlite_history(conn, "up_daily_stats")
            df_merged.to_sql("up_daily_stats", conn, if_exists="append", index=False)

        if deleted_rows:
            self.vacuum_sqlite()

        self.config.file_merged_output.parent.mkdir(parents=True, exist_ok=True)
        df_merged.to_csv(self.config.file_merged_output, index=False, encoding="utf-8-sig")

        final_cols = self._desired_main_columns(df_merged)
        df_feishu = self._sanitize_feishu_dataframe(df_merged[final_cols] if final_cols else df_merged)
        header = df_feishu.columns.tolist()
        values = df_feishu.values.tolist()
        return [header] + values, {
            "rows": len(df_feishu),
            "columns": len(header),
            "db_rows": len(df_merged),
            "deleted_history_rows": deleted_rows,
            "added_columns": added_columns,
            "merged_output": self.config.file_merged_output.name,
            "deduplicated_by": self.uid_column if self.uid_column in header else ",".join(merge_keys),
        }

    def prepare_single_csv_data(self, csv_path):
        dataframe = pd.read_csv(csv_path, encoding="utf-8-sig")
        dataframe = self._sanitize_feishu_dataframe(dataframe)
        header = dataframe.columns.tolist()
        values = dataframe.values.tolist()
        return [header] + values, {
            "rows": len(dataframe),
            "columns": len(header),
            "source_file": Path(csv_path).name,
            "deduplicated_by": self.uid_column if self.uid_column in header else "整行去空",
        }

    def prepare_single_table_data(self, table_name, csv_fallback_path=None):
        dataframe = self._load_dataframe_from_store_or_csv(table_name, csv_fallback_path)
        if dataframe is None or dataframe.empty:
            raise RuntimeError(f"没有可上传的数据表: {table_name}")
        dataframe = self._sanitize_feishu_dataframe(dataframe)
        header = dataframe.columns.tolist()
        values = dataframe.values.tolist()
        return [header] + values, {
            "rows": len(dataframe),
            "columns": len(header),
            "source_file": f"sqlite:{table_name}",
            "deduplicated_by": self.uid_column if self.uid_column in header else "整行去空",
        }

    def overwrite_feishu_sheets(self, token, sheet_id, all_values, previous_row_count=0, chunk_size=2000):
        actual_row_count = max(len(all_values) - 1, 0)
        existing_total_row_count = self._probe_existing_sheet_row_count(
            token,
            sheet_id,
            len(all_values[0]),
            max(previous_row_count, actual_row_count),
        )
        total_chunks = (len(all_values) + chunk_size - 1) // chunk_size if all_values else 0

        with create_progress(transient=True) as progress:
            task_id = progress.add_task("整表覆盖飞书", total=max(total_chunks, 1))
            for index in range(0, len(all_values), chunk_size):
                chunk_data = all_values[index:index + chunk_size]
                start_row = index + 1
                self._write_range(token, sheet_id, start_row, chunk_data)
                progress.advance(task_id)
            if not all_values:
                progress.advance(task_id)

        existing_row_count = max(existing_total_row_count - 1, 0)
        pruned_rows = max(existing_row_count - actual_row_count, 0)
        if pruned_rows > 0:
            delete_start_index = actual_row_count + 1
            delete_end_index = existing_total_row_count
            self._delete_row_range(token, sheet_id, delete_start_index, delete_end_index)

        return {
            "mode": "overwrite",
            "updated_rows": actual_row_count,
            "appended_rows": 0,
            "pruned_rows": pruned_rows,
            "existing_row_count": existing_row_count,
            "existing_total_row_count": existing_total_row_count,
        }

    def run(self, prune_missing=True):
        with create_progress(transient=False) as progress:
            task_id = progress.add_task("准备飞书同步", total=5)

            progress.update(task_id, description="整理本地结果并生成飞书视图")
            sheets_data, data_meta = self.prepare_data_and_save_to_db()
            self._advance_stage(progress, task_id, f"本地视图已生成({data_meta['rows']} 行)")

            content_hash = self.calculate_content_hash(sheets_data)
            upload_state = self.load_upload_state()

            progress.update(task_id, description="获取飞书访问令牌")
            access_token = self.get_tenant_access_token()
            self._advance_stage(progress, task_id, "访问令牌获取完成")

            progress.update(task_id, description="定位目标子表")
            sheet_id, sheet_title = self.get_target_sheet_id(access_token)
            self._advance_stage(progress, task_id, f"目标子表已定位: {sheet_title or sheet_id}")

            progress.update(task_id, description="整表覆盖飞书")
            previous_row_count = int(upload_state.get("row_count") or 0)
            sync_meta = self.overwrite_feishu_sheets(
                access_token,
                sheet_id,
                sheets_data,
                previous_row_count=previous_row_count,
            )
            self._advance_stage(progress, task_id, "飞书同步完成")

            progress.update(task_id, description="保存上传状态")
            self.save_upload_state(content_hash, len(sheets_data) - 1)
            self._advance_stage(progress, task_id, "上传状态已保存")

        lines = [
            f"目标子表: {sheet_title or sheet_id}",
            f"视图行数: {data_meta['rows']}",
            f"视图列数: {data_meta['columns']}",
            f"去重主键: {data_meta.get('deduplicated_by', self.uid_column)}",
            f"更新行数: {sync_meta.get('updated_rows', 0)}",
            f"清理旧行: {sync_meta.get('pruned_rows', 0)}",
            f"覆盖前远端行数: {sync_meta.get('existing_row_count', 0)}",
            "上传方式: 整表覆盖",
            "数据来源: SQLite 快照优先，CSV 回退",
        ]
        if data_meta.get("deleted_history_rows"):
            lines.append(f"历史库清理: {data_meta['deleted_history_rows']} 行")
        if data_meta.get("added_columns"):
            lines.append(f"SQLite 补列: {', '.join(data_meta['added_columns'])}")

        get_console().print(create_summary_panel("飞书同步完成", lines, border_style="green"))

    def run_single_csv(
        self,
        csv_path,
        *,
        sheet_title,
        sheet_index,
        upload_state_json,
        panel_title="分析表同步完成",
    ):
        csv_path = Path(csv_path).resolve()
        with create_progress(transient=False) as progress:
            task_id = progress.add_task("准备分析表同步", total=5)

            progress.update(task_id, description="整理本地分析数据")
            sheets_data, data_meta = self.prepare_single_csv_data(csv_path)
            self._advance_stage(progress, task_id, f"分析视图已生成({data_meta['rows']} 行)")

            content_hash = self.calculate_content_hash(sheets_data)
            upload_state = self.load_upload_state(upload_state_json)

            progress.update(task_id, description="获取飞书访问令牌")
            access_token = self.get_tenant_access_token()
            self._advance_stage(progress, task_id, "访问令牌获取完成")

            progress.update(task_id, description="定位分析子表")
            sheet_id, resolved_sheet_title = self.get_target_sheet_id(
                access_token,
                sheet_title=sheet_title,
                sheet_index=sheet_index,
            )
            self._advance_stage(progress, task_id, f"分析子表已定位: {resolved_sheet_title or sheet_id}")

            progress.update(task_id, description="整表覆盖分析表")
            previous_row_count = int(upload_state.get("row_count") or 0)
            sync_meta = self.overwrite_feishu_sheets(
                access_token,
                sheet_id,
                sheets_data,
                previous_row_count=previous_row_count,
            )
            self._advance_stage(progress, task_id, "分析表同步完成")

            progress.update(task_id, description="保存分析表上传状态")
            self.save_upload_state(content_hash, len(sheets_data) - 1, upload_state_json)
            self._advance_stage(progress, task_id, "分析表上传状态已保存")

        lines = [
            f"目标子表: {resolved_sheet_title or sheet_id}",
            f"源文件: {data_meta['source_file']}",
            f"视图行数: {data_meta['rows']}",
            f"视图列数: {data_meta['columns']}",
            f"去重主键: {data_meta.get('deduplicated_by', self.uid_column)}",
            f"清理旧行: {sync_meta.get('pruned_rows', 0)}",
            "上传方式: 整表覆盖",
        ]
        get_console().print(create_summary_panel(panel_title, lines, border_style="green"))

    def run_single_table(
        self,
        table_name,
        *,
        csv_fallback_path,
        sheet_title,
        sheet_index,
        upload_state_json,
        panel_title="分析表同步完成",
    ):
        with create_progress(transient=False) as progress:
            task_id = progress.add_task("准备分析表同步", total=5)

            progress.update(task_id, description="整理SQLite分析数据")
            sheets_data, data_meta = self.prepare_single_table_data(table_name, csv_fallback_path)
            self._advance_stage(progress, task_id, f"分析视图已生成({data_meta['rows']} 行)")

            content_hash = self.calculate_content_hash(sheets_data)
            upload_state = self.load_upload_state(upload_state_json)

            progress.update(task_id, description="获取飞书访问令牌")
            access_token = self.get_tenant_access_token()
            self._advance_stage(progress, task_id, "访问令牌获取完成")

            progress.update(task_id, description="定位分析子表")
            sheet_id, resolved_sheet_title = self.get_target_sheet_id(
                access_token,
                sheet_title=sheet_title,
                sheet_index=sheet_index,
            )
            self._advance_stage(progress, task_id, f"分析子表已定位: {resolved_sheet_title or sheet_id}")

            progress.update(task_id, description="整表覆盖分析表")
            previous_row_count = int(upload_state.get("row_count") or 0)
            sync_meta = self.overwrite_feishu_sheets(
                access_token,
                sheet_id,
                sheets_data,
                previous_row_count=previous_row_count,
            )
            self._advance_stage(progress, task_id, "分析表同步完成")

            progress.update(task_id, description="保存分析表上传状态")
            self.save_upload_state(content_hash, len(sheets_data) - 1, upload_state_json)
            self._advance_stage(progress, task_id, "分析表上传状态已保存")

        lines = [
            f"目标子表: {resolved_sheet_title or sheet_id}",
            f"来源: {data_meta['source_file']}",
            f"视图行数: {data_meta['rows']}",
            f"视图列数: {data_meta['columns']}",
            f"去重主键: {data_meta.get('deduplicated_by', self.uid_column)}",
            f"清理旧行: {sync_meta.get('pruned_rows', 0)}",
            "上传方式: 整表覆盖",
        ]
        get_console().print(create_summary_panel(panel_title, lines, border_style="green"))
