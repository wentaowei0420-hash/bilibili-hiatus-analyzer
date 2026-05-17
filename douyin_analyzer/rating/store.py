from pathlib import Path


def rating_store_db_path(config):
    return Path(getattr(config, "rating_store_db", None) or getattr(config, "export_store_db", ""))


def source_store_db_path(config):
    return Path(getattr(config, "export_store_db", ""))
