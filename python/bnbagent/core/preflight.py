"""Bounded preflight work; caller timeout never joins a stalled RPC worker."""

from concurrent.futures import Future
from threading import BoundedSemaphore, Thread

_SLOTS = BoundedSemaphore(4)


def submit_preflight(function, params):
    if not _SLOTS.acquire(blocking=False):
        raise TimeoutError("Preflight RPC capacity is busy")
    future = Future()

    def run():
        try:
            if not future.set_running_or_notify_cancel():
                return
            try:
                future.set_result(function(params))
            except BaseException as exc:
                future.set_exception(exc)
        finally:
            _SLOTS.release()

    try:
        # A stalled RPC must neither grow queued work nor block interpreter exit.
        Thread(target=run, daemon=True, name="bnb-preflight").start()
    except BaseException:
        _SLOTS.release()
        raise
    return future
