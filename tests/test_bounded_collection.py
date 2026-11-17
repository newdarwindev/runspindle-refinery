"""Real retained stores and owned subprocess limits; no live providers or installs."""

import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any
from unittest.mock import patch

import test_feedback_collection as fixtures
from refinery import tether_dependency as dependency
from refinery.feedback_collection import SelectedFeedback, collect
from refinery.lineage import digest


class CancellationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.CollectionTests(
            "test_exact_batch_replay_and_atomic_provenance"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.cancel = Event()

    def empty_in_fresh_process(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                "import sqlite3,sys,json,socket; socket.socket=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('network denied')); "
                "d=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True); "
                "print(json.dumps([d.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('objects','families','attempts','experiments')])); d.close()",
                str(self.fixture.root / "refinery.sqlite"),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        self.assertEqual(json.loads(result.stdout), [0, 0, 0, 0])
        self.assertEqual(len(self.fixture.provider.requests), self.fixture.calls_before)

    def test_precancelled_does_not_verify_or_resolve(self) -> None:
        self.cancel.set()
        with patch.object(self.fixture.dep, "check") as verify:
            with self.assertRaisesRegex(ValueError, "collection cancelled"):
                self.fixture.collect(cancel=self.cancel)
            verify.assert_not_called()
        self.assertEqual(self.fixture.approvals, [])
        self.empty_in_fresh_process()

    def test_cancellation_after_actual_dependency_verification_prevents_collection(
        self,
    ) -> None:
        original = self.fixture.dep.check

        def verify(store: Any) -> None:
            original(store)
            self.cancel.set()

        with patch.object(self.fixture.dep, "check", side_effect=verify):
            with self.assertRaisesRegex(ValueError, "collection cancelled"):
                self.fixture.collect(cancel=self.cancel)
        self.empty_in_fresh_process()

    def test_cancellation_during_material_or_consent_callback_prevents_persistence(
        self,
    ) -> None:
        def resolve(_: Any) -> Any:
            self.cancel.set()
            return self.fixture.material

        with self.assertRaisesRegex(ValueError, "collection cancelled"):
            self.fixture.collect(cancel=self.cancel, resolve_material=resolve)
        self.empty_in_fresh_process()
        self.cancel.clear()

        def approve(_: str, __: str) -> bool:
            self.cancel.set()
            return True

        with self.assertRaisesRegex(ValueError, "collection cancelled"):
            self.fixture.collect(cancel=self.cancel, approve_training=approve)
        self.empty_in_fresh_process()

    def test_second_selected_record_cancellation_leaves_no_partial_batch(self) -> None:
        raw = self.fixture.event(
            feedback_id="feedback-two", event_id="feedback-two-event"
        )
        self.fixture.feedback.ingest(
            raw, digest(raw), self.fixture.delivery, self.fixture.payload
        )
        second = SelectedFeedback("synthetic", "feedback-two", 1, digest(raw), "gold")
        actual_collect = collect
        calls: list[object] = []

        def resolve(target: Any) -> Any:
            calls.append(target)
            if len(calls) == 2:
                self.cancel.set()
            return self.fixture.material

        def batch(
            store: Any,
            dep: Any,
            feedback: Any,
            selected: Any,
            rules: Any,
            **options: Any,
        ) -> str:
            return actual_collect(
                store, dep, feedback, (*selected, second), rules, **options
            )

        with patch.object(fixtures, "collect", side_effect=batch):
            with self.assertRaisesRegex(ValueError, "collection cancelled"):
                self.fixture.collect(cancel=self.cancel, resolve_material=resolve)
        self.assertEqual(len(calls), 2)
        self.empty_in_fresh_process()

    def test_cancellation_after_receipt_insert_rolls_back_objects_and_families(
        self,
    ) -> None:
        original = self.fixture.store.put
        observed = []

        def put(tx: Any, kind: str, identity: str, value: Any) -> Any:
            result = original(tx, kind, identity, value)
            if kind == "feedback_collection":
                observed.append(
                    tx.execute("SELECT count(*) FROM objects").fetchone()[0]
                )
                self.cancel.set()
            return result

        with patch.object(self.fixture.store, "put", side_effect=put):
            with self.assertRaisesRegex(ValueError, "collection cancelled"):
                self.fixture.collect(cancel=self.cancel)
        self.assertTrue(observed and observed[0] > 0)
        self.empty_in_fresh_process()
        self.cancel.clear()
        snapshot = self.fixture.collect(cancel=self.cancel)
        self.assertEqual(len(self.fixture.store.lineage(snapshot)[0].examples), 1)

    def test_cancellation_after_commit_does_not_claim_rollback(self) -> None:
        transaction = self.fixture.store.transaction

        @contextlib.contextmanager
        def finish() -> Any:
            with transaction() as tx:
                yield tx
            self.cancel.set()

        with patch.object(self.fixture.store, "transaction", side_effect=finish):
            snapshot = self.fixture.collect(cancel=self.cancel)
        self.assertTrue(self.cancel.is_set())
        self.assertEqual(len(self.fixture.store.lineage(snapshot)[0].examples), 1)

    def test_foreign_cancellation_object_rejects_without_invocation(self) -> None:
        class Forged:
            def is_set(self) -> bool:
                raise AssertionError("must not call foreign cancellation object")

        with self.assertRaisesRegex(ValueError, "invalid collection cancellation"):
            self.fixture.collect(cancel=Forged())
        self.empty_in_fresh_process()

    def test_removed_last_cancellation_guard_exposes_actual_persistence(self) -> None:
        # Independently mutate only the post-insert guard, keeping all consent gates.
        import refinery.feedback_collection as collection

        text = Path(collection.__file__).read_text()
        needle = '            db.put(db.db, "feedback_collection", receipt_sha, receipt)\n            check_cancel()'
        self.assertEqual(text.count(needle), 1)
        code = text.replace(
            needle,
            '            db.put(db.db, "feedback_collection", receipt_sha, receipt)',
        )
        namespace = dict(collection.__dict__)
        exec(compile(code, str(collection.__file__), "exec"), namespace)
        original = self.fixture.store.put

        def put(tx: Any, kind: str, identity: str, value: Any) -> Any:
            result = original(tx, kind, identity, value)
            if kind == "feedback_collection":
                self.cancel.set()
            return result

        # Reuse existing dataclasses so exact type checks test the intended guard.
        namespace["SelectedFeedback"] = collection.SelectedFeedback
        namespace["Material"] = collection.Material
        with (
            patch.object(self.fixture.store, "put", side_effect=put),
            patch.object(fixtures, "collect", namespace["collect"]),
        ):
            snapshot = self.fixture.collect(cancel=self.cancel)
        self.assertTrue(self.cancel.is_set())
        self.assertGreater(
            self.fixture.store.db.execute("SELECT count(*) FROM objects").fetchone()[0],
            0,
        )
        self.assertEqual(len(self.fixture.store.lineage(snapshot)[0].examples), 1)


class DependencyBoundsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        deny = patch.object(
            socket, "socket", side_effect=AssertionError("network forbidden")
        )
        spy = deny.start()
        self.addCleanup(deny.stop)
        self.addCleanup(spy.assert_not_called)

    def run_git(self, script: str) -> str:
        # An explicit test launcher replaces only Git executable, not product code.
        original = subprocess.Popen

        def launch(args: Any, **kwargs: Any) -> Any:
            self.assertEqual(args[0], "git")
            return original([sys.executable, "-I", "-B", "-c", script], **kwargs)

        with patch.object(subprocess, "Popen", side_effect=launch):
            return dependency.bounded_head(self.root)

    def test_actual_hung_git_leader_rejects_in_bounded_time(self) -> None:
        start = time.monotonic()
        with patch.object(dependency, "GIT_TIMEOUT_SECONDS", 0.1):
            with self.assertRaisesRegex(ValueError, "Tether source unavailable"):
                self.run_git("import time; time.sleep(30)")
        self.assertLess(time.monotonic() - start, 2)

    def test_large_output_invalid_utf8_and_nonzero_git_reject(self) -> None:
        for script in [
            "import os; os.write(1,b'x'*100000)",
            "import os; os.write(1,b'\\xff')",
            "raise SystemExit(2)",
        ]:
            with self.subTest(script=script):
                with self.assertRaisesRegex(ValueError, "Tether source unavailable"):
                    self.run_git(script)

    def test_inherited_pipe_does_not_extend_wait(self) -> None:
        start = time.monotonic()
        with patch.object(dependency, "GIT_TIMEOUT_SECONDS", 0.1):
            with self.assertRaisesRegex(ValueError, "Tether source unavailable"):
                self.run_git(
                    "import os,time; pid=os.fork(); time.sleep(0.5) if pid==0 else None"
                )
        self.assertLess(time.monotonic() - start, 0.45)
        # Disposable test descendant self-exits; this is not a cleanup claim.

    def test_regular_file_limit_symlink_and_fifo_reject(self) -> None:
        file = self.root / "module.py"
        file.write_bytes(b"a" * 17)
        with self.assertRaises(ValueError):
            dependency.bounded_file(file, 16)
        file.write_bytes(b"a" * 16)
        self.assertEqual(dependency.bounded_file(file, 16), b"a" * 16)
        link = self.root / "link"
        link.symlink_to(file)
        with self.assertRaises(ValueError):
            dependency.bounded_file(link)
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        start = time.monotonic()
        with self.assertRaises(ValueError):
            dependency.bounded_file(fifo)
        self.assertLess(time.monotonic() - start, 1)

    def test_failed_git_fences_dependency_instance_without_retry(self) -> None:
        dep = object.__new__(dependency.TetherDependency)
        dep.root = self.root
        dep._verification_stopped = False
        dep._verification_lock = Lock()
        with patch.object(
            dependency, "bounded_head", side_effect=ValueError("stopped")
        ) as head:
            with self.assertRaises(ValueError):
                dep.verify()
            with self.assertRaisesRegex(ValueError, "previously stopped"):
                dep.verify()
            self.assertEqual(head.call_count, 1)

    def test_interrupted_git_verification_also_fences_reuse(self) -> None:
        dep = object.__new__(dependency.TetherDependency)
        dep.root = self.root
        dep._verification_stopped = False
        dep._verification_lock = Lock()
        with patch.object(
            dependency, "bounded_head", side_effect=KeyboardInterrupt
        ) as head:
            with self.assertRaises(KeyboardInterrupt):
                dep.verify()
            with self.assertRaisesRegex(ValueError, "previously stopped"):
                dep.verify()
            self.assertEqual(head.call_count, 1)

    def test_overlapping_verification_owns_one_child_and_preserves_success_or_failure(
        self,
    ) -> None:
        source = fixtures.dependency()
        for succeeds in (True, False):
            with self.subTest(succeeds=succeeds):
                dep = object.__new__(dependency.TetherDependency)
                dep.root = source.root
                dep.pin = source.pin
                dep._verification_stopped = False
                dep._verification_lock = Lock()
                entered = Event()
                release = self.root / (
                    "release-success" if succeeds else "release-failure"
                )
                children: list[subprocess.Popen[bytes]] = []
                peaks: list[int] = []
                results: list[object] = []
                original = subprocess.Popen
                script = (
                    "import pathlib,time,sys; "
                    f"release=pathlib.Path({str(release)!r}); deadline=time.monotonic()+1; "
                    "\nwhile not release.exists() and time.monotonic()<deadline: time.sleep(0.005)\n"
                    f"print({dependency.COMMIT!r}); sys.exit({0 if succeeds else 2})"
                )

                def launch(args: Any, **kwargs: Any) -> Any:
                    self.assertEqual(args[0], "git")
                    child = original(
                        [sys.executable, "-I", "-B", "-c", script], **kwargs
                    )
                    children.append(child)
                    peaks.append(sum(p.poll() is None for p in children))
                    entered.set()
                    return child

                def work() -> None:
                    try:
                        results.append(dep.verify())
                    except BaseException as error:
                        results.append(error)

                with (
                    patch.object(subprocess, "Popen", side_effect=launch),
                    patch.object(dependency, "GIT_TIMEOUT_SECONDS", 1),
                ):
                    worker = Thread(target=work)
                    worker.start()
                    try:
                        self.assertTrue(entered.wait(2))
                        with self.assertRaisesRegex(ValueError, "already pending"):
                            dep.verify()
                        self.assertEqual(len(children), 1)
                    finally:
                        release.touch()
                        worker.join(3)
                        for child in children:
                            if child.poll() is None:
                                child.kill()
                            child.wait(timeout=2)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(len(results), 1)
                    if succeeds:
                        self.assertEqual(results[0], source.sources)
                        self.assertEqual(dep.verify(), source.sources)
                        self.assertEqual(len(children), 2)
                    else:
                        self.assertIsInstance(results[0], ValueError)
                        with self.assertRaisesRegex(ValueError, "previously stopped"):
                            dep.verify()
                        self.assertEqual(len(children), 1)
                    self.assertEqual(max(peaks), 1)
