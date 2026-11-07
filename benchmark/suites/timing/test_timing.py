import time


def test_latency_budget():
    start = time.time()
    time.sleep(0.01)
    assert time.time() - start < 0.05, "too slow"
