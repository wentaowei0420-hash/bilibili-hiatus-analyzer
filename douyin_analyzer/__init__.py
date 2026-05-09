__all__ = ["main", "run_analysis", "run_feishu_upload", "upload_main"]


def __getattr__(name):
    if name in __all__:
        from . import app

        return getattr(app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
