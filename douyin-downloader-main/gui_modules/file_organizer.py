from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path


GRADE_DIR_NAMES = {
    "S": "S级",
    "A": "A级",
    "B": "B级",
    "C": "C级",
    "D": "D级",
}
GRADE_PREFIX_RE = re.compile(r"^\s*([SABCD])(?:级)?(?:[_\-\s]|$)", re.IGNORECASE)


@dataclass(frozen=True)
class OrganizeResult:
    root: Path
    moved_count: int
    deleted_webp_count: int
    skipped_count: int

    def to_message(self) -> str:
        return (
            f"整理完成：移动 {self.moved_count} 项，"
            f"删除 WEBP {self.deleted_webp_count} 个，"
            f"跳过 {self.skipped_count} 项。\n"
            f"目录：{self.root}"
        )


def organize_download_directory(download_dir: str | Path) -> OrganizeResult:
    root = Path(download_dir).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"下载目录不存在：{root}")
    if not root.is_dir():
        raise NotADirectoryError(f"下载路径不是目录：{root}")

    deleted_webp_count = delete_webp_files(root)
    moved_count = 0
    skipped_count = 0

    for child in list(root.iterdir()):
        if _is_grade_dir(child):
            continue
        grade = grade_from_name(child.name)
        if not grade:
            skipped_count += 1
            continue

        target_dir = root / GRADE_DIR_NAMES[grade]
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = unique_destination(target_dir / child.name)
        shutil.move(str(child), str(destination))
        moved_count += 1

    return OrganizeResult(
        root=root,
        moved_count=moved_count,
        deleted_webp_count=deleted_webp_count,
        skipped_count=skipped_count,
    )


def delete_webp_files(root: Path) -> int:
    deleted = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() != ".webp":
            continue
        path.unlink()
        deleted += 1
    return deleted


def grade_from_name(name: str) -> str:
    match = GRADE_PREFIX_RE.match(name)
    if not match:
        return ""
    return match.group(1).upper()


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    for index in range(1, 10000):
        candidate = parent / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"无法生成不冲突的目标路径：{path}")


def _is_grade_dir(path: Path) -> bool:
    return path.is_dir() and path.name in set(GRADE_DIR_NAMES.values())
