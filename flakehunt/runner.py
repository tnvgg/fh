"""Runs pytest in a child process under a given order / seed / delay."""
import json
import os
import subprocess
import sys
import tempfile


class Runner:
    def __init__(self, target, root=None, timeout=120):
        self.target = os.path.abspath(target)
        self.root = root
        self.timeout = timeout

    @classmethod
    def discover(cls, target, timeout=120):
        """Run the suite once in natural order and learn its rootdir and test order."""
        r = cls(target, None, timeout)
        data = r.run()
        if data is None or not data.get("order"):
            return None, data
        r.root = data.get("rootdir") or os.getcwd()
        return r, data

    def clone(self, new_root):
        """Same suite, but inside a copy of the project that lives at new_root."""
        rel = os.path.relpath(self.target, self.root)
        return Runner(os.path.join(new_root, rel), new_root, self.timeout)

    def run(self, order=None, seed=None, delay=0.0):
        fd, out = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        order_file = None
        cmd = [sys.executable, "-m", "pytest", self.target,
               "-p", "flakehunt.plugin", "-p", "no:randomly", "-p", "no:cacheprovider",
               "--flakehunt-out=" + out, "-q", "--tb=short"]
        if self.root:
            cmd.append("--rootdir=" + self.root)
        if order is not None:
            fd2, order_file = tempfile.mkstemp(suffix=".json")
            with os.fdopen(fd2, "w") as f:
                json.dump(list(order), f)
            cmd.append("--flakehunt-order-file=" + order_file)
        if seed is not None:
            cmd.append("--flakehunt-seed=%d" % seed)
        if delay:
            cmd.append("--flakehunt-delay=%s" % delay)
        env = os.environ.copy()
        if seed is not None:
            env["PYTHONHASHSEED"] = str(seed)
        data = None
        try:
            subprocess.run(cmd, env=env, cwd=self.root or None, capture_output=True,
                           text=True, timeout=self.timeout)
            with open(out) as f:
                data = json.load(f)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            data = None
        finally:
            for p in (out, order_file):
                if p and os.path.exists(p):
                    os.remove(p)
        return data
