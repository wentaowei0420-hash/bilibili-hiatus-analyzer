from pathlib import Path


RATING_TABLES = (
    "video_score_current",
    "creator_score_current",
    "bilibili_creator_manual_rating",
)


def rating_store_db_path(config):
    explicit = getattr(config, "rating_store_db", None)
    if explicit:
        return Path(explicit)
    source_db = Path(getattr(config, "export_store_db", ""))
    if source_db.name:
        return source_db.with_name("bilibili_rating_store.db")
    return source_db


def source_store_db_path(config):
    return Path(getattr(config, "export_store_db", ""))
