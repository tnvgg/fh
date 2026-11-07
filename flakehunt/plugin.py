"""pytest plugin used by FlakeHunt. Loaded in the child process with `-p flakehunt.plugin`.

It reorders tests, seeds randomness per test, adds timing jitter, and writes a JSON
report that the parent process reads.
"""
import json
import random
import sys
import time
import zlib

import pytest

_STATE = {"results": [], "order": [], "seed": None, "delay": 0.0, "rootdir": ""}


def pytest_addoption(parser):
    g = parser.getgroup("flakehunt")
    g.addoption("--flakehunt-out", action="store", default=None)
    g.addoption("--flakehunt-order-file", action="store", default=None)
    g.addoption("--flakehunt-delay", action="store", default="0.0")
    g.addoption("--flakehunt-seed", action="store", default=None)


def _install_jitter(max_delay, seed):
    """Make every time.sleep() a little longer and thread switches more frequent."""
    rng = random.Random(seed)
    real_sleep = time.sleep

    def jittery_sleep(secs, *args):
        real_sleep(secs + rng.uniform(0, max_delay))

    time.sleep = jittery_sleep
    try:
        sys.setswitchinterval(1e-6)
    except Exception:
        pass


def pytest_configure(config):
    _STATE["results"] = []
    seed = config.getoption("--flakehunt-seed")
    _STATE["seed"] = int(seed) if seed not in (None, "") else None
    delay = float(config.getoption("--flakehunt-delay") or 0.0)
    _STATE["delay"] = delay
    _STATE["rootdir"] = str(config.rootpath)
    if delay > 0:
        _install_jitter(delay, _STATE["seed"])


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(session, config, items):
    path = config.getoption("--flakehunt-order-file")
    if path:
        with open(path) as f:
            wanted = json.load(f)
        by_id = {it.nodeid: it for it in items}
        items[:] = [by_id[n] for n in wanted if n in by_id]
    _STATE["order"] = [it.nodeid for it in items]


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    seed = _STATE["seed"]
    if seed is None:
        return
    key = "%s:%s" % (seed, item.nodeid)
    random.seed(key)
    np = sys.modules.get("numpy")
    if np is not None:
        try:
            np.random.seed(zlib.crc32(key.encode()) % (2 ** 32))
        except Exception:
            pass


def pytest_runtest_logreport(report):
    if report.when == "call":
        outcome = report.outcome
    elif report.when in ("setup", "teardown") and report.outcome == "failed":
        outcome = "error"
    else:
        return
    _STATE["results"].append({
        "nodeid": report.nodeid,
        "outcome": outcome,
        "when": report.when,
        "duration": report.duration,
        "longrepr": report.longreprtext if report.failed else None,
    })


def pytest_sessionfinish(session, exitstatus):
    out = session.config.getoption("--flakehunt-out")
    if not out:
        return
    with open(out, "w") as f:
        json.dump({
            "exitstatus": int(exitstatus),
            "rootdir": _STATE["rootdir"],
            "order": _STATE["order"],
            "results": _STATE["results"],
        }, f)
