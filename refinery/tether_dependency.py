"""Load only reviewed Tether source snapshots, never bytecode or import fallback."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

PIN = Path(__file__).resolve().parents[1] / "dependencies/tether-feedback.json"
COMMIT = "ae5ac216b45ca413283e4200c0a4a61054998b58"
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


class TetherDependency:
    def __init__(self, root: Path):
        self.root = root.absolute()
        self.pin: dict[str, Any] = json.loads(PIN.read_text())
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
        if (
            not self.root.is_dir()
            or self.root.is_symlink()
            or (self.root / "tether").is_symlink()
        ):
            raise ValueError("Tether source unavailable")
        try:
            head = subprocess.check_output(
                [
                    "git",
                    "--no-optional-locks",
                    "-c",
                    "core.fsmonitor=false",
                    "-C",
                    str(self.root),
                    "rev-parse",
                    "HEAD",
                ],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            raise ValueError("Tether source unavailable") from None
        if head != COMMIT:
            raise ValueError("Tether source revision changed")
        sources = {}
        for name, sha in self.pin["files"].items():
            path = self.root / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("Tether source unavailable")
            raw = path.read_bytes()
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
