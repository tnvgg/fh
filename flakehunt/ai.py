"""BharatCode model API client (OpenAI-compatible chat completions)."""
import os
import re
import time

import requests

SYSTEM = ("You are a senior Python engineer who debugs flaky pytest suites. "
          "Be concrete and brief. Never invent code that you were not shown.")

KIND_HELP = {
    "ORDER-DEPENDENT": "It passes alone but fails after the earlier tests in the reproducer ran: shared or leaked state.",
    "SEED-DEPENDENT": "It passes normally but fails under a specific random/hash seed.",
    "TIMING-DEPENDENT": "It passes normally but fails when sleeps and thread switches get jittery.",
    "SEED+TIMING-DEPENDENT": "It fails only under a specific seed combined with timing jitter.",
    "INTERMITTENT": "It fails some of the time even when run alone.",
}


class AIError(Exception):
    pass


def base_url():
    return os.environ.get("BHARATCODE_BASE_URL", "https://bharatcode.ai/api/model/v1").rstrip("/")


def model():
    return os.environ.get("FLAKEHUNT_MODEL", "qwen-3.8-27b")


def enabled():
    return bool(os.environ.get("BHARATCODE_API_KEY"))


def chat(user, system=SYSTEM, temperature=0.2, retries=3):
    key = os.environ.get("BHARATCODE_API_KEY")
    if not key:
        raise AIError("BHARATCODE_API_KEY is not set")
    body = {"model": model(), "temperature": temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    last = ""
    for attempt in range(retries):
        try:
            r = requests.post(base_url() + "/chat/completions", json=body,
                              headers={"Authorization": "Bearer " + key}, timeout=120)
        except requests.RequestException as e:
            last = str(e)
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 200:
            try:
                text = r.json()["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, ValueError):
                raise AIError("unexpected API response shape")
            return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
        last = "HTTP %d: %s" % (r.status_code, r.text[:200])
        if r.status_code in (429, 500, 502, 503):
            time.sleep(2 ** attempt)
            continue
        break
    raise AIError(last or "request failed")


def _sources_block(sources):
    return "\n\n".join("### %s\n```python\n%s\n```" % (p, t) for p, t in sources)


def _repro_block(f):
    lines = "\n".join("%d. %s" % (i + 1, n) for i, n in enumerate(f.min_order))
    extra = ""
    if f.seed is not None:
        extra += "\nSeed used: %s" % f.seed
    if f.delay:
        extra += "\nTiming jitter used: up to %ss per sleep" % f.delay
    return lines + extra


def analyze_failure(f, sources):
    prompt = (
        "Flaky pytest test: %s\nClassification: %s. %s\n\n"
        "Minimal reproducer (tests run in this order, the last one fails):\n%s\n\n"
        "Traceback:\n%s\n\nSource files involved:\n%s\n\n"
        "Reply in markdown with exactly three short sections:\n"
        "1. Suspected cause (name the exact shared object / seed / timing assumption)\n"
        "2. Evidence (quote lines from the code or traceback)\n"
        "3. Fix (the smallest change; prefer test isolation over weakening assertions)"
    ) % (f.nodeid, f.kind, KIND_HELP.get(f.kind, ""), _repro_block(f),
         (f.traceback or "")[:3000], _sources_block(sources))
    return chat(prompt)


FIX_FORMAT = """Reply with one or more edit blocks and nothing else, in exactly this format:

<<<<<<< FILE path/relative/to/the/project/root.py
<<<<<<< SEARCH
exact existing text to replace (must occur exactly once in that file; leave EMPTY to append to the file or create it)
=======
replacement text
>>>>>>> END"""


def propose_fix(f, sources, previous_error=None):
    prompt = (
        "A pytest test fails only because of test pollution.\nFailing test: %s\n"
        "Minimal reproducer (the last test fails, the earlier ones leak state):\n%s\n\n"
        "Traceback:\n%s\n\nSource files involved (paths are relative to the project root):\n%s\n\n"
        "Fix the pollution with the SMALLEST change. Prefer an autouse fixture in conftest.py that "
        "resets the leaked state, or isolating the state inside the polluting test. "
        "Do NOT delete, skip or weaken any assertion.\n\n%s"
    ) % (f.nodeid, _repro_block(f), (f.traceback or "")[:2500], _sources_block(sources), FIX_FORMAT)
    if previous_error:
        prompt += "\n\nYour previous attempt was rejected: %s\nTry again." % previous_error
    return chat(prompt, temperature=0.1)
