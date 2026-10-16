"""The execution service must import and lock on Windows (no fcntl there). Simulated with a fake msvcrt in a child process;
the real Windows run is the user's."""
import subprocess
import sys
import textwrap
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")

CODE = textwrap.dedent("""
    import sys, types, tempfile, os
    sys.path.insert(0, %r)
    sys.platform = "win32"
    calls = []
    sys.modules["msvcrt"] = types.SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=lambda fd, mode, n: calls.append((mode, n)))
    sys.modules["fcntl"] = None          # `import fcntl` would raise ImportError
    import tradelab.execution.service as svc
    assert svc.fcntl is None
    with open(os.path.join(tempfile.mkdtemp(), "x.lock"), "a+") as fh:
        svc._lock_nb(fh); svc._unlock(fh)
    assert calls == [(2, 1), (0, 1)], calls
    print("OK")
""") % SRC


def test_service_imports_and_locks_without_fcntl():
    r = subprocess.run([sys.executable, "-I", "-c", CODE], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout + r.stderr
