"""Ask the model for a patch, then PROVE it works by re-running the reproducer on a copy."""
import difflib
import os
import random
import re
import shutil
import tempfile
from dataclasses import dataclass, field

from flakehunt import ai
from flakehunt.engine import count_fails, failed_ids

BLOCK = re.compile(
    r"<<<<<<< FILE (?P<path>[^\n]+)\n<<<<<<< SEARCH\n(?P<search>.*?)\n?=======\n(?P<repl>.*?)\n?>>>>>>> END",
    re.S)
IGNORE = shutil.ignore_patterns(".git", ".venv", "venv", "env", "node_modules", "__pycache__",
                                ".pytest_cache", "*.sqlite", "flakehunt_fixes", ".mypy_cache",
                                "*.egg-info", "dist", "build")
MAX_COPY_BYTES = 300 * 1024 * 1024


class EditError(Exception):
    pass


@dataclass
class FixResult:
    ok: bool
    edits: list = field(default_factory=list)
    diff: str = ""
    message: str = ""
    attempts: int = 0


def parse_edits(text):
    edits = []
    for m in BLOCK.finditer(text or ""):
        edits.append({"path": m.group("path").strip().strip("`"),
                      "search": m.group("search"), "replace": m.group("repl")})
    return edits


def _resolve(root, path):
    path = path.replace("\\", "/")
    root_real = os.path.realpath(root)
    full = os.path.realpath(os.path.join(root, path))
    if os.path.isabs(path) or not (full == root_real or full.startswith(root_real + os.sep)):
        raise EditError("path outside the project: %s" % path)
    return full


def apply_edits(root, edits):
    """Return {path: [original_or_None, new_text]} without touching the disk."""
    changes = {}
    for e in edits:
        path = e["path"]
        full = _resolve(root, path)
        if path not in changes:
            orig = None
            if os.path.exists(full):
                with open(full, encoding="utf-8") as f:
                    orig = f.read()
            changes[path] = [orig, orig or ""]
        cur = changes[path][1]
        search, repl = e["search"], e["replace"]
        if search == "":
            sep = "" if (cur == "" or cur.endswith("\n")) else "\n"
            new = cur + sep + repl + ("" if repl.endswith("\n") else "\n")
        else:
            n = cur.count(search)
            if n != 1:
                raise EditError("SEARCH text must match exactly once in %s (found %d)" % (path, n))
            new = cur.replace(search, repl, 1)
        changes[path][1] = new
    return changes


def check_guards(changes):
    """Refuse patches that weaken the tests."""
    for path, (old, new) in changes.items():
        base = os.path.basename(path)
        if old is not None and (base.startswith("test_") or base.endswith("_test.py")):
            if len(re.findall(r"\bassert\b", new)) < len(re.findall(r"\bassert\b", old)):
                raise EditError("patch removes an assert from %s" % path)


def write_changes(root, changes):
    for path, (_, new) in changes.items():
        full = _resolve(root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(new)


def make_diff(changes):
    out = []
    for path, (old, new) in sorted(changes.items()):
        out.extend(difflib.unified_diff((old or "").splitlines(True), new.splitlines(True),
                                        fromfile="a/" + path, tofile="b/" + path))
    return "".join(out)


def _tree_size(root):
    total = 0
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in (".git", ".venv", "venv", "node_modules")]
        for f in fn:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


def copy_project(root, dst):
    if _tree_size(root) > MAX_COPY_BYTES:
        raise EditError("project is too large to copy for verification")
    shutil.copytree(root, dst, ignore=IGNORE, dirs_exist_ok=True)


def gather_sources(root, nodeids, limit=16000):
    paths = []
    for n in nodeids:
        p = n.split("::")[0]
        if p not in paths:
            paths.append(p)
        d = os.path.dirname(p)
        while True:
            c = os.path.join(d, "conftest.py") if d else "conftest.py"
            if os.path.exists(os.path.join(root, c)) and c not in paths:
                paths.append(c)
            if not d:
                break
            d = os.path.dirname(d)
        folder = os.path.join(root, os.path.dirname(p))
        if os.path.isdir(folder):
            extra = [f for f in sorted(os.listdir(folder)) if f.endswith(".py")
                     and not f.startswith("test_") and not f.endswith("_test.py") and f != "conftest.py"]
            for f in extra[:3]:
                rel = os.path.join(os.path.dirname(p), f)
                if rel not in paths:
                    paths.append(rel)
    out, used = [], 0
    for p in paths:
        try:
            with open(os.path.join(root, p), encoding="utf-8") as f:
                text = f.read()[:6000]
        except (OSError, UnicodeDecodeError):
            continue
        if used + len(text) > limit:
            break
        used += len(text)
        out.append((p, text))
    return out


def verify(clone, f, all_tests):
    nid = f.nodeid
    k = count_fails(clone, nid, f.min_order, f.seed, f.delay, 3)
    if k:
        return False, "the reproducer still fails %d/3 times" % k
    rng = random.Random(7)
    for i in range(3):
        order = list(all_tests)
        rng.shuffle(order)
        data = clone.run(order=order, seed=f.seed, delay=f.delay)
        if data is None:
            return False, "pytest crashed in shuffled run %d" % (i + 1)
        if nid in failed_ids(data):
            return False, "the test still fails in shuffled run %d" % (i + 1)
    return True, "ok"


def propose_and_verify(runner, f, all_tests, attempts=2, say=None):
    say = say or (lambda m: None)
    sources = gather_sources(runner.root, f.min_order)
    err = None
    last_edits = []
    for attempt in range(1, attempts + 1):
        say("asking the model for a patch (attempt %d)" % attempt)
        edits = parse_edits(ai.propose_fix(f, sources, err))
        if not edits:
            err = "no valid edit blocks found; use the exact block format"
            continue
        last_edits = edits
        tmp = tempfile.mkdtemp(prefix="flakehunt_fix_")
        try:
            copy_project(runner.root, tmp)
            try:
                changes = apply_edits(tmp, edits)
                check_guards(changes)
            except EditError as e:
                err = str(e)
                continue
            write_changes(tmp, changes)
            say("verifying the patch on a copy of your project")
            ok, detail = verify(runner.clone(tmp), f, all_tests)
            if ok:
                return FixResult(True, edits, make_diff(changes), "verified", attempt)
            err = "the patch applied but verification failed: " + detail
        except EditError as e:
            err = str(e)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return FixResult(False, last_edits, "", err or "no patch produced", attempts)
