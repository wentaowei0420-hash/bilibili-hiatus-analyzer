from threading import Event


class OperationCancelled(RuntimeError):
    pass


_STOP_EVENT = Event()


def request_stop():
    _STOP_EVENT.set()


def clear_stop():
    _STOP_EVENT.clear()


def stop_requested():
    return _STOP_EVENT.is_set()


def check_stop(message="用户请求终止运行"):
    if stop_requested():
        raise OperationCancelled(message)
