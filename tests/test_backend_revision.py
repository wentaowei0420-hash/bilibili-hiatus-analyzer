import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.revision import API_VERSION, BACKEND_SERVICE, backend_revision, health_payload
from gui_backend_client import _health_payload_is_compatible


def test_health_payload_includes_current_revision():
    payload = health_payload()

    assert payload["status"] == "ok"
    assert payload["service"] == BACKEND_SERVICE
    assert payload["api_version"] == API_VERSION
    assert payload["revision"] == backend_revision()


def test_health_payload_compatibility_requires_matching_revision():
    payload = health_payload()

    assert _health_payload_is_compatible(payload) is True
    assert _health_payload_is_compatible({**payload, "revision": "old-revision"}) is False
