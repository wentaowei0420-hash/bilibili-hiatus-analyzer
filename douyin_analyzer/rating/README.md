# Douyin Rating Module

This package owns the Douyin rating system.

- `video_scoring.py`: builds video ratings from cached/source data.
- `creator_scoring.py`: builds creator ratings from source data plus video ratings.
- `store.py`: resolves database boundaries.

Database boundary:

- Source data stays in `export_store_db`: followings, progress-derived video state, cache inventory, archives, downloader records.
- Rating output stays in `rating_store_db`: `video_score_current`, `creator_score_current`, and manual rating tables.

All rating code should import through `douyin_analyzer.rating` or its submodules.
