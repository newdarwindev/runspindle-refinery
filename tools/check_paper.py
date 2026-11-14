"""Validate the reviewed paper closure; never implies runtime acceptance."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(root: Path) -> dict[str, object]:
    design = root / "docs/design"
    manifest = json.loads((design / "artifacts.json").read_text())
    disposition = json.loads((design / "source-disposition.json").read_text())
    evidence = json.loads((design / "build-evidence.json").read_text())
    tex = (design / "workflow-refinery.tex").read_text()
    entries = {item["path"]: item for item in manifest["artifacts"]}
    if len(entries) != len(manifest["artifacts"]):
        raise AssertionError("duplicate artifact")
    expected = {
        str(p.relative_to(root))
        for p in design.rglob("*")
        if p.is_file() and p.name != "artifacts.json"
    }
    if set(entries) != expected:
        raise AssertionError("unlisted or missing paper artifact")
    for path, item in entries.items():
        target = (root / path).resolve()
        if not (
            target.is_relative_to(design.resolve()) and (not (root / path).is_symlink())
        ):
            raise AssertionError()
        if sha(target) != item["sha256"]:
            raise AssertionError(path)
        if item["implementation_evidence"] is not False:
            raise AssertionError()
    if disposition["source_id"] != "SRC-013":
        raise AssertionError()
    if disposition["unresolved_content_or_rights"] != []:
        raise AssertionError()
    if len(disposition["sections"]) != 9:
        raise AssertionError()
    figures = disposition["figures"]
    if {item["selected_figure"] for item in figures} != set(range(1, 9)):
        raise AssertionError()
    for item in figures:
        if item["disposition"] != "retained_cleared_historical_vector_asset":
            raise AssertionError()
        if item["checks"]["pixels"] != "reviewed":
            raise AssertionError()
        if not item["checks"]["links"] == item["checks"]["attachments"] == 0:
            raise AssertionError()
        if entries[item["destination"]]["historical_original"] is not True:
            raise AssertionError()
    if "figures/#4.pdf" not in tex:
        raise AssertionError()
    for item in figures:
        if "{" + Path(item["destination"]).stem + "}" not in tex:
            raise AssertionError()
    for forbidden in (
        "\\write18",
        "\\openin",
        "\\openout",
        "\\read",
        "\\input",
        "\\include{",
        "\\immediate",
        "\\directlua",
        "\\special",
        "\\catcode",
    ):
        if forbidden in tex:
            raise AssertionError(forbidden)
    if len(re.findall("\\\\view\\{", tex)) != 8:
        raise AssertionError()
    urls = set(re.findall("\\\\href\\{([^}]+)\\}", tex))
    if not (urls == set(evidence["pdf_links"]) and len(urls) == 5):
        raise AssertionError()
    if not all(
        (
            url.startswith("https://github.com/newdarwindev/runspindle-refinery/")
            for url in urls
        )
    ):
        raise AssertionError()
    if "Copyright (c) 2026 newdarwindev" not in tex:
        raise AssertionError()
    if evidence["source_sha256"] != sha(design / "workflow-refinery.tex"):
        raise AssertionError()
    if evidence["pdf_sha256"] != sha(design / "workflow-refinery.pdf"):
        raise AssertionError()
    if evidence["toolchain_lock_sha256"] != sha(design / "toolchain-lock.json"):
        raise AssertionError()
    if not evidence["pages"] == evidence["figures"] == 8:
        raise AssertionError()
    if evidence["rendering"]["reviewed_pages"] != list(range(1, 9)):
        raise AssertionError()
    if not evidence["overfull_boxes"] == evidence["underfull_boxes"] == 0:
        raise AssertionError()
    if evidence["runtime_acceptance"] != "not_run":
        raise AssertionError()
    if (
        not evidence["shell_escape"]
        is evidence["parse_first_line"]
        is evidence["automatic_generation"]
        is False
    ):
        raise AssertionError()
    if evidence["pass_history"][-1]["accepted"] is not True:
        raise AssertionError()
    if not all((not p["accepted"] for p in evidence["pass_history"][:-1])):
        raise AssertionError()
    lock = json.loads((design / "toolchain-lock.json").read_text())
    if len(lock["files"]) != 83:
        raise AssertionError()
    if len({entry["path"] for entry in lock["files"]}) != 83:
        raise AssertionError()
    for entry in lock["files"]:
        if not entry["path"].startswith(
            (
                "/usr/share/texmf/",
                "/usr/share/texlive/texmf-dist/",
                "/var/lib/texmf/",
                "/etc/texmf/",
                "/usr/bin/",
            )
        ):
            raise AssertionError()
        if not re.fullmatch("[0-9a-f]{64}", entry["sha256"]):
            raise AssertionError()
    source = next(
        (
            s
            for s in json.loads((root / "provenance.json").read_text())["sources"]
            if s["source_id"] == "SRC-013"
        )
    )
    if (
        source["migration_status"]
        != "adapted_scope_complete_component_verified_mirror_pending"
    ):
        raise AssertionError()
    if manifest["migration_status"] != "partial":
        raise AssertionError()
    return {
        "status": "passed",
        "evidence_class": "paper_closure_integrity",
        "figures": 8,
        "pages": 8,
        "runtime_acceptance": "not_run",
    }


if __name__ == "__main__":
    print(json.dumps(validate(ROOT), sort_keys=True))
