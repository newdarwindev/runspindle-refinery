"""Static candidate evidence checks; not framework execution."""

import hashlib
import json
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "offline/linux-native-candidate"


def read(name: str) -> dict[str, Any]:
    value = json.loads((DIRECTORY / name).read_text())
    if not isinstance(value, dict):
        raise ValueError("candidate evidence must be a JSON object")
    return value


class LinuxNativeCandidateTests(unittest.TestCase):
    def test_manifest_digest_and_rights(self) -> None:
        new = read("dependencies.json")
        old = json.loads((ROOT / "offline/dependencies.json").read_text())
        digest = hashlib.sha256(
            json.dumps(new, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.assertEqual(digest, read("receipt.json")["manifest_sha256"])
        self.assertEqual(new["dependencies"], old["dependencies"])
        self.assertEqual(len(new["installed_files"]), 11877)
        self.assertEqual(new["execution"], "not_run")

    def test_bounded_actual_delta(self) -> None:
        new = read("dependencies.json")["installed_files"]
        old = json.loads((ROOT / "offline/dependencies.json").read_text())[
            "installed_files"
        ]
        added = sorted(new.keys() - old.keys())
        changed = sorted(p for p in new.keys() & old.keys() if new[p] != old[p])
        self.assertEqual(len(added), 76)
        self.assertTrue(all(p.endswith(".dist-info/uv_cache.json") for p in added))
        self.assertEqual(sum(p.endswith(".dist-info/RECORD") for p in changed), 76)
        self.assertEqual(sum(p.startswith("bin/") for p in changed), 20)
        self.assertEqual(len(changed), 96)
        self.assertFalse(old.keys() - new.keys())
        self.assertEqual(
            read("receipt.json")["differences"],
            {"added": added, "changed": changed, "missing": []},
        )

    def test_recipe_is_native_and_bound(self) -> None:
        recipe = read("recipe.json")
        self.assertEqual(recipe["interpreter"], "/usr/local/bin/python3.14")
        self.assertEqual(recipe["python_version"], "Python 3.14.0")
        self.assertEqual(recipe["network"], "none")
        self.assertEqual(recipe["image"], read("receipt.json")["image"])
        self.assertEqual(recipe["wheels"], read("wheelhouse.json")["wheels"])
        self.assertEqual(len(recipe["wheels"]), 76)
        self.assertEqual(
            read("wheelhouse.json")["image_sha256"],
            read("receipt.json")["input_hashes"]["native-wheelhouse-v2.udf.iso"],
        )

    def test_no_execution_or_promotion_claim(self) -> None:
        receipt = read("receipt.json")
        self.assertEqual(receipt["status"], "review_required_not_execution_authority")
        self.assertFalse(receipt["canonical_changed"])
        self.assertEqual(receipt["framework_execution"], "not_run")
        self.assertEqual(receipt["qualification"], "not_run")
        self.assertEqual(receipt["provider_calls"], 0)
        self.assertEqual(receipt["udf_rebuild_reproducibility"], "not_verified")
        self.assertEqual(receipt["preparations"], 2)
        self.assertTrue(receipt["fresh_guest_roots"])
        self.assertTrue(receipt["candidate_json_identical"])
        self.assertTrue(receipt["wheel_metadata_identical"])
