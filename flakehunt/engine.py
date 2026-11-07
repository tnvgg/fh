"""Core logic: hunt for failures under perturbation, then classify each one."""
import random
from dataclasses import dataclass, field

from flakehunt.shrinker import shrink_failure

PROFILES = ("order", "seed", "timing", "combined")
ISO = 4  # isolation runs per check

BROKEN = "BROKEN"
ORDER = "ORDER-DEPENDENT"
SEED = "SEED-DEPENDENT"
TIMING = "TIMING-DEPENDENT"
PERTURB = "SEED+TIMING-DEPENDENT"
INTERMITTENT = "INTERMITTENT"


@dataclass
class Finding:
    nodeid: str
    kind: str
    min_order: list
    seed: object = None
    delay: float = 0.0
    repro_rate: str = ""
    traceback: str = ""
    original_order: list = field(default_factory=list)
    fail_runs: int = 0
    runs: int = 0


def failed_ids(data):
    if not data:
        return set()
    return {r["nodeid"] for r in data.get("results", []) if r["outcome"] in ("failed", "error")}


def traceback_of(data, nodeid):
    for r in (data or {}).get("results", []):
        if r["nodeid"] == nodeid and r["outcome"] in ("failed", "error"):
            return r.get("longrepr") or ""
    return ""


def plan_run(profile, all_tests, rng):
    order = list(all_tests)
    if profile in ("order", "combined"):
        rng.shuffle(order)
    seed = rng.randint(1, 9999) if profile in ("seed", "combined") else None
    delay = 0.1 if profile in ("timing", "combined") else 0.0
    return order, seed, delay


def hunt(runner, all_tests, runs, profile, rng=None, on_run=None):
    """Run the suite `runs` times under perturbation. Returns ({nodeid: info}, crashed_runs)."""
    rng = rng or random.Random()
    found, crashed = {}, 0
    for i in range(1, runs + 1):
        order, seed, delay = plan_run(profile, all_tests, rng)
        data = runner.run(order=order, seed=seed, delay=delay)
        new = []
        if data is None:
            crashed += 1
        else:
            for nid in sorted(failed_ids(data)):
                info = found.get(nid)
                if info is None:
                    info = found[nid] = {"order": order, "seed": seed, "delay": delay,
                                         "traceback": traceback_of(data, nid),
                                         "fail_runs": 0, "first_run": i}
                    new.append(nid)
                info["fail_runs"] += 1
        if on_run:
            on_run(i, new, found)
    return found, crashed


def count_fails(runner, nodeid, order, seed, delay, n):
    k = 0
    for _ in range(n):
        if nodeid in failed_ids(runner.run(order=order, seed=seed, delay=delay)):
            k += 1
    return k


def _perturb_kind(seed, delay):
    if delay and seed is not None:
        return PERTURB
    return TIMING if delay else SEED


def classify(runner, nodeid, info, runs, iso=ISO, say=None):
    """Decide what kind of failure this is, and find the smallest reproducer."""
    say = say or (lambda msg: None)
    seed, delay, order = info["seed"], info["delay"], info["order"]
    base = dict(nodeid=nodeid, traceback=info.get("traceback", ""), original_order=order,
                fail_runs=info["fail_runs"], runs=runs)
    perturbed = seed is not None or bool(delay)

    say("checking the test on its own")
    k0 = count_fails(runner, nodeid, [nodeid], None, 0.0, iso)
    if k0 == iso:
        return Finding(kind=BROKEN, min_order=[nodeid], repro_rate="%d/%d alone" % (k0, iso), **base)

    if k0 > 0:
        # fails sometimes even alone: intermittent. A stored seed may still make it repeatable.
        if perturbed:
            k1 = count_fails(runner, nodeid, [nodeid], seed, delay, iso)
            if k1 > 0:
                return Finding(kind=INTERMITTENT, min_order=[nodeid], seed=seed, delay=delay,
                               repro_rate="%d/%d alone with the saved seed/delay" % (k1, iso), **base)
        return Finding(kind=INTERMITTENT, min_order=[nodeid],
                       repro_rate="%d/%d alone" % (k0, iso), **base)

    if perturbed:
        say("checking the test alone under the same seed/delay")
        k1 = count_fails(runner, nodeid, [nodeid], seed, delay, iso)
        if k1 > 0:
            kind, use_seed, use_delay, rate = _perturb_kind(seed, delay), seed, delay, k1
            if seed is not None and delay:
                # combined profile: work out which perturbation is really needed
                say("separating seed from timing")
                ks = count_fails(runner, nodeid, [nodeid], seed, 0.0, iso)
                if ks == iso:
                    kind, use_delay, rate = SEED, 0.0, ks
                else:
                    kd = count_fails(runner, nodeid, [nodeid], None, delay, iso)
                    if kd > 0:
                        kind = TIMING  # keep the seed: it makes the jitter repeatable for replay
            return Finding(kind=kind, min_order=[nodeid], seed=use_seed, delay=use_delay,
                           repro_rate="%d/%d alone" % (rate, iso), **base)

    say("shrinking the failing order")
    min_order = shrink_failure(lambda o: nodeid in failed_ids(runner.run(order=o, seed=seed, delay=delay)),
                               order, nodeid)
    if len(min_order) > 1:
        say("confirming the reproducer")
        k2 = count_fails(runner, nodeid, min_order, seed, delay, 3)
        if k2 == 3:
            return Finding(kind=ORDER, min_order=min_order, seed=seed, delay=delay,
                           repro_rate="3/3", **base)
        if k2 > 0:
            return Finding(kind=INTERMITTENT, min_order=min_order, seed=seed, delay=delay,
                           repro_rate="%d/3 on the reduced order" % k2, **base)
    idx = order.index(nodeid) if nodeid in order else len(order) - 1
    return Finding(kind=INTERMITTENT, min_order=order[:idx + 1], seed=seed, delay=delay,
                   repro_rate="could not be reproduced again", **base)
