import csv
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def atomic_write_text(path, text, encoding="utf-8"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding=encoding,
            delete=False,
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as tmp_file:
            tmp_file.write(text)
            tmp_name = tmp_file.name
        os.replace(tmp_name, path)
    except Exception:
        if tmp_name:
            Path(tmp_name).unlink(missing_ok=True)
        raise


def atomic_write_json(path, payload, encoding="utf-8", indent=None, separators=None):
    text = json.dumps(payload, ensure_ascii=False, indent=indent, separators=separators)
    atomic_write_text(path, text, encoding=encoding)


def atomic_write_csv(path, fieldnames, rows, header_row=None, encoding="utf-8-sig"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = None
    try:
        with NamedTemporaryFile(
            "w",
            encoding=encoding,
            newline="",
            delete=False,
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as tmp_file:
            writer = csv.DictWriter(tmp_file, fieldnames=fieldnames, extrasaction="ignore")
            writer.writerow(header_row or {field: field for field in fieldnames})
            writer.writerows(rows or [])
            tmp_name = tmp_file.name
        os.replace(tmp_name, path)
    except Exception:
        if tmp_name:
            Path(tmp_name).unlink(missing_ok=True)
        raise
