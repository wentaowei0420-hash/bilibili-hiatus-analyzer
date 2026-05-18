from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    vendor_dir = Path(__file__).resolve().parent.parent / "runtime" / "vendor"
    if vendor_dir.exists():
        sys.path.insert(0, str(vendor_dir))

    host = os.getenv("HIATUS_API_HOST", "127.0.0.1")
    port = int(os.getenv("HIATUS_API_PORT", "8000"))
    try:
        import uvicorn

        uvicorn.run("backend.api:app", host=host, port=port, reload=False)
    except Exception as exc:
        print(f"FastAPI backend unavailable, falling back to stdlib backend: {exc}", flush=True)
        from .stdlib_api import run

        run(host=host, port=port)


if __name__ == "__main__":
    main()
