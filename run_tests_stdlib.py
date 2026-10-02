"""Stdlib-only runner for the pytest-style suite (no pytest installed).

Emulates tmp_path / monkeypatch / capsys and pytest.raises. NOT part of the
shipped package; CI uses real pytest.
"""

import inspect
import io
import os
import sys
import tempfile
import traceback
from contextlib import contextmanager
from pathlib import Path

PASS, FAIL = [], []


class Raises:
    def __init__(self, exc):
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, t, v, tb):
        if t is None:
            raise AssertionError(f"DID NOT RAISE {self.exc}")
        return issubclass(t, self.exc)


class FakePytest:
    @staticmethod
    @contextmanager
    def raises(exc):
        try:
            yield
        except exc:
            return
        raise AssertionError(f"DID NOT RAISE {exc}")


sys.modules["pytest"] = FakePytest()  # type: ignore[assignment]


class Monkeypatch:
    def __init__(self):
        self._saved = {}

    def setenv(self, k, v):
        if k not in self._saved:
            self._saved[k] = os.environ.get(k, None)
        os.environ[k] = v

    def undo(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class Capsys:
    def __init__(self):
        self._out, self._err = io.StringIO(), io.StringIO()
        self._old = (sys.stdout, sys.stderr)

    def __enter__(self):
        sys.stdout, sys.stderr = self._out, self._err
        return self

    def __exit__(self, *a):
        sys.stdout, sys.stderr = self._old

    def readouterr(self):
        out, err = self._out.getvalue(), self._err.getvalue()
        self._out.seek(0)
        self._out.truncate()
        self._err.seek(0)
        self._err.truncate()
        return type("C", (), {"out": out, "err": err})()


def run_module(modname):
    mod = __import__(modname, fromlist=["*"])
    for name in dir(mod):
        if not name.startswith("test_"):
            continue
        fn = getattr(mod, name)
        if not callable(fn):
            continue
        params = inspect.signature(fn).parameters
        kwargs = {}
        tmp = None
        mp = None
        cap = None
        if "tmp_path" in params:
            tmp = Path(tempfile.mkdtemp())
            kwargs["tmp_path"] = tmp
        if "monkeypatch" in params:
            mp = Monkeypatch()
            kwargs["monkeypatch"] = mp
        if "capsys" in params:
            cap = Capsys()
            kwargs["capsys"] = cap
        try:
            if cap is not None:
                with cap:
                    fn(**kwargs)
            else:
                fn(**kwargs)
            PASS.append(f"{modname}.{name}")
        except Exception:
            FAIL.append(f"{modname}.{name}\n{traceback.format_exc()}")
        finally:
            if mp is not None:
                mp.undo()


if __name__ == "__main__":
    sys.path.insert(0, "src")
    for m in sys.argv[1:]:
        run_module(m)
    print(f"PASSED {len(PASS)} FAILED {len(FAIL)}")
    for f in FAIL:
        print("=" * 60)
        print(f)
    sys.exit(1 if FAIL else 0)
