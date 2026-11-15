"""Synthetic source-only checks. No wheels, uv or DSPy execute here."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tools import dspy_preparation_candidate as prep
from tools import offline_dspy


class PreparationCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "deps"
        (self.base / "example.dist-info").mkdir(parents=True)
        (self.base / "bin").mkdir()
        self.script = self.base / "bin/example"
        self.script.write_text("#!" + prep.INTERPRETER + "\nprint('synthetic')\n")
        self.record = self.base / "example.dist-info/RECORD"
        self.write_record()

    def write_record(self) -> None:
        data = self.script.read_bytes()
        digest = (
            base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
        )
        with self.record.open("w", newline="") as stream:
            csv.writer(stream).writerows(
                [
                    ["bin/example", "sha256=" + digest, str(len(data))],
                    ["example.dist-info/RECORD", "", ""],
                ]
            )

    def test_records_and_scripts(self) -> None:
        self.assertEqual(
            prep.check_records(self.base), {"example": prep.sha(self.script)}
        )

    def test_script_prefix_not_normalized(self) -> None:
        self.script.write_text("#!/host/python3.14\n")
        self.write_record()
        with self.assertRaisesRegex(ValueError, "interpreter"):
            prep.check_records(self.base)
        self.assertEqual(self.script.read_text(), "#!/host/python3.14\n")

    def test_tampered_record_bytes(self) -> None:
        self.script.write_text("tampered")
        with self.assertRaisesRegex(ValueError, "hash or size"):
            prep.check_records(self.base)

    def test_unrecorded_file(self) -> None:
        (self.base / "extra").write_text("unexpected")
        with self.assertRaisesRegex(ValueError, "unrecorded"):
            prep.check_records(self.base)

    def test_escaping_record(self) -> None:
        self.record.write_text("../outside,,\n")
        with self.assertRaisesRegex(ValueError, "escaping"):
            prep.check_records(self.base)

    def test_symlink_rejected(self) -> None:
        (self.base / "link").symlink_to(self.script)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            prep.check_records(self.base)

    def test_bytecode_rejected(self) -> None:
        (self.base / "extra.pyc").write_bytes(b"synthetic")
        with self.assertRaisesRegex(ValueError, "bytecode"):
            prep.check_records(self.base)

    def recipe_fixture(self) -> tuple[dict[str, Any], Path, Path]:
        # Deliberately synthetic hashes, never claimed to be official artifacts.
        (self.root / "offline").mkdir()
        wheels = self.root / "wheels"
        wheels.mkdir()
        contents = {
            "example.dist-info/METADATA": b"Name: example\nVersion: 1.0.0\nLicense: MIT\n",
            "example.dist-info/LICENSE": b"synthetic license",
        }
        rows = []
        for name, data in contents.items():
            value = (
                base64.urlsafe_b64encode(hashlib.sha256(data).digest())
                .decode()
                .rstrip("=")
            )
            rows.append([name, "sha256=" + value, str(len(data))])
        rows.append(["example.dist-info/RECORD", "", ""])
        stream = io.StringIO()
        csv.writer(stream).writerows(rows)
        contents["example.dist-info/RECORD"] = stream.getvalue().encode()
        with zipfile.ZipFile(wheels / "example.whl", "w") as archive:
            for name, data in contents.items():
                archive.writestr(name, data)
        digest = prep.sha(wheels / "example.whl")
        (self.root / "offline/requirements.lock").write_text(
            "example==1.0.0 --hash=sha256:" + digest
        )
        (self.root / "offline/toolchain.json").write_text(
            json.dumps({"version": "Python 3.14.0"})
        )
        artifacts = self.root / "artifacts"
        artifacts.mkdir()
        (artifacts / "uv").write_bytes(b"synthetic uv")
        with tarfile.open(artifacts / "uv.tar.gz", "w:gz") as archive:
            member = tarfile.TarInfo("uv-aarch64-unknown-linux-gnu/uv")
            member.size = len(b"synthetic uv")
            archive.addfile(member, io.BytesIO(b"synthetic uv"))
        (artifacts / "uv.tar.gz.sha256").write_text(
            prep.sha(artifacts / "uv.tar.gz") + "\n"
        )
        recipe = {
            "schema_version": 1,
            "image": offline_dspy.IMAGE,
            "platform": "linux/arm64",
            "python_version": "Python 3.14.0",
            "interpreter": prep.INTERPRETER,
            "argv": prep.ARGV,
            "environment": prep.ENVIRONMENT,
            "cwd": "/work",
            "umask": "022",
            "network": "none",
            "dependency_lock_sha256": prep.sha(self.root / "offline/requirements.lock"),
            "toolchain_sha256": prep.sha(self.root / "offline/toolchain.json"),
            "uv": {
                "version": "0.0.0",
                "url": "https://github.com/astral-sh/uv/releases/download/0.0.0/uv-aarch64-unknown-linux-gnu.tar.gz",
                "archive_sha256": prep.sha(artifacts / "uv.tar.gz"),
                "binary_sha256": prep.sha(artifacts / "uv"),
                "checksum_url": "https://github.com/astral-sh/uv/releases/download/0.0.0/uv-aarch64-unknown-linux-gnu.tar.gz.sha256",
                "checksum_sha256": prep.sha(artifacts / "uv.tar.gz.sha256"),
            },
            "wheels": {"example.whl": digest},
        }
        return recipe, artifacts, wheels

    def test_recipe_exact_bindings(self) -> None:
        recipe, artifacts, wheels = self.recipe_fixture()
        with patch.object(prep, "ROOT", self.root):
            prep.check_recipe(recipe, artifacts, wheels)
            for key in (
                "image",
                "platform",
                "interpreter",
                "argv",
                "environment",
                "dependency_lock_sha256",
            ):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    prep.check_recipe(dict(recipe, **{key: "wrong"}), artifacts, wheels)

    def test_artifact_and_wheel_hashes(self) -> None:
        recipe, artifacts, wheels = self.recipe_fixture()
        with patch.object(prep, "ROOT", self.root):
            recipe["uv"]["binary_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "artifact hash"):
                prep.check_recipe(recipe, artifacts, wheels)
            recipe["uv"]["binary_sha256"] = prep.sha(artifacts / "uv")
            (wheels / "example.whl").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "wheelhouse differs"):
                prep.check_recipe(recipe, artifacts, wheels)

    def test_candidate_is_not_promoted(self) -> None:
        recipe, artifacts, wheels = self.recipe_fixture()
        canonical = self.root / "offline/dependencies.json"
        canonical.write_text('{"old": true}')
        before = canonical.read_bytes()
        with (
            patch.object(prep, "ROOT", self.root),
            patch.object(
                prep,
                "inventory_bytes",
                return_value={
                    "dependencies": [{"name": "example", "version": "1.0.0"}]
                },
            ),
        ):
            result = prep.candidate(self.base, recipe, artifacts, wheels)
        self.assertFalse(result["matches_canonical_manifest"])
        self.assertEqual(result["framework_qualification"], "not_run")
        self.assertEqual(canonical.read_bytes(), before)
        self.assertIn("candidate_manifest", result)
        self.assertNotIn("installed_files", result)

    def test_duplicate_wheel_distribution(self) -> None:
        recipe, artifacts, wheels = self.recipe_fixture()
        (wheels / "duplicate.whl").write_bytes((wheels / "example.whl").read_bytes())
        recipe["wheels"]["duplicate.whl"] = prep.sha(wheels / "duplicate.whl")
        with (
            patch.object(prep, "ROOT", self.root),
            self.assertRaisesRegex(ValueError, "duplicate"),
        ):
            prep.check_recipe(recipe, artifacts, wheels)

    def test_duplicate_json_keys(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate JSON"):
            json.loads('{"image": 1, "image": 2}', object_pairs_hook=prep.unique_object)

    def test_snapshot_survives_input_mutation(self) -> None:
        files = prep.snapshot(self.base)
        self.script.write_text("#!/other/interpreter\n")
        prep.check_record_bytes(files)
        self.assertEqual(
            prep.scripts_from_bytes(files)["example"], prep.digest(files["bin/example"])
        )
        with self.assertRaisesRegex(ValueError, "hash or size"):
            prep.check_records(self.base)

    def test_missing_duplicate_and_self_records(self) -> None:
        original = self.record.read_bytes()
        for suffix in (b"missing,sha256=bad,1\n", b"bin/example,sha256=bad,1\n"):
            self.record.write_bytes(original + suffix)
            with self.assertRaises(ValueError):
                prep.check_records(self.base)
        self.record.write_bytes(original.splitlines(keepends=True)[0])
        with self.assertRaisesRegex(ValueError, "self row"):
            prep.check_records(self.base)

    def test_boolean_schema_not_integer(self) -> None:
        recipe, artifacts, wheels = self.recipe_fixture()
        recipe["schema_version"] = True
        with (
            patch.object(prep, "ROOT", self.root),
            self.assertRaisesRegex(ValueError, "unsupported"),
        ):
            prep.check_recipe(recipe, artifacts, wheels)

    def test_snapshot_inventory_matches_original_schema(self) -> None:
        tree = self.root / "inventory"
        tree.mkdir()
        for index in range(76):
            name = "dspy" if index == 0 else "synthetic" + str(index)
            version = "3.1.0" if index == 0 else "1.0"
            dist = tree / (name + ".dist-info")
            dist.mkdir()
            (dist / "METADATA").write_text(
                "Name: " + name + "\nVersion: " + version + "\nLicense: MIT\n"
            )
            (dist / "LICENSE").write_text("Synthetic notice fixture")
        from typing import cast

        self.assertEqual(
            prep.inventory_bytes(prep.snapshot(tree)),
            cast(Any, offline_dspy.inventory)(tree),
        )

    def test_recipe_file_boundaries(self) -> None:
        path = self.root / "recipe.json"
        path.write_text("{}")
        self.assertEqual(prep.read_recipe(path), {})
        link = self.root / "recipe-link.json"
        link.symlink_to(path)
        with self.assertRaisesRegex(ValueError, "symlink"):
            prep.read_recipe(link)
        path.write_bytes(b" " * (1024 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, "size limit"):
            prep.read_recipe(path)

    def test_unsafe_zip_directory(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../escape/", b"")
        with self.assertRaisesRegex(ValueError, "unsafe"):
            prep.wheel_identities(
                {"bad.whl": buffer.getvalue()},
                "example==1.0.0 --hash=sha256:" + "0" * 64,
            )

    def test_snapshot_directory_node_bound(self) -> None:
        (self.base / "empty").mkdir()
        with (
            patch.object(prep, "MAX_FILES", 1),
            self.assertRaisesRegex(ValueError, "limit"),
        ):
            prep.snapshot(self.base)

    def test_gzip_metadata_expansion_is_bounded(self) -> None:
        recipe, artifacts, wheels = self.recipe_fixture()
        with (
            patch.object(prep, "ROOT", self.root),
            patch.object(prep, "MAX_BYTES", 1024),
        ):
            with self.assertRaisesRegex(ValueError, "expansion limit"):
                prep.check_recipe(recipe, artifacts, wheels)
