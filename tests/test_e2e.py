"""End to end: real pytest subprocesses on a generated demo project. The model is stubbed."""
import os
import random

import pytest

from flakehunt import ai, engine, fixer
from flakehunt.demo import generate
from flakehunt.runner import Runner

VICTIM = "test_a_registry.py::test_registry_starts_empty"
POLLUTER = "test_users.py::test_register_user"


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    d = tmp_path_factory.mktemp("proj") / "demo"
    generate(str(d), tests=40)
    runner, base = Runner.discover(str(d))
    assert runner is not None
    return runner, base["order"]


@pytest.fixture(scope="module")
def finding(project):
    runner, tests = project
    found, crashed = engine.hunt(runner, tests, 12, "order", random.Random(1))
    victim = next(n for n in found if n.endswith(VICTIM))
    return engine.classify(runner, victim, found[victim], 12)


def test_victim_passes_in_natural_order(project):
    # the demo also holds an intentionally random test, so only the victim is checked here
    runner, tests = project
    assert not any(n.endswith(VICTIM) for n in engine.failed_ids(runner.run()))


def test_order_dependent_flake_is_found_and_shrunk(finding):
    assert finding.kind == engine.ORDER
    assert [n.split("/")[-1] for n in finding.min_order] == [POLLUTER, VICTIM]
    assert finding.repro_rate == "3/3"


def test_replay_reproduces_the_minimal_order(project, finding):
    runner, _ = project
    assert finding.nodeid in engine.failed_ids(runner.run(order=finding.min_order))


def test_always_failing_test_is_broken_not_flaky(tmp_path):
    (tmp_path / "test_a.py").write_text("def test_bad():\n    assert False\n\n\ndef test_ok():\n    assert True\n")
    runner, base = Runner.discover(str(tmp_path))
    found, _ = engine.hunt(runner, base["order"], 2, "order", random.Random(1))
    f = engine.classify(runner, next(iter(found)), next(iter(found.values())), 2)
    assert f.kind == engine.BROKEN


def test_fixture_errors_are_detected(tmp_path):
    (tmp_path / "test_s.py").write_text(
        "import pytest\n\n@pytest.fixture\ndef boom():\n    raise RuntimeError('x')\n\n"
        "def test_x(boom):\n    assert True\n\ndef test_y():\n    assert True\n")
    runner, base = Runner.discover(str(tmp_path))
    assert any(n.endswith("test_x") for n in engine.failed_ids(base))


GOOD_FIX = (
    "<<<<<<< FILE {c}\n<<<<<<< SEARCH\n=======\nimport pytest\nimport app_state\n\n\n"
    "@pytest.fixture(autouse=True)\ndef _reset_registry():\n    app_state.REGISTRY.clear()\n    yield\n"
    "    app_state.REGISTRY.clear()\n>>>>>>> END\n")
USELESS_FIX = (
    "<<<<<<< FILE {c}\n<<<<<<< SEARCH\n=======\nimport pytest\n\n\n@pytest.fixture(autouse=True)\n"
    "def _noop():\n    yield\n>>>>>>> END\n")
WEAKENING_FIX = (
    "<<<<<<< FILE {t}\n<<<<<<< SEARCH\n    assert app_state.REGISTRY == {{}}\n=======\n    pass\n"
    ">>>>>>> END\n")


def _stub(monkeypatch, template, finding):
    folder = os.path.dirname(finding.min_order[0].split("::")[0])
    conftest = os.path.join(folder, "conftest.py") if folder else "conftest.py"
    test_file = finding.nodeid.split("::")[0]
    monkeypatch.setattr(ai, "propose_fix",
                        lambda f, sources, err=None: template.format(c=conftest, t=test_file))


def test_good_patch_is_verified(monkeypatch, project, finding):
    runner, tests = project
    _stub(monkeypatch, GOOD_FIX, finding)
    res = fixer.propose_and_verify(runner, finding, tests, attempts=1)
    assert res.ok and "_reset_registry" in res.diff
    # the real project must be untouched: verification happens on a copy
    assert finding.nodeid in engine.failed_ids(runner.run(order=finding.min_order))


def test_useless_patch_is_rejected_by_verification(monkeypatch, project, finding):
    runner, tests = project
    _stub(monkeypatch, USELESS_FIX, finding)
    res = fixer.propose_and_verify(runner, finding, tests, attempts=1)
    assert not res.ok and "verification failed" in res.message


def test_test_weakening_patch_is_rejected_by_guard(monkeypatch, project, finding):
    runner, tests = project
    _stub(monkeypatch, WEAKENING_FIX, finding)
    res = fixer.propose_and_verify(runner, finding, tests, attempts=1)
    assert not res.ok and "assert" in res.message
