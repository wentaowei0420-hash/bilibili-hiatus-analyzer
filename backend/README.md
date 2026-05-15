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
POST /api/jobs
GET  /api/jobs
GET  /api/jobs/{job_id}
GET  /api/jobs/{job_id}/events?offset=0
POST /api/jobs/{job_id}/cancel
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
