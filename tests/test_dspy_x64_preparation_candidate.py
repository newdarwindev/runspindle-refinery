"""Synthetic bytes only; no installer, runtime or framework execution."""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools import dspy_preparation_candidate as common
from tools import dspy_x64_preparation_candidate as prep


def record(contents: dict[str, bytes], name: str) -> None:
    rows = [
        [
            p,
            "sha256="
            + base64.urlsafe_b64encode(__import__("hashlib").sha256(data).digest())
            .decode()
            .rstrip("="),
            str(len(data)),
        ]
        for p, data in contents.items()
    ]
    rows.append([name, "", ""])
    stream = io.StringIO()
    csv.writer(stream).writerows(rows)
    contents[name] = stream.getvalue().encode()


def archive(contents: dict[str, bytes], links: dict[str, str] | None = None) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as tar:
        for name, data in contents.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            entry.mode = 0o644
            tar.addfile(entry, io.BytesIO(data))
        for name, target in (links or {}).items():
            entry = tarfile.TarInfo(name)
            entry.type = tarfile.SYMTYPE
            entry.linkname = target
            tar.addfile(entry)
    return stream.getvalue()


class WheelTagTests(unittest.TestCase):
    def wheel(self, filename: str, tags: list[str]) -> dict[str, bytes]:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as wheel:
            wheel.writestr(
                "example-1.0.dist-info/METADATA", "Name: example\nVersion: 1.0\n"
            )
            wheel.writestr(
                "example-1.0.dist-info/WHEEL",
                "Wheel-Version: 1.0\n" + "".join(f"Tag: {tag}\n" for tag in tags),
            )
        return {filename: buffer.getvalue()}

    def test_equivalent_compressed_and_expanded_metadata(self) -> None:
        platforms = "manylinux_2_17_x86_64.manylinux2014_x86_64"
        filename = f"example-1.0-cp314-cp314-{platforms}.whl"
        for tags in (
            [f"cp314-cp314-{platforms}"],
            [f"cp314-cp314-{part}" for part in platforms.split(".")],
        ):
            with self.subTest(tags=tags):
                prep.check_wheel_tags(self.wheel(filename, tags))

    def test_compression_preserves_exact_set_and_compatibility(self) -> None:
        filename = "example-1.0-cp314-cp314-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
        for tags in (
            ["cp314-cp314-manylinux2014_x86_64"],
            ["cp314-cp314-manylinux2014_x86_64.linux_aarch64"],
            [
                "cp314-cp314-manylinux_2_17_x86_64.manylinux2014_x86_64",
                "cp314-cp314-manylinux2014_x86_64",
            ],
            [],
        ):
            with self.subTest(tags=tags), self.assertRaises(ValueError):
                prep.check_wheel_tags(self.wheel(filename, tags))
        with self.assertRaisesRegex(ValueError, "incompatible"):
            prep.check_wheel_tags(
                self.wheel(
                    "example-1.0-cp314-cp314-linux_aarch64.whl",
                    ["cp314-cp314-linux_aarch64"],
                )
            )

    def test_malformed_and_oversized_compression(self) -> None:
        for value in (
            "py3-none-any.",
            "py3-none-.any",
            "py3-none-any..linux_x86_64",
            "py3-none-any.any",
            "py3-none-any-extra",
            "py3-none-ANY",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                prep.expand_tag(value)
        with (
            patch.object(prep, "MAX_FILES", 3),
            self.assertRaisesRegex(ValueError, "limit"),
        ):
            prep.expand_tag("py2.py3-none-any.linux_x86_64")


class X64CandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for folder in (
            "wheels",
            "artifacts",
            "runtime",
            "uv-release",
            "deps-a",
            "deps-b",
            "offline",
        ):
            (self.root / folder).mkdir()
        installed = {}
        lock = []
        for index in range(76):
            name, version = ("dspy", "3.1.0") if index == 0 else (f"dep{index}", "1.0")
            dist = f"{name}-{version}.dist-info"
            contents = {
                f"{dist}/METADATA": f"Name: {name}\nVersion: {version}\nLicense: MIT\n".encode(),
                f"{dist}/LICENSE": b"Synthetic notice",
                f"{dist}/WHEEL": b"Wheel-Version: 1.0\nTag: py3-none-any\n",
            }
            record(contents, f"{dist}/RECORD")
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as wheel:
                for path, data in contents.items():
                    wheel.writestr(path, data)
            data = buffer.getvalue()
            (self.root / "wheels" / f"{name}-{version}-py3-none-any.whl").write_bytes(
                data
            )
            lock.append(f"{name}=={version} --hash=sha256:{common.digest(data)}")
            installed.update(contents)
        for folder in ("deps-a", "deps-b"):
            for path, data in installed.items():
                output = self.root / folder / path
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(data)
            (self.root / folder / ".lock").write_bytes(b"")
            (self.root / folder / ".lock").chmod(0o666)
        lock_bytes = "\n".join(lock).encode()
        (self.root / "requirements.lock").write_bytes(lock_bytes)
        (self.root / "offline/requirements.lock").write_bytes(lock_bytes)
        elf = b"\x7fELF\x02\x01" + b"\x00" * 12 + b"\x3e\x00" + b"\x00" * 44
        self.artifacts = {
            "uv.tar.gz": archive({"uv-x86_64-unknown-linux-gnu/uv": elf}),
            "python.tar.gz": archive(
                {"python/bin/python3.14": elf}, {"python/bin/python": "python3.14"}
            ),
        }
        self.artifacts["uv.tar.gz.sha256"] = (
            common.digest(self.artifacts["uv.tar.gz"])
            + "  uv-x86_64-unknown-linux-gnu.tar.gz\n"
        ).encode()
        for name, data in self.artifacts.items():
            (self.root / "artifacts" / name).write_bytes(data)
        for folder, source in (
            ("runtime", "python.tar.gz"),
            ("uv-release", "uv.tar.gz"),
        ):
            entries = prep.archive_entries(
                self.artifacts[source], links=folder == "runtime"
            )
            for path, (kind, mode, data) in entries.items():
                output = self.root / folder / path
                output.parent.mkdir(parents=True, exist_ok=True)
                if kind == "link":
                    output.symlink_to(data.decode())
                else:
                    output.write_bytes(data)
                    output.chmod(mode)
        for name, value in {
            "LOCK_SHA256": common.digest(lock_bytes),
            "UV_SHA256": common.digest(self.artifacts["uv.tar.gz"]),
            "UV_CHECKSUM_SHA256": common.digest(self.artifacts["uv.tar.gz.sha256"]),
            "PYTHON_SHA256": common.digest(self.artifacts["python.tar.gz"]),
        }.items():
            self.enterContext(patch.object(prep, name, value))
        self.enterContext(patch.object(common, "ROOT", self.root))
        self.recipe = prep.recipe_for(
            self.root,
            {
                p.name: common.digest(p.read_bytes())
                for p in (self.root / "wheels").iterdir()
            },
        )

    def test_complete_synthetic_candidate(self) -> None:
        result = prep.audit(self.root, self.recipe)
        self.assertEqual(
            result["status"], "UNREVIEWED_CANDIDATE_NOT_EXECUTION_AUTHORITY"
        )
        self.assertEqual(result["candidate_manifest"]["target"], "linux/x86_64")
        self.assertEqual(result["framework_qualification"], "not_run")
        self.assertEqual(len(result["candidate_manifest"]["dependencies"]), 76)
        self.assertEqual(result, prep.audit(self.root, self.recipe))

    def test_installer_artifact_preserved(self) -> None:
        result = prep.audit(self.root, self.recipe)
        self.assertEqual(result["installer_artifacts"][".lock"]["mode"], "0666")
        self.assertEqual(
            result["candidate_manifest"]["installed_files"][".lock"],
            common.digest(b""),
        )

    def test_installer_artifact_bad_variants(self) -> None:
        for variant in (
            "nonempty",
            "mode",
            "symlink",
            "hardlink",
            "fifo",
            "missing",
            "alias",
        ):
            with self.subTest(variant=variant):
                for folder in ("deps-a", "deps-b"):
                    target = self.root / folder / ".lock"
                    target.unlink(missing_ok=True)
                    if variant == "nonempty":
                        target.write_bytes(b"bad")
                    elif variant == "mode":
                        target.write_bytes(b"")
                        target.chmod(0o600)
                    elif variant == "symlink":
                        target.symlink_to("../requirements.lock")
                    elif variant == "hardlink":
                        target.hardlink_to(self.root / "requirements.lock")
                    elif variant == "fifo":
                        os.mkfifo(target)
                    elif variant == "alias":
                        target.write_bytes(b"")
                        target.chmod(0o666)
                        (self.root / folder / "other.lock").write_bytes(b"")
                with self.assertRaises(ValueError):
                    prep.audit(self.root, self.recipe)

    def test_exact_wrapper_interpreter(self) -> None:
        dist = "dspy-3.1.0.dist-info"
        for interpreter in (self.recipe["interpreter"], "/wrong/python"):
            for folder in ("deps-a", "deps-b"):
                base = self.root / folder
                contents = {
                    str(p.relative_to(base)): p.read_bytes()
                    for p in (base / dist).iterdir()
                    if p.name != "RECORD"
                }
                contents["bin/dspy-example"] = (
                    "#!" + interpreter + "\nprint('synthetic')\n"
                ).encode()
                record(contents, dist + "/RECORD")
                for name, data in contents.items():
                    path = base / name
                    path.parent.mkdir(exist_ok=True)
                    path.write_bytes(data)
            if interpreter == self.recipe["interpreter"]:
                self.assertIn(
                    "bin/dspy-example",
                    prep.audit(self.root, self.recipe)["generated_scripts"],
                )
            else:
                with self.assertRaisesRegex(ValueError, "interpreter mismatch"):
                    prep.audit(self.root, self.recipe)

    def test_equal_tree_record_tampering(self) -> None:
        for folder in ("deps-a", "deps-b"):
            (self.root / folder / "dspy-3.1.0.dist-info/LICENSE").write_bytes(
                b"tampered"
            )
        with self.assertRaisesRegex(ValueError, "hash or size"):
            prep.audit(self.root, self.recipe)

    def test_duplicate_distribution_alias(self) -> None:
        original = self.root / "wheels/dspy-3.1.0-py3-none-any.whl"
        original.with_name("alias-3.1.0-py3-none-any.whl").write_bytes(
            original.read_bytes()
        )
        self.recipe = prep.recipe_for(
            self.root,
            {
                p.name: common.digest(p.read_bytes())
                for p in (self.root / "wheels").iterdir()
            },
        )
        with self.assertRaisesRegex(ValueError, "duplicate or unlocked"):
            prep.audit(self.root, self.recipe)

    def test_opaque_wheel_alias(self) -> None:
        original = self.root / "wheels/dspy-3.1.0-py3-none-any.whl"
        original.rename(original.with_name("alias-3.1.0-py3-none-any.whl"))
        self.recipe = prep.recipe_for(
            self.root,
            {
                p.name: common.digest(p.read_bytes())
                for p in (self.root / "wheels").iterdir()
            },
        )
        with self.assertRaisesRegex(ValueError, "filename identity"):
            prep.audit(self.root, self.recipe)

    def test_installer_special_permission_bits(self) -> None:
        for mode in (0o4666, 0o2666, 0o1666):
            for folder in ("deps-a", "deps-b"):
                (self.root / folder / ".lock").chmod(mode)
            with (
                self.subTest(mode=mode),
                self.assertRaisesRegex(ValueError, "mode-0666"),
            ):
                prep.audit(self.root, self.recipe)

    def test_tree_special_mode_difference(self) -> None:
        (self.root / "deps-a/dspy-3.1.0.dist-info/LICENSE").chmod(0o4644)
        with self.assertRaisesRegex(ValueError, "trees differ"):
            prep.audit(self.root, self.recipe)

    def test_wheel_member_path_limits(self) -> None:
        for name in ("a/" * 66 + "deep", "a\\b", "../escape"):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as wheel:
                wheel.writestr(name, b"data")
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(ValueError, "path or depth"),
            ):
                prep.check_wheel_paths({"synthetic.whl": stream.getvalue()})

    def test_wrong_profile(self) -> None:
        self.recipe["platform"] = "linux/arm64"
        with self.assertRaisesRegex(ValueError, "recipe"):
            prep.audit(self.root, self.recipe)

    def test_wrong_archive_pin(self) -> None:
        (self.root / "artifacts/uv.tar.gz").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "pin mismatch"):
            prep.audit(self.root, self.recipe)

    def test_runtime_tamper(self) -> None:
        (self.root / "runtime/python/bin/python3.14").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "differ from pinned"):
            prep.audit(self.root, self.recipe)

    def test_runtime_link_tamper(self) -> None:
        path = self.root / "runtime/python/bin/python"
        path.unlink()
        path.symlink_to("/etc/passwd")
        with self.assertRaisesRegex(ValueError, "absolute"):
            prep.audit(self.root, self.recipe)

    def test_dependency_difference(self) -> None:
        (self.root / "deps-b/extra").write_bytes(b"unexpected")
        with self.assertRaisesRegex(ValueError, "trees differ"):
            prep.audit(self.root, self.recipe)

    def test_matching_unrecorded_rejected(self) -> None:
        for folder in ("deps-a", "deps-b"):
            (self.root / folder / "extra").write_bytes(b"unexpected")
        with self.assertRaisesRegex(ValueError, "unrecorded"):
            prep.audit(self.root, self.recipe)

    def test_symlink_dependency(self) -> None:
        (self.root / "deps-a/link").symlink_to("/etc/passwd")
        with self.assertRaisesRegex(ValueError, "symlink"):
            prep.snapshot(self.root / "deps-a")

    def test_fifo_does_not_block(self) -> None:
        os.mkfifo(self.root / "deps-a/fifo")
        with self.assertRaisesRegex(ValueError, "special"):
            prep.snapshot(self.root / "deps-a")

    def test_ancestor_symlink(self) -> None:
        (self.root / "alias").symlink_to(self.root / "deps-a")
        with self.assertRaisesRegex(ValueError, "symlink"):
            prep.snapshot(self.root / "alias")

    def test_read_mutation(self) -> None:
        target = self.root / "changing"
        target.mkdir()
        (target / "a").write_bytes(b"before")
        read = os.read
        mutated = False

        def mutating(fd: int, count: int) -> bytes:
            nonlocal mutated
            data = read(fd, count)
            if not mutated:
                mutated = True
                (target / "a").write_bytes(b"after")
            return data

        with (
            patch.object(os, "read", side_effect=mutating),
            self.assertRaisesRegex(ValueError, "changed"),
        ):
            prep.snapshot(target)

    def test_parent_swap(self) -> None:
        target = self.root / "parent"
        (target / "sub").mkdir(parents=True)
        (target / "sub/a").write_bytes(b"before")
        read = os.read
        swapped = False

        def swapping(fd: int, count: int) -> bytes:
            nonlocal swapped
            data = read(fd, count)
            if not swapped:
                swapped = True
                (target / "sub").rename(target / "old")
                (target / "sub").mkdir()
                (target / "sub/a").write_bytes(b"replacement")
            return data

        with (
            patch.object(os, "read", side_effect=swapping),
            self.assertRaisesRegex(ValueError, "changed|replaced"),
        ):
            prep.snapshot(target)

    def test_link_cycles_and_escape(self) -> None:
        for links in (
            {"a": "b", "b": "a"},
            {"a": "../outside"},
            {"a": "/outside"},
            {"a": "missing"},
        ):
            with self.subTest(links=links), self.assertRaises(ValueError):
                prep.archive_entries(archive({}, links), links=True)

    def test_link_chain(self) -> None:
        result = prep.archive_entries(
            archive(
                {"bin/python3.14": b"bytes"},
                {"bin/python": "python3", "bin/python3": "python3.14"},
            ),
            links=True,
        )
        self.assertEqual(result["bin/python"][2], b"python3")

    def test_archive_traversal_and_special(self) -> None:
        with self.assertRaises(ValueError):
            prep.archive_entries(archive({"../outside": b"bad"}))
        with self.assertRaises(ValueError):
            prep.archive_entries(archive({"ok": b"a"}, {"link": "ok"}))

    def test_archive_limit(self) -> None:
        with (
            patch.object(prep, "MAX_BYTES", 32),
            self.assertRaisesRegex(ValueError, "expansion limit"),
        ):
            prep.archive_entries(archive({"big": b"a" * 100}))

    def test_size_and_node_limits(self) -> None:
        with (
            patch.object(prep, "MAX_FILES", 1),
            self.assertRaisesRegex(ValueError, "node limit"),
        ):
            prep.snapshot(self.root / "deps-a")
        with (
            patch.object(prep, "MAX_BYTES", 1),
            self.assertRaisesRegex(ValueError, "size limit"),
        ):
            prep.snapshot(self.root / "deps-a")

    def test_tags(self) -> None:
        for tag in (
            "py3-none-any",
            "cp314-cp314-manylinux_2_28_x86_64",
            "cp39-abi3-manylinux2014_x86_64",
        ):
            self.assertTrue(prep.compatible_tag(tag), tag)
        for tag in (
            "cp314-cp314-manylinux_2_28_aarch64",
            "cp315-abi3-manylinux_2_17_x86_64",
            "cp314-cp314t-manylinux_2_28_x86_64",
            "py3-none-win_amd64",
            "cp314-cp314-manylinux_2_40_x86_64",
        ):
            self.assertFalse(prep.compatible_tag(tag), tag)

    def test_duplicate_recipe_keys(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate"):
            json.loads('{"x":1,"x":2}', object_pairs_hook=common.unique_object)

    def test_lock_and_recipe_regular_bounded(self) -> None:
        with self.assertRaisesRegex(ValueError, "size limit"):
            prep.read_input(self.root / "requirements.lock", 1)
        os.mkfifo(self.root / "fifo")
        with self.assertRaisesRegex(ValueError, "regular"):
            prep.read_input(self.root / "fifo")


if __name__ == "__main__":
    unittest.main()
