"""Generates a demo project with a hidden order-dependent flake in a big suite."""
import os

FILES = {
    "app_state.py": '"""A module-level registry that tests accidentally share."""\nREGISTRY = {}\n',
    "conftest.py": '"""Shared pytest configuration for the demo project."""\n',
    "test_a_registry.py": (
        "import app_state\n\n\n"
        "def test_registry_starts_empty():\n"
        '    assert app_state.REGISTRY == {}, "registry leaked state: %r" % (app_state.REGISTRY,)\n'),
    "test_users.py": (
        "import app_state\n\n\n"
        "def test_register_user():\n"
        '    app_state.REGISTRY["alice"] = {"role": "admin"}\n'
        '    assert "alice" in app_state.REGISTRY\n'),
    "test_random.py": (
        "import random\n\n\n"
        "def test_retry_budget_roll():\n"
        '    assert random.random() > 0.3, "unlucky roll"\n'),
    "test_timing.py": (
        "import time\n\n\n"
        "def test_latency_budget():\n"
        "    start = time.time()\n"
        "    time.sleep(0.01)\n"
        '    assert time.time() - start < 0.05, "too slow"\n'),
}


def generate(directory, tests=100):
    os.makedirs(directory, exist_ok=True)
    for name, text in FILES.items():
        with open(os.path.join(directory, name), "w") as f:
            f.write(text)
    fillers = max(0, tests - 4)
    files = max(1, fillers // 10) if fillers else 0
    per = -(-fillers // files) if files else 0
    idx = 0
    for n in range(1, files + 1):
        body = []
        for _ in range(per):
            if idx >= fillers:
                break
            idx += 1
            body.append("def test_filler_%03d():\n    assert %d + 0 == %d\n" % (idx, idx, idx))
        with open(os.path.join(directory, "test_filler_%02d.py" % n), "w") as f:
            f.write("\n\n".join(body))
    return {"directory": directory, "tests": 4 + fillers, "files": 5 + files}
