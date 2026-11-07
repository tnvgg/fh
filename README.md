# FlakeHunt

**Find the flaky test. Shrink it to a minimal reproducer. Get a fix that is proven to work.**

FlakeHunt is a reliability tool for `pytest`. It re-runs your suite under shuffled order, random seeds and
timing jitter, tells you *what kind* of flake each failure is, reduces it to the smallest reproducer
(100 tests down to 2 in the demo below), and uses the **BharatCode model API** to explain the cause and
propose a patch. The patch is only shown as "verified" after FlakeHunt re-runs the reproducer and shuffled
suites on a **copy** of your project and they pass.

```
flaky test in CI  ->  scan  ->  classify  ->  minimal reproducer  ->  model proposes patch  ->  verified on a copy  ->  --apply
```

## Why

"It passes locally and fails randomly in CI" costs hours. Existing tools can rerun or shuffle tests; FlakeHunt
goes further: it classifies the failure, keeps a one-command replay in a local vault, and closes the loop with
a patch that is checked by actually running your tests, not just by trusting the model.

## Quick start

Python 3.9+ and `pytest` in the same environment as your tests.

```bash
git clone https://github.com/<you>/flakehunt && cd flakehunt
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

cp .env.example .env        # add BHARATCODE_API_KEY (optional, enables the model features)
flakehunt doctor            # checks pytest and the BharatCode API connection

flakehunt demo              # creates demo_big/: 100 tests with a hidden order-dependent flake
flakehunt scan demo_big --runs 12 --profile combined --fix
flakehunt replay 1          # reproduce the saved failure again
flakehunt serve             # local dashboard at http://127.0.0.1:8000
```

To scan your own project, install FlakeHunt into the **same virtualenv as your project** (its tests run in that
environment) and run `flakehunt scan path/to/tests`.

## What a scan looks like

Real output from the demo, no model key set (`flakehunt scan demo_big --runs 12 --profile order --no-ai`, about 11 s):

```
Discovered 100 tests (0 failed in natural order).
Failure seen (run 2): demo_big/test_a_registry.py::test_registry_starts_empty
Failure seen (run 2): demo_big/test_random.py::test_retry_budget_roll

demo_big/test_a_registry.py::test_registry_starts_empty
Verdict: ORDER-DEPENDENT   failed in 4/12 runs   reproduces: 3/3
  1. demo_big/test_users.py::test_register_user
  2. demo_big/test_a_registry.py::test_registry_starts_empty

demo_big/test_random.py::test_retry_budget_roll
Verdict: INTERMITTENT   failed in 2/12 runs   reproduces: 2/4 alone
```

With `BHARATCODE_API_KEY` set, each flaky test also gets a model explanation (suspected cause, evidence, fix), and
`--fix` adds a patch that is shown only if it passes verification.

## How it works

| Step | What happens |
|---|---|
| **Hunt** | Runs the suite N times under a profile: `order` (shuffle), `seed` (per-test `random`/NumPy seeds + `PYTHONHASHSEED`), `timing` (jitter on `time.sleep`, faster thread switching), or `combined`. |
| **Classify** | Re-runs each failing test alone and under the saved seed/delay: **BROKEN** (fails alone, not flaky and not saved), **ORDER-DEPENDENT**, **SEED-DEPENDENT**, **TIMING-DEPENDENT**, or **INTERMITTENT**. |
| **Shrink** | Delta debugging (ddmin) over the tests that ran before the failure, then 3 confirmation runs. |
| **Vault** | Saves the reproducer plus the seed and delay in SQLite, so `flakehunt replay N` reproduces it deterministically. |
| **Explain** | The BharatCode model (`qwen-3.8-27b` by default) gets the reproducer, traceback and the relevant source files. |
| **Fix + verify** | The model returns search/replace edits. They are applied to a temporary copy of the project; the reproducer must pass 3/3 and the test must pass in 3 shuffled full-suite runs. Patches that remove an `assert` or touch files outside the project are rejected. `flakehunt fix N --apply` writes a verified patch to your files. |

Other commands: `vault-list`, `report N`, `fix N`, `bench`, `doctor`. Add `--ci` to `scan` to exit 1 when flakes are found.

## Uses the BharatCode model API

All model calls go through `flakehunt/ai.py` (OpenAI-compatible chat completions on `https://bharatcode.ai/api/model/v1`).
The model is used twice: to explain a failure and to propose a patch in `flakehunt/fixer.py`. Configure with
`BHARATCODE_API_KEY`, and optionally `FLAKEHUNT_MODEL` or `BHARATCODE_BASE_URL`. Everything else runs without a key.

## Benchmark

`flakehunt bench` runs 5 small hand-written suites (order, seed, timing, stable, broken) under every profile,
8 runs per cell. Measured result: **14/14 expected detections, 0 false positives** (82 s).

This is a sanity check on synthetic suites, not evidence of real-world recall. Seed-dependent tests are often
reported as `INTERMITTENT` because unseeded randomness makes them fail sometimes even alone; they still come
with a saved seed for replay.

## Limits

- `pytest` only. `pytest-xdist` (`-n`) is not supported; `pytest-randomly` is disabled during scans.
- The timing profile adds jitter to `time.sleep` and thread switching. It does not simulate CPU load and does not touch `asyncio.sleep`.
- Shrinking only reduces the tests that ran *before* the failing one.
- Verified fixes currently cover order-dependent flakes. A verified patch can still be a poor design: read the diff.
- Scans run your tests many times. Do not point them at tests that touch production data.
- Prior art exists for parts of this (for example `pytest-randomly`, `pytest-flakefinder`, `detect-test-pollution`); FlakeHunt's angle is the combination of classification, replay vault and verified model-proposed fixes.

## Development

```bash
pip install -e . httpx
python -m pytest tests -q     # 27 tests; the model is stubbed, no API key needed
```

Layout: `flakehunt/engine.py` (hunt + classify), `shrinker.py` (ddmin), `plugin.py` (pytest plugin),
`ai.py` + `fixer.py` (BharatCode API + verified patches), `db.py`, `dashboard.py`, `bench.py`, `demo.py`.

Built for the BharatCode Build League. MIT licensed.
