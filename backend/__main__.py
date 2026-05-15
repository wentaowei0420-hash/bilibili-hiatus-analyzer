from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    vendor_dir = Path(__file__).resolve().parent.parent / "runtime" / "vendor"
    if vendor_dir.exists():
        sys.path.insert(0, str(vendor_dir))

    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "Missing API dependencies. Install them with: pip install fastapi uvicorn"
        ) from exc

    host = os.getenv("HIATUS_API_HOST", "127.0.0.1")
    port = int(os.getenv("HIATUS_API_PORT", "8000"))
    uvicorn.run("backend.api:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
