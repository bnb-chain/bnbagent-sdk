import threading


import pytest


from bnbagent.core import preflight


def test_preflight_capacity_is_bounded_while_stalled_work_remains_alive():
    stop = threading.Event()
    futures = [preflight.submit_preflight(lambda _: stop.wait(2), {}) for _ in range(4)]
    try:
        with pytest.raises(TimeoutError):
            preflight.submit_preflight(lambda _: None, {})
        with pytest.raises(TimeoutError):
            futures[0].result(timeout=0.01)
    finally:
        stop.set()
        for future in futures:
            future.result(timeout=2)
