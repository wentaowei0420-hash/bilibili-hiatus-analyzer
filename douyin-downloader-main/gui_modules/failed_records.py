from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any


FAILED_RECORD_FIELDS = [
    "aweme_id",
    "video_url",
    "uploader_name",
    "video_title",
    "recorded_at",
]


def load_failed_aweme_ids(path: Path) -> set[str]:
    return set(read_failed_rows(path).keys())


def append_failed_record(path: Path, row: dict[str, Any]) -> None:
    aweme_id = str(row.get("aweme_id") or "").strip()
    if not aweme_id:
        return

    rows = read_failed_rows(path)
    rows[aweme_id] = {
        "aweme_id": aweme_id,
        "video_url": str(row.get("video_url") or "").strip(),
        "uploader_name": str(row.get("uploader_name") or "").strip(),
        "video_title": str(row.get("video_title") or "").strip(),
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_failed_rows(path, rows)


def remove_failed_record(path: Path, aweme_id: Any) -> None:
    aweme_id = str(aweme_id or "").strip()
    if not aweme_id or not path.exists():
        return

    rows = read_failed_rows(path)
    if aweme_id not in rows:
        return
    rows.pop(aweme_id, None)
    write_failed_rows(path, rows)


def read_failed_rows(path: Path) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    if not path.exists():
        return rows

    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                aweme_id = str((row or {}).get("aweme_id") or "").strip()
                if aweme_id:
                    rows[aweme_id] = {
                        key: str(value or "") for key, value in row.items()
                    }
    except Exception:
        return {}
    return rows


def write_failed_rows(path: Path, rows: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FAILED_RECORD_FIELDS)
        writer.writeheader()
        for aweme_id in sorted(rows):
            writer.writerow(
                {
                    field: rows[aweme_id].get(field, "")
                    for field in FAILED_RECORD_FIELDS
                }
            )
