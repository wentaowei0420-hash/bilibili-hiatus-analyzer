# Hiatus Analyzer API

This backend is the new boundary between UI and analyzer business logic.
It intentionally does not import or modify `douyin-downloader-main`.

## Run

```bash
python -m pip install -r requirements.txt
python -m backend
```

The default address is:

```text
http://127.0.0.1:8000/
```

You can override it with:

```bash
set HIATUS_API_HOST=127.0.0.1
set HIATUS_API_PORT=8000
python -m backend
```

## Core API

```text
GET  /api/health
GET  /api/capabilities
GET  /api/config/defaults
GET  /api/gui/metadata
POST /api/jobs
GET  /api/jobs
GET  /api/jobs/{job_id}
GET  /api/jobs/{job_id}/events?offset=0
POST /api/jobs/{job_id}/cancel
GET  /api/bilibili/cookie-status
GET  /api/douyin/stats
GET  /api/douyin/rating-overview
GET  /api/douyin/creator-detail/{uploader_id}
POST /api/douyin/creator-manual-grade
GET  /api/douyin/status-reset
POST /api/douyin/status-reset
GET  /api/douyin/archive
POST /api/douyin/archive
POST /api/douyin/archive/restore
```

Long analyzer runs are represented as jobs. The frontend starts a job,
polls status and logs, and may request cancellation. The worker queue is
single-threaded by default because Douyin browser automation and the shared
runtime stop flag are not safe to run concurrently.

## Internal Boundaries

- `common.domain_models` defines typed creator, video, and analysis result
  models used as the migration target away from raw dictionaries.
- `common.reporting` defines the reporter/progress interface used by
  analyzers.
- `bilibili_analyzer.console_reporter.RichAnalyzerReporter` preserves the
  existing Rich console behavior for CLI runs.
- `backend.reporting.JobReporter` sends analyzer messages and progress into
  the job log stream for API/Web runs.
- `common.repositories.AnalyzerCacheRepository` wraps platform cache stores so
  cache access can move behind a repository boundary without changing existing
  JSON/CSV cache files.

## Desktop GUI Boundary

- `gui.py` owns PyQt widgets, layout, dialogs, and event wiring.
- `gui_models.py` owns GUI-facing data models such as `RunConfig`.
- `gui_backend_client.py` owns HTTP API calls, job creation, cancellation, and
  log polling for the desktop GUI.
- `backend.gui_data` owns GUI data reads/writes for Douyin stats, rating
  overview, creator details, full-status reset, and archive management.
- `backend.config_defaults` owns analyzer default configuration reads for GUI
  initialization.
- `backend.gui_schema` owns GUI display metadata, including field definitions,
  options, buckets, grade order, and table columns exposed through
  `/api/gui/metadata`.
- Long-running GUI actions should be added to `backend.job_models.JobKind` and
  dispatched by `backend.task_runner`, not implemented directly inside `gui.py`.
- GUI data views should expose backend API endpoints rather than reading SQLite
  or analyzer cache files directly from `gui.py`.
- GUI default values should come from `/api/config/defaults`, not analyzer
  config imports inside `gui.py`.
- GUI display protocol should come from `/api/gui/metadata`; `gui.py` should
  only render widgets and wire events.
