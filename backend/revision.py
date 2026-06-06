from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path


API_VERSION = "9"
BACKEND_SERVICE = "hiatus-backend"
ROOT_DIR = Path(__file__).resolve().parent.parent
SOURCE_DIRS = (
    ROOT_DIR / "backend",
    ROOT_DIR / "common",
    ROOT_DIR / "douyin_analyzer",
    ROOT_DIR / "bilibili_analyzer",
)


@lru_cache(maxsize=1)
def backend_revision() -> str:
    digest = hashlib.sha256()
    for base_dir in SOURCE_DIRS:
        for path in sorted(base_dir.rglob("*.py")):
            digest.update(path.relative_to(ROOT_DIR).as_posix().encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def health_payload() -> dict[str, str]:
    return {
        "status": "ok",
        "service": BACKEND_SERVICE,
        "api_version": API_VERSION,
        "revision": backend_revision(),
    }
