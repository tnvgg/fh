"""Delta debugging (ddmin) for test-order failures."""


def shrink_failure(fails, original_order, failing_test, max_checks=80):
    """Find a small list of earlier tests that still makes `failing_test` fail.

    `fails(order)` runs the given order and returns True if `failing_test` fails.
    Returns the reduced order, ending with `failing_test`.
    """
    try:
        idx = original_order.index(failing_test)
    except ValueError:
        return list(original_order)
    seq = list(original_order[:idx])
    if not seq or fails([failing_test]):
        return [failing_test]

    checks = [0]

    def check(candidate):
        checks[0] += 1
        return fails(candidate + [failing_test])

    n = 2
    while len(seq) >= 2 and checks[0] < max_checks:
        size = -(-len(seq) // n)
        chunks = [seq[i:i + size] for i in range(0, len(seq), size)]
        reduced = False
        for c in chunks:
            if check(c):
                seq, n, reduced = c, 2, True
                break
        if not reduced and len(chunks) > 2:
            for i in range(len(chunks)):
                comp = [t for j, c in enumerate(chunks) if j != i for t in c]
                if check(comp):
                    seq, n, reduced = comp, max(n - 1, 2), True
                    break
        if not reduced:
            if n >= len(seq):
                break
            n = min(len(seq), n * 2)
    return seq + [failing_test]
