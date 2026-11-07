"""FlakeHunt command line interface."""
import json
import os
import random
import sys
from typing import Optional

import typer
from dotenv import find_dotenv, load_dotenv
from rich.align import Align
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.syntax import Syntax
from rich.table import Table

from flakehunt import ai, db, engine, fixer

from flakehunt.runner import Runner

load_dotenv(find_dotenv(usecwd=True))

app = typer.Typer(
    help="FlakeHunt: find flaky pytest tests, shrink them to a minimal reproducer, get a verified fix.",
    add_completion=False)
console = Console()

KIND_STYLE = {"ORDER-DEPENDENT": "yellow", "SEED-DEPENDENT": "magenta", "TIMING-DEPENDENT": "cyan",
              "SEED+TIMING-DEPENDENT": "magenta", "INTERMITTENT": "red", "BROKEN": "dim"}


def banner():
    console.print(Align.center("[bold cyan]FLAKEHUNT[/bold cyan]"))
    console.print(Align.center("[green]Find the randomness. Reproduce the failure. Catch the bug.[/green]"))
    console.print()


def kind_tag(kind):
    return "[bold %s]%s[/bold %s]" % (KIND_STYLE.get(kind, "white"), kind, KIND_STYLE.get(kind, "white"))


def reproducer_panel(order, title):
    t = Table(show_header=False, box=None, padding=(0, 2))
    for i, n in enumerate(order):
        t.add_row("[dim]%d.[/dim]" % (i + 1), "[yellow]%s[/yellow]" % escape(n))
    return Panel(t, title="[bold yellow]%s[/bold yellow]" % title, border_style="yellow", expand=False)


def finding_from_row(row):
    return engine.Finding(
        nodeid=row["nodeid"], kind=row["kind"] or "INTERMITTENT", min_order=json.loads(row["min_order"]),
        seed=row["seed"], delay=row["delay"] or 0.0, repro_rate=row["repro_rate"] or "",
        traceback=row["traceback"] or "", original_order=json.loads(row["original_order"] or "[]"))


def show_fix(result):
    if result.ok:
        console.print(Panel(Syntax(result.diff, "diff", theme="ansi_dark"), border_style="green",
                            title="[bold green]Verified fix[/bold green] (reproducer + 3 shuffled runs pass)"))
    else:
        console.print(Panel(escape(result.message), border_style="red",
                            title="[bold red]No verified fix[/bold red]"))


@app.command()
def scan(
    target: str = typer.Argument(..., help="Directory or file with pytest tests"),
    runs: int = typer.Option(8, help="Number of perturbed runs"),
    profile: str = typer.Option("order", help="order | seed | timing | combined"),
    fix: bool = typer.Option(False, "--fix", help="Ask the model for a patch and verify it"),
    no_ai: bool = typer.Option(False, "--no-ai", help="Skip all model calls"),
    timeout: int = typer.Option(120, help="Seconds allowed per pytest run"),
    rng_seed: Optional[int] = typer.Option(None, help="Make the scan itself repeatable"),
    ci: bool = typer.Option(False, "--ci", help="Exit with code 1 if flaky tests are found"),
):
    """Hunt for flaky tests, classify them, shrink the reproducer, and save it to the vault."""
    if profile not in engine.PROFILES:
        raise typer.BadParameter("profile must be one of: " + ", ".join(engine.PROFILES))
    db.init_db()
    banner()
    info = Table.grid(padding=(0, 2))
    info.add_row("[bold cyan]Target:[/bold cyan]", escape(target))
    info.add_row("[bold cyan]Runs:[/bold cyan]", str(runs))
    info.add_row("[bold cyan]Profile:[/bold cyan]", "[magenta]%s[/magenta]" % profile.upper())
    use_ai = ai.enabled() and not no_ai
    info.add_row("[bold cyan]Model:[/bold cyan]", ai.model() + " (BharatCode)" if use_ai else "off")
    console.print(Panel(info, title="[bold green]Scan configuration[/bold green]", border_style="green", expand=False))

    with console.status("[cyan]Discovering tests...", spinner="dots"):
        runner, base = Runner.discover(target, timeout)
    if runner is None:
        console.print("[bold red]Could not run pytest on that target.[/bold red] Is it a path with tests, "
                      "and is pytest installed in this environment?")
        raise typer.Exit(2)
    all_tests = base["order"]
    base_failed = engine.failed_ids(base)
    console.print("Discovered [bold blue]%d[/bold blue] tests (%d failed in natural order).\n"
                  % (len(all_tests), len(base_failed)))
    if len(all_tests) < 2:
        console.print("[yellow]Need at least 2 tests to look for order or timing problems.[/yellow]")

    scan_id = db.save_scan(runner.root, runner.target, runs, profile)
    rng = random.Random(rng_seed)
    with Progress(SpinnerColumn(), TextColumn("[cyan]Hunting for flakes..."), BarColumn(),
                  "[progress.percentage]{task.percentage:>3.0f}%", TimeElapsedColumn(), console=console) as progress:
        task = progress.add_task("hunt", total=runs)

        def on_run(i, new, found):
            for n in new:
                progress.console.print("[bold red]Failure seen[/bold red] (run %d): %s" % (i, escape(n)))
            progress.advance(task)

        found, crashed = engine.hunt(runner, all_tests, runs, profile, rng, on_run)
    if crashed:
        console.print("[yellow]%d run(s) crashed or timed out and were ignored.[/yellow]" % crashed)
    if not found:
        console.print(Panel("[bold green]No failures in %d perturbed runs.[/bold green] Stable under this profile; "
                            "try more --runs or another --profile." % runs, border_style="green"))
        return

    if not use_ai and not no_ai:
        console.print("[dim]Model analysis skipped: set BHARATCODE_API_KEY in .env to enable it.[/dim]")
    console.print("\n[bold red on black] AUTOPSY [/bold red on black]")
    summary, broken, flaky = [], [], 0
    for nodeid, finfo in found.items():
        console.print("\n[bold cyan]%s[/bold cyan]" % escape(nodeid))
        with console.status("[yellow]classifying...[/yellow]") as status:
            f = engine.classify(runner, nodeid, finfo, runs,
                                say=lambda m: status.update("[yellow]%s...[/yellow]" % m))
        console.print("Verdict: %s   failed in %d/%d runs   reproduces: %s"
                      % (kind_tag(f.kind), f.fail_runs, f.runs, f.repro_rate))
        if f.kind == engine.BROKEN:
            console.print("[dim]Fails every time even alone: this test is broken, not flaky. Not saved.[/dim]")
            broken.append(f)
            continue
        flaky += 1
        console.print(reproducer_panel(f.min_order, "Minimal reproducer"))
        if f.seed is not None or f.delay:
            console.print("[dim]Replay uses seed=%s delay=%s[/dim]" % (f.seed, f.delay))
        explanation, fix_status = "", "-"
        sources = fixer.gather_sources(runner.root, f.min_order)
        if use_ai:
            with console.status("[blue]Asking the BharatCode model...[/blue]"):
                try:
                    explanation = ai.analyze_failure(f, sources)
                except ai.AIError as e:
                    explanation = "Model analysis unavailable: %s" % e
            console.print(Panel(Markdown(explanation), title="[bold blue]AI failure detective[/bold blue]",
                                border_style="blue"))
        fid = db.save_failure(scan_id, f, explanation)
        if fix and use_ai:
            if f.kind != engine.ORDER:
                console.print("[dim]Verified fixes currently cover order-dependent flakes only.[/dim]")
            else:
                with console.status("[blue]Proposing and verifying a patch...[/blue]") as status:
                    try:
                        res = fixer.propose_and_verify(runner, f, all_tests,
                                                       say=lambda m: status.update("[blue]%s...[/blue]" % m))
                    except ai.AIError as e:
                        res = fixer.FixResult(False, message="model call failed: %s" % e)
                show_fix(res)
                if res.ok:
                    db.update_fix(fid, res.diff, res.edits, True)
                    fix_status = "verified"
                else:
                    fix_status = "failed"
        summary.append((fid, nodeid, f, fix_status))

    if summary:
        t = Table(title="Saved to the regression vault", border_style="cyan")
        for c in ("ID", "Test", "Kind", "Failed", "Reproduces", "Fix"):
            t.add_column(c)
        for fid, nodeid, f, fs in summary:
            t.add_row(str(fid), escape(nodeid.split("/")[-1]), kind_tag(f.kind),
                      "%d/%d" % (f.fail_runs, f.runs), escape(f.repro_rate), fs)
        console.print()
        console.print(t)
        console.print("Replay: [bold]flakehunt replay <ID>[/bold]   Report: [bold]flakehunt report <ID>[/bold]   "
                      "Dashboard: [bold]flakehunt serve[/bold]")
    if broken:
        console.print("\n[dim]%d broken test(s) were skipped (they fail even alone).[/dim]" % len(broken))
    if ci and flaky:
        raise typer.Exit(1)


@app.command("vault-list")
def vault_list():
    """List every flaky test saved in the vault."""
    db.init_db()
    rows = db.list_failures()
    if not rows:
        console.print(Panel("The vault is empty. Run [bold]flakehunt scan[/bold] first.", border_style="yellow"))
        return
    t = Table(title="Regression vault", border_style="cyan")
    for c in ("ID", "Date", "Kind", "Reproduces", "Fix", "Test"):
        t.add_column(c)
    for r in rows:
        t.add_row(str(r["id"]), str(r["timestamp"]), kind_tag(r["kind"] or "?"), r["repro_rate"] or "?",
                  "verified" if r.get("fix_verified") else "-", escape(r["nodeid"]))
    console.print(t)


@app.command()
def report(failure_id: int):
    """Print the full autopsy report for one saved failure."""
    db.init_db()
    r = db.get_failure(failure_id)
    if not r:
        console.print("[bold red]No failure #%d in the vault.[/bold red]" % failure_id)
        raise typer.Exit(1)
    console.print("\n[bold red on black] AUTOPSY REPORT #%d [/bold red on black]" % failure_id)
    console.print("[bold]Test:[/bold] %s\n[bold]Kind:[/bold] %s   [bold]Reproduces:[/bold] %s   [bold]Date:[/bold] %s"
                  % (escape(r["nodeid"]), kind_tag(r["kind"] or "?"), r["repro_rate"], r["timestamp"]))
    console.print(reproducer_panel(json.loads(r["min_order"]), "Minimal reproducer"))
    if r["seed"] is not None or r["delay"]:
        console.print("[dim]Replay uses seed=%s delay=%s[/dim]" % (r["seed"], r["delay"]))
    console.print(Panel(escape(r["traceback"] or ""), title="[bold red]Traceback[/bold red]", border_style="red"))
    if r["ai_explanation"]:
        console.print(Panel(Markdown(r["ai_explanation"]), title="[bold blue]AI failure detective[/bold blue]",
                            border_style="blue"))
    if r.get("fix_diff"):
        console.print(Panel(Syntax(r["fix_diff"], "diff", theme="ansi_dark"), border_style="green",
                            title="[bold green]Verified fix[/bold green]"))


@app.command()
def replay(failure_id: int, runs: int = typer.Option(3, help="How many times to replay"),
           timeout: int = typer.Option(120)):
    """Re-run the saved minimal reproducer (with its saved seed and delay)."""
    db.init_db()
    banner()
    r = db.get_failure(failure_id)
    if not r:
        console.print("[bold red]No failure #%d in the vault.[/bold red]" % failure_id)
        raise typer.Exit(1)
    if not r["root"] or not os.path.isdir(r["root"]):
        console.print("[bold red]The project folder is gone:[/bold red] %s" % escape(str(r["root"])))
        raise typer.Exit(1)
    runner = Runner(r["target"], r["root"], timeout)
    order = json.loads(r["min_order"])
    console.print("Replaying [bold]%s[/bold] (%s, seed=%s, delay=%s) x%d\n"
                  % (escape(r["nodeid"]), r["kind"], r["seed"], r["delay"], runs))
    hits, first_tb = 0, ""
    with console.status("[yellow]Executing the minimal reproducer...[/yellow]"):
        for _ in range(runs):
            data = runner.run(order=order, seed=r["seed"], delay=r["delay"] or 0.0)
            if r["nodeid"] in engine.failed_ids(data):
                hits += 1
                first_tb = first_tb or engine.traceback_of(data, r["nodeid"])
    if hits:
        console.print(Panel(escape(first_tb), border_style="green",
                            title="[bold green]Reproduced %d/%d[/bold green]" % (hits, runs)))
    else:
        console.print(Panel("Reproduced 0/%d. The flake may be fixed now, or it needs a different seed/timing."
                            % runs, border_style="yellow"))


@app.command()
def fix(failure_id: int, apply: bool = typer.Option(False, "--apply", help="Write the verified patch to your files"),
        timeout: int = typer.Option(120)):
    """Ask the model for a patch and verify it on a copy (or apply a verified one with --apply)."""
    db.init_db()
    r = db.get_failure(failure_id)
    if not r:
        console.print("[bold red]No failure #%d in the vault.[/bold red]" % failure_id)
        raise typer.Exit(1)
    if not r["root"] or not os.path.isdir(r["root"]):
        console.print("[bold red]The project folder is gone:[/bold red] %s" % escape(str(r["root"])))
        raise typer.Exit(1)
    if apply:
        if not r.get("fix_verified") or not r.get("fix_edits"):
            console.print("No verified fix saved for #%d. Run [bold]flakehunt fix %d[/bold] first." % (failure_id, failure_id))
            raise typer.Exit(1)
        try:
            changes = fixer.apply_edits(r["root"], json.loads(r["fix_edits"]))
            fixer.check_guards(changes)
        except fixer.EditError as e:
            console.print("[bold red]Could not apply the patch:[/bold red] %s (files changed since it was made?)" % e)
            raise typer.Exit(1)
        fixer.write_changes(r["root"], changes)
        console.print("[bold green]Patch applied.[/bold green] Check it with [bold]flakehunt replay %d[/bold] "
                      "(it should now report 0 reproductions)." % failure_id)
        return
    if not ai.enabled():
        console.print("Set BHARATCODE_API_KEY in .env first (see `flakehunt doctor`).")
        raise typer.Exit(1)
    f = finding_from_row(r)
    if f.kind != engine.ORDER:
        console.print("[yellow]Verified fixes currently cover order-dependent flakes only.[/yellow]")
        raise typer.Exit(1)
    runner = Runner(r["target"], r["root"], timeout)
    base = runner.run()
    if base is None:
        console.print("[bold red]pytest could not run the project.[/bold red]")
        raise typer.Exit(2)
    with console.status("[blue]Proposing and verifying a patch...[/blue]") as status:
        try:
            res = fixer.propose_and_verify(runner, f, base["order"],
                                           say=lambda m: status.update("[blue]%s...[/blue]" % m))
        except ai.AIError as e:
            res = fixer.FixResult(False, message="model call failed: %s" % e)
    show_fix(res)
    if res.ok:
        db.update_fix(failure_id, res.diff, res.edits, True)
        console.print("Apply it with [bold]flakehunt fix %d --apply[/bold]" % failure_id)


@app.command()
def serve(port: int = typer.Option(8000), host: str = typer.Option("127.0.0.1")):
    """Open the local vault dashboard."""
    import uvicorn
    banner()
    console.print("Dashboard: [bold cyan]http://%s:%d[/bold cyan]  (Ctrl+C to stop)" % (host, port))
    uvicorn.run("flakehunt.dashboard:app", host=host, port=port, log_level="warning")


@app.command()
def demo(tests: int = typer.Option(100, help="Total tests in the demo project"),
         directory: str = typer.Option("demo_big", help="Where to create it")):
    """Create a demo project with a hidden order-dependent flake."""
    from flakehunt.demo import generate
    info = generate(directory, tests)
    console.print("Created [bold]%s[/bold]: %d tests in %d files. Try:\n"
                  "  [bold]flakehunt scan %s --runs 12 --profile combined --fix[/bold]"
                  % (info["directory"], info["tests"], info["files"], info["directory"]))


@app.command()
def bench(directory: str = typer.Option("benchmark", help="Folder with manifest.json"),
          runs: int = typer.Option(8, help="Perturbed runs per suite and profile")):
    """Measure detection on known flaky, stable and broken suites."""
    from flakehunt.bench import run_bench, summarize
    if not os.path.exists(os.path.join(directory, "manifest.json")):
        console.print("[bold red]No manifest.json in %s[/bold red] (run this from the repo root)." % escape(directory))
        raise typer.Exit(1)
    banner()
    with console.status("[cyan]Running the benchmark...[/cyan]") as status:
        rows = run_bench(directory, runs, say=lambda m: status.update("[cyan]%s[/cyan]" % m))
    t = Table(title="Detection benchmark (%d runs per cell)" % runs, border_style="cyan")
    for c in ("Suite", "Profile", "Result", "Detail"):
        t.add_column(c)
    for r in rows:
        style = {"PASS": "green", "MISS": "red", "FALSE-POSITIVE": "red", "WRONG-KIND": "yellow"}.get(r["status"], "dim")
        t.add_row(r["suite"], r["profile"], "[%s]%s[/%s]" % (style, r["status"], style), escape(r["detail"]))
    console.print(t)
    s = summarize(rows)
    console.print("Expected detections found: [bold]%d/%d[/bold]   False positives: [bold]%d[/bold]"
                  % (s["passed"], s["expected_cells"], s["false_positives"]))


@app.command()
def doctor():
    """Check your setup, including the BharatCode API connection."""
    import pytest
    import requests
    console.print("Python %s, pytest %s" % (sys.version.split()[0], pytest.__version__))
    console.print("API base URL: %s   model: %s" % (ai.base_url(), ai.model()))
    if not ai.enabled():
        console.print("[yellow]BHARATCODE_API_KEY is not set.[/yellow] Put it in .env (see .env.example).")
        raise typer.Exit(1)
    key = os.environ["BHARATCODE_API_KEY"]
    try:
        r = requests.get(ai.base_url() + "/models", headers={"Authorization": "Bearer " + key}, timeout=30)
        console.print("GET /models -> HTTP %d" % r.status_code)
        if r.status_code == 200:
            ids = [m.get("id") for m in r.json().get("data", [])]
            console.print("Model listed: %s" % ("yes" if ai.model() in ids else "NO (set FLAKEHUNT_MODEL to one of: %s)" % ids[:8]))
    except Exception as e:
        console.print("[red]/models check failed:[/red] %s" % e)
    try:
        console.print("Chat test -> %r" % ai.chat("Reply with the single word OK.", temperature=0)[:40])
        console.print("[bold green]BharatCode API works.[/bold green]")
    except ai.AIError as e:
        console.print("[bold red]Chat test failed:[/bold red] %s" % e)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
