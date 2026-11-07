from flakehunt.shrinker import shrink_failure


def make_fails(polluters, victim):
    calls = {"n": 0}

    def fails(order):
        calls["n"] += 1
        if victim not in order:
            return False
        before = order[:order.index(victim)]
        return all(p in before for p in polluters)

    return fails, calls


TESTS = ["t%d" % i for i in range(100)]


def test_single_polluter_among_100():
    fails, calls = make_fails(["t37"], "t90")
    assert shrink_failure(fails, TESTS, "t90") == ["t37", "t90"]
    assert calls["n"] < 60


def test_two_polluters_are_both_kept():
    fails, _ = make_fails(["t12", "t70"], "t95")
    assert shrink_failure(fails, TESTS, "t95") == ["t12", "t70", "t95"]


def test_failing_alone_returns_only_the_test():
    fails = lambda order: "t5" in order
    assert shrink_failure(fails, TESTS, "t5") == ["t5"]


def test_unknown_test_returns_original_order():
    fails, _ = make_fails([], "t1")
    assert shrink_failure(fails, TESTS, "nope") == TESTS


def test_first_test_has_no_prefix():
    fails, _ = make_fails([], "t0")
    assert shrink_failure(fails, TESTS, "t0") == ["t0"]
