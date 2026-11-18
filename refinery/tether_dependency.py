"""Load only reviewed Tether source snapshots, never bytecode or import fallback."""

import hashlib
import json
import os
import selectors
import stat
import subprocess
import sys
import time
from pathlib import Path
from threading import Lock
from types import ModuleType
from typing import Any

PIN = Path(__file__).resolve().parents[1] / "dependencies/tether-feedback.json"
COMMIT = "1e8d2091c55dfad47fdc64ec2e73fcd946da9921"
ORDER = (
    "__init__",
    "sanitization",
    "schema",
    "admission",
    "testing",
    "journal",
    "native_testing",
    "feedback_schema",
    "feedback",
    "slack_wire",
    "slack_ingress",
    "slack_feedback",
    "source_observation",
)


GIT_TIMEOUT_SECONDS = 5.0
GIT_OUTPUT_LIMIT = 128
SOURCE_FILE_LIMIT = 1024 * 1024


def bounded_file(path: Path, limit: int = SOURCE_FILE_LIMIT) -> bytes:
    """Read at most limit+1 bytes from a regular local file, never a FIFO."""
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Tether source unavailable")
        chunks: list[bytes] = []
        count = 0
        while count <= limit:
            part = os.read(descriptor, min(65536, limit + 1 - count))
            if not part:
                return b"".join(chunks)
            chunks.append(part)
            count += len(part)
        raise ValueError("Tether source unavailable")
    except (OSError, AttributeError):
        raise ValueError("Tether source unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def verify_ae5_historical_pin() -> None:
    """Admit only the preserved prior manifest, never treat old/current as equal."""
    raw = bounded_file(PIN.parent / "tether-feedback-ae5ac216.json")
    if (
        hashlib.sha256(raw).hexdigest()
        != "480116946e7d1aeaf95aeb4f666dc3aeb58dc12bdbab0fe94287b0e7a5325b86"
    ):
        raise ValueError("historical Tether manifest changed")


def bounded_head(root: Path) -> str:
    """Bound owned Git wait/output; not a descendant-cleanup or hostile-OS claim."""
    child: subprocess.Popen[bytes] | None = None
    try:
        child = subprocess.Popen(
            [
                "git",
                "--no-optional-locks",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(root),
                "rev-parse",
                "HEAD",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if child.stdout is None:
            raise ValueError("Tether source unavailable")
        expires = time.monotonic() + GIT_TIMEOUT_SECONDS
        output = bytearray()
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while True:
                remaining = expires - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise ValueError("Tether source unavailable")
                chunk = os.read(
                    child.stdout.fileno(), GIT_OUTPUT_LIMIT + 1 - len(output)
                )
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > GIT_OUTPUT_LIMIT:
                    raise ValueError("Tether source unavailable")
        if child.wait(timeout=max(0, expires - time.monotonic())) != 0:
            raise ValueError("Tether source unavailable")
        return bytes(output).decode("ascii").strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        raise ValueError("Tether source unavailable") from None
    finally:
        if child is not None:
            if child.stdout is not None:
                child.stdout.close()
            if child.poll() is None:
                try:
                    child.kill()
                except OSError:
                    pass  # Concurrent leader exit does not expose raw diagnostics.
            try:
                child.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass  # No universal termination or process-group cleanup assertion.


class TetherDependency:
    def __init__(self, root: Path):
        self.root = root.absolute()
        self._verification_stopped = False
        self._verification_lock = Lock()
        self.pin: dict[str, Any] = json.loads(bounded_file(PIN, 65536))
        if (
            self.pin["repository"] != "newdarwindev/runspindle-tether"
            or self.pin["commit"] != COMMIT
            or set(self.pin["files"]) != {f"tether/{name}.py" for name in ORDER}
        ):
            raise ValueError("Tether pin mismatch")
        self.sources = self.verify()
        self.modules: dict[str, ModuleType] = {}
        existing = {k for k in sys.modules if k == "tether" or k.startswith("tether.")}
        if existing:
            raise ValueError(
                "Tether already imported; use one verified dependency instance"
            )
        try:
            for short in ORDER:
                name = "tether" if short == "__init__" else "tether." + short
                module = ModuleType(name)
                module.__file__ = str(self.root / f"tether/{short}.py")
                module.__package__ = "tether"
                if short == "__init__":
                    module.__path__ = []  # No loader fallback outside verified snapshots.
                sys.modules[name] = module
                self.modules[name] = module
                exec(
                    compile(
                        self.sources[f"tether/{short}.py"], module.__file__, "exec"
                    ),
                    module.__dict__,
                )
        except BaseException:
            for name in self.modules:
                sys.modules.pop(name, None)
            raise

    def verify(self) -> dict[str, bytes]:
        if not self._verification_lock.acquire(blocking=False):
            raise ValueError("Tether verification already pending")
        try:
            return self._verify_locked()
        finally:
            self._verification_lock.release()

    def _verify_locked(self) -> dict[str, bytes]:
        if (
            not self.root.is_dir()
            or self.root.is_symlink()
            or (self.root / "tether").is_symlink()
        ):
            raise ValueError("Tether source unavailable")
        if self._verification_stopped:
            raise ValueError("Tether verification previously stopped")
        try:
            head = bounded_head(self.root)
        except BaseException:
            self._verification_stopped = True
            raise
        if head != COMMIT:
            raise ValueError("Tether source revision changed")
        sources = {}
        for name, sha in self.pin["files"].items():
            path = self.root / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("Tether source unavailable")
            raw = bounded_file(path)
            if hashlib.sha256(raw).hexdigest() != sha:
                raise ValueError("Tether source changed")
            sources[name] = raw
        return sources

    def check(self, store: Any) -> None:
        if self.verify() != self.sources:
            raise ValueError("Tether source changed")
        if any(
            sys.modules.get(name) is not module for name, module in self.modules.items()
        ):
            raise ValueError("Tether imported identity changed")
        if type(store) is not self.modules["tether.feedback"].FeedbackStore:
            raise ValueError("unverified Tether store")
