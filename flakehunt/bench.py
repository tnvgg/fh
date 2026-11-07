"""Detection benchmark: known flaky / stable / broken suites x every profile."""
import json
import os
import random

from flakehunt import engine
from flakehunt.runner import Runner


def run_bench(directory, runs=8, profiles=engine.PROFILES, rng_seed=1, say=None):
    say = say or (lambda m: None)
    with open(os.path.join(directory, "manifest.json")) as f:
        manifest = json.load(f)
    rows = []
    for suite in manifest["suites"]:
        target = os.path.join(directory, suite["path"])
        runner, base = Runner.discover(target)
        if runner is None:
            rows.append({"suite": suite["name"], "profile": "-", "status": "ERROR", "detail": "no tests found"})
            continue
        tests = base["order"]
        for profile in profiles:
            say("%s / %s" % (suite["name"], profile))
            rng = random.Random("%s:%s:%s" % (rng_seed, suite["name"], profile))
            found, _ = engine.hunt(runner, tests, runs, profile, rng)
            findings = {n: engine.classify(runner, n, info, runs) for n, info in found.items()}
            expect = suite["expect"]
            if expect is None:
                status = "PASS" if not findings else "FALSE-POSITIVE"
                detail = "no findings" if not findings else ", ".join(
                    "%s=%s" % (n.split("::")[-1], f.kind) for n, f in findings.items())
                rows.append({"suite": suite["name"], "profile": profile, "status": status,
                             "detail": detail, "expected": True})
                continue
            hits = [(n, f) for n, f in findings.items() if any(n.endswith(e) for e in expect["nodeids"])]
            should = profile in expect["profiles"]
            if hits:
                good = any(f.kind in expect["kinds"] for _, f in hits)
                status = "PASS" if good else "WRONG-KIND"
                detail = ", ".join("%s=%s" % (n.split("::")[-1], f.kind) for n, f in hits)
            else:
                status = "MISS" if should else "n/a"
                detail = "not found" if should else "not expected under this profile"
            rows.append({"suite": suite["name"], "profile": profile, "status": status,
                         "detail": detail, "expected": should})
    return rows


def summarize(rows):
    exp = [r for r in rows if r.get("expected")]
    ok = [r for r in exp if r["status"] == "PASS"]
    fp = [r for r in rows if r["status"] == "FALSE-POSITIVE"]
    return {"expected_cells": len(exp), "passed": len(ok), "false_positives": len(fp)}
