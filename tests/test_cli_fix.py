"""The whole CLI flow with the model stubbed: scan --fix, fix --apply, then the flake is gone."""
import os

from typer.testing import CliRunner

from flakehunt import ai, db, engine, main
from flakehunt.demo import generate
from flakehunt.runner import Runner

GOOD = (
    "<<<<<<< FILE {c}\n<<<<<<< SEARCH\n=======\nimport pytest\nimport app_state\n\n\n"
    "@pytest.fixture(autouse=True)\ndef _reset_registry():\n    app_state.REGISTRY.clear()\n    yield\n"
    "    app_state.REGISTRY.clear()\n>>>>>>> END\n")


def test_scan_fix_apply_replay(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FLAKEHUNT_DB", str(tmp_path / "vault.sqlite"))
    monkeypatch.setenv("BHARATCODE_API_KEY", "dummy")
    generate("demo", tests=30)
    monkeypatch.setattr(ai, "analyze_failure", lambda f, s: "## Suspected cause\nshared REGISTRY dict")

    def propose(f, sources, err=None):
        folder = os.path.dirname(f.min_order[0].split("::")[0])
        return GOOD.format(c=os.path.join(folder, "conftest.py") if folder else "conftest.py")

    monkeypatch.setattr(ai, "propose_fix", propose)
    cli = CliRunner()

    out = cli.invoke(main.app, ["scan", "demo", "--runs", "12", "--rng-seed", "1", "--fix"])
    assert out.exit_code == 0, out.output
    rows = [r for r in db.list_failures() if r["kind"] == engine.ORDER]
    assert len(rows) == 1 and rows[0]["fix_verified"] == 1 and "_reset_registry" in rows[0]["fix_diff"]
    assert "shared REGISTRY" in rows[0]["ai_explanation"]
    assert "_reset_registry" not in (tmp_path / "demo" / "conftest.py").read_text()

    out = cli.invoke(main.app, ["fix", str(rows[0]["id"]), "--apply"])
    assert out.exit_code == 0, out.output
    assert "_reset_registry" in (tmp_path / "demo" / "conftest.py").read_text()

    import json
    runner = Runner(rows[0]["target"], rows[0]["root"])
    data = runner.run(order=json.loads(rows[0]["min_order"]))
    assert rows[0]["nodeid"] not in engine.failed_ids(data)


def test_report_and_vault_list_work(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FLAKEHUNT_DB", str(tmp_path / "v.sqlite"))
    cli = CliRunner()
    assert "empty" in cli.invoke(main.app, ["vault-list"]).output
    assert cli.invoke(main.app, ["report", "99"]).exit_code == 1
