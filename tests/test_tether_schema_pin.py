"""Real pinned SQLite families and subprocess import-integrity checks."""

import importlib
import json
import os
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

# Match unittest discovery identity: a second package import duplicates its
# verified dependency singleton and must not bypass the loader guard.
fixtures = importlib.import_module("test_feedback_collection")

ROOT = Path(__file__).resolve().parents[1]


class TetherSchemaPinTests(unittest.TestCase):
    def test_all_retained_families_reject_incomplete_schema_without_repair(
        self,
    ) -> None:
        for filename, table in (
            ("admission.sqlite", "mirrors"),
            ("journal.sqlite", "provider_attempts"),
            ("feedback.sqlite", "feedback_heads"),
        ):
            with self.subTest(family=filename):
                case = fixtures.CollectionTests()
                case.setUp()
                try:
                    path = case.root / filename
                    with sqlite3.connect(path) as db:
                        db.execute(f'DROP TABLE "{table}"')
                    before = path.read_bytes()
                    modules = case.dep.modules
                    with self.assertRaises(modules["tether.schema"].SchemaRejected):
                        if filename == "admission.sqlite":
                            case.a.Admission(str(path), None, None)
                        elif filename == "journal.sqlite":
                            case.j.ProviderJournal(
                                str(path), case.admission, None, None
                            )
                        else:
                            case.f.FeedbackStore(
                                str(path), case.journal, None, None, None
                            )
                    self.assertEqual(path.read_bytes(), before)
                    self.assertEqual(
                        case.store.db.execute(
                            "SELECT count(*) FROM objects"
                        ).fetchone()[0],
                        0,
                    )
                finally:
                    case.doCleanups()

    def test_fresh_process_verified_schema_has_no_fallback(self) -> None:
        source = os.environ["RUNSPINDLE_TETHER_SOURCE"]
        script = """
from pathlib import Path
import hashlib, json, sys
from refinery.tether_dependency import TetherDependency
def check(ok):
    if not ok: raise RuntimeError('pin invariant')
p = TetherDependency(Path(sys.argv[1]))
check(p.modules['tether'].__path__ == [])
check('tether.schema' in p.modules)
check(p.modules['tether.admission'].open_store is p.modules['tether.schema'].open_store)
check(p.modules['tether.journal'].open_store is p.modules['tether.schema'].open_store)
check(p.modules['tether.feedback'].open_store is p.modules['tether.schema'].open_store)
print(json.dumps({'modules': len(p.modules), 'schema': hashlib.sha256(p.sources['tether/schema.py']).hexdigest()}))
"""
        result = subprocess.run(
            [sys.executable, "-O", "-c", script, source],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        actual = json.loads(result.stdout)
        pin = json.loads((ROOT / "dependencies/tether-feedback.json").read_text())
        self.assertEqual(actual["modules"], 8)
        self.assertEqual(actual["schema"], pin["files"]["tether/schema.py"])

    def test_schema_hash_or_inventory_change_denies_before_import(self) -> None:
        script = """
from pathlib import Path
import json, sys, tempfile
from unittest.mock import patch
from refinery import tether_dependency as d
original = json.loads(d.PIN.read_text())
for mode in ('changed', 'missing'):
    pin = json.loads(json.dumps(original))
    if mode == 'changed': pin['files']['tether/schema.py'] = '0' * 64
    else: del pin['files']['tether/schema.py']
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'pin.json'
        path.write_text(json.dumps(pin))
        with patch.object(d, 'PIN', path):
            try: d.TetherDependency(Path(sys.argv[1]))
            except ValueError: pass
            else: raise RuntimeError('invalid pin admitted')
        if any(n == 'tether' or n.startswith('tether.') for n in sys.modules):
            raise RuntimeError('unverified import survived')
"""
        subprocess.run(
            [
                sys.executable,
                "-O",
                "-c",
                script,
                os.environ["RUNSPINDLE_TETHER_SOURCE"],
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
