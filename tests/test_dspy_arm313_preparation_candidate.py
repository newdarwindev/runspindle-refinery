"""Synthetic input/output audit checks; no installer or framework execution."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tests.test_dspy_x64_preparation_candidate import archive, record
from tools import dspy_arm313_preparation_candidate as prep
from tools import dspy_preparation_candidate as common


class Arm313CandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for folder in ("offline/python313", "wheels", "artifacts", "a", "b"):
            (self.root / folder).mkdir(parents=True)
        recipe = common.read_recipe(prep.PROFILE)
        installed = {}
        hashes = {}
        lock = []
        for index in range(76):
            name, version = ("dspy", "3.1.0") if index == 0 else (f"dep{index}", "1.0")
            dist = f"{name}-{version}.dist-info"
            contents = {
                f"{dist}/METADATA": f"Name: {name}\nVersion: {version}\nLicense: MIT\nRequires-Python: >=3.10,<3.14\n".encode(),
                f"{dist}/LICENSE": b"Synthetic fixture notice",
                f"{dist}/WHEEL": b"Wheel-Version: 1.0\nTag: py3-none-any\n",
            }
            record(contents, f"{dist}/RECORD")
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as wheel:
                for path, data in contents.items():
                    wheel.writestr(path, data)
            filename = f"{name}-{version}-py3-none-any.whl"
            data = buffer.getvalue()
            (self.root / "wheels" / filename).write_bytes(data)
            hashes[filename] = common.digest(data)
            lock.append(f"{name}=={version} --hash=sha256:{common.digest(data)}")
            installed.update(contents)
        lock_path = self.root / "offline/requirements.lock"
        lock_path.write_text("\n".join(lock))
        recipe["dependency_lock_sha256"] = common.sha(lock_path)
        recipe["wheels"] = hashes
        binary = b"Synthetic inert installer bytes"
        uv_archive = archive({"uv-aarch64-unknown-linux-gnu/uv": binary})
        checksum = (common.digest(uv_archive) + "\n").encode()
        for name, data in (
            ("uv", binary),
            ("uv.tar.gz", uv_archive),
            ("uv.tar.gz.sha256", checksum),
        ):
            (self.root / "artifacts" / name).write_bytes(data)
        recipe["uv"].update(
            binary_sha256=common.digest(binary),
            archive_sha256=common.digest(uv_archive),
            checksum_sha256=common.digest(checksum),
        )
        profile = self.root / "offline/python313/recipe.json"
        profile.write_text(json.dumps(recipe))
        for folder in ("a", "b"):
            for name, data in installed.items():
                output = self.root / folder / name
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(data)
            (self.root / folder / ".lock").write_bytes(b"")
            (self.root / folder / ".lock").chmod(0o666)
        self.enterContext(patch.object(prep, "ROOT", self.root))
        self.enterContext(patch.object(prep, "PROFILE", profile))
        self.enterContext(patch.object(common, "ROOT", self.root))

    def test_complete_audit_and_comparison_remain_candidates(self) -> None:
        inputs = prep.audit_inputs(self.root / "artifacts", self.root / "wheels")
        self.assertEqual(len(inputs["requires_python"]), 76)
        comparison = prep.compare_preparations(self.root / "a", self.root / "b")
        self.assertEqual(comparison["candidate_manifest"]["python"], "3.13.13")
        self.assertEqual(comparison["framework_qualification"], "not_run")
        self.assertIn("not_execution_authority", comparison["status"])

    def test_same_directory_is_not_two_preparations(self) -> None:
        with self.assertRaisesRegex(ValueError, "distinct"):
            prep.compare_preparations(self.root / "a", self.root / "a")

    def test_changed_bytes_reject(self) -> None:
        path = self.root / "b/dspy-3.1.0.dist-info/LICENSE"
        path.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "differ"):
            prep.compare_preparations(self.root / "a", self.root / "b")

    def test_only_mode_difference_rejects(self) -> None:
        path = self.root / "b/dspy-3.1.0.dist-info/LICENSE"
        path.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "differ"):
            prep.compare_preparations(self.root / "a", self.root / "b")

    def test_unrecorded_bytes_in_both_outputs_reject(self) -> None:
        for folder in ("a", "b"):
            (self.root / folder / "unrecorded").write_bytes(b"same")
        with self.assertRaisesRegex(ValueError, "unrecorded"):
            prep.compare_preparations(self.root / "a", self.root / "b")

    def test_changed_wheel_rejects_before_metadata(self) -> None:
        (self.root / "wheels/dspy-3.1.0-py3-none-any.whl").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "wheelhouse differs"):
            prep.audit_inputs(self.root / "artifacts", self.root / "wheels")

    def test_requires_python_conflicts_and_unknown_syntax_deny(self) -> None:
        self.assertTrue(prep.permits_python(">=3.10,<3.14"))
        self.assertTrue(prep.permits_python(">= 3.10"))
        self.assertTrue(prep.permits_python("!=3.0.*,!=3.1.*,!=3.2.*,>=2.7"))
        self.assertFalse(prep.permits_python(">=3.14"))
        self.assertFalse(prep.permits_python("<3.13"))
        self.assertFalse(prep.permits_python("!=3.13.*"))
        for value in ("", "~=3.13", ">=3.13.*", ">=3.13; ignored", "garbage"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                prep.permits_python(value)

    def test_wheel_requirement_conflict_and_duplicate_deny(self) -> None:
        for headers in (
            "Requires-Python: >=3.14\n",
            "Requires-Python: >=3.10\nRequires-Python: >=3.14\n",
        ):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as wheel:
                wheel.writestr(
                    "example-1.0.dist-info/METADATA",
                    "Name: example\nVersion: 1.0\n" + headers,
                )
            with (
                self.subTest(headers=headers),
                self.assertRaisesRegex(ValueError, "Requires-Python"),
            ):
                prep.check_python_requirements({"example.whl": buffer.getvalue()})

    def test_arm313_tags_exclude_wrong_runtime(self) -> None:
        for tag in (
            "py3-none-any",
            "cp313-cp313-manylinux_2_28_aarch64",
            "cp310-abi3-manylinux_2_17_aarch64",
        ):
            self.assertTrue(prep.compatible_tag(tag), tag)
        for tag in (
            "cp314-cp314-manylinux_2_28_aarch64",
            "cp313-cp313-manylinux_2_28_x86_64",
            "cp313-cp313t-manylinux_2_28_aarch64",
            "cp314-abi3-manylinux_2_28_aarch64",
            "cp313-cp313-manylinux_2_99_aarch64",
        ):
            self.assertFalse(prep.compatible_tag(tag), tag)


if __name__ == "__main__":
    unittest.main()
