# Douyin Rating Module

This package owns the Douyin rating system.

- `video_scoring.py`: builds video ratings from cached/source data.
- `creator_scoring.py`: builds creator ratings from source data plus video ratings.
- `store.py`: resolves database boundaries.

Database boundary:

- Source data stays in `export_store_db`: followings, progress-derived video state, cache inventory, archives, downloader records.
- Rating output stays in `rating_store_db`: `video_score_current`, `creator_score_current`, and manual rating tables.

The old modules `douyin_analyzer.video_scoring`, `douyin_analyzer.creator_scoring`, and
`douyin_analyzer.rating_store` are compatibility wrappers. New code should import from
`douyin_analyzer.rating`.
