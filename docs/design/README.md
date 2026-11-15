# Refinery paper and reviewed figure lift

[Read the eight-page paper](workflow-refinery.pdf) or edit its [TeX source](workflow-refinery.tex).
This is a compiled, reviewed adaptation of the complete selected SRC-013 paper scope,
not a faithful historical re-render, an executable port or runtime acceptance.
All eight cleared historical vector figures are included with the retained MIT notice.
The original complete historical PDF remains a separate private reference.

## Scope and evidence

- [Section, figure and dependency disposition](source-disposition.json) accounts for every selected part, every safe exclusion, rights review and changed interpretation.
- [Build evidence](build-evidence.json) binds the source/PDF hashes, eight pages, eight figures, five unique public links, render hashes and bounded build history.
- [Source-wrapper change evidence](source-wrapper-change-evidence.json) separately records the later import-order-only correction for Ruff 0.14.5 with explicit `E4,E7,E9,F,I` selection. The historical build receipt is unchanged; all non-import AST nodes, command/environment assignments and compiled artifact bytes remain identical. This correction is not a new paper build or an execution of the committed wrapper.
- [Pinned toolchain](toolchain-lock.json) records all 80 loaded stock TeX inputs plus the engine, timeout and confinement executable hashes. Dependency bytes are not bundled.
- Raw compiler logs, recorder files and correction history are preserved privately. Public evidence records their hashes, normalized task scope and stock tool identities without private paths.
- [Artifact manifest](artifacts.json) distinguishes retained historical assets, adapted source and newly compiled output. Overall repository selected-source migration remains partial because other source IDs are separate obligations.
- [Notices](THIRD_PARTY_NOTICES.md) distinguish MIT source expression from Latin Modern fonts and unrelated sources.

The canonical artifact is this component directory. The composition copy must mirror
these bytes and figures after its dependent integration; the older composition
ReportLab generator must not overwrite this TeX-built paper. Until that integration
lands, the composition copy is stale and issue #3 remains open.

## Reproduce without installation

Use an existing compatible Linux TeX Live 2026/Debian environment with the exact
`toolchain-lock.json` files. The builder verifies hashes and refuses mismatches.
It never installs packages, generates fonts/formats, enables shell escape or contacts
providers. Each invocation is limited to 120 seconds in a network-isolated mount
namespace exposing only stock system/TeX trees and this source/output. No private
home or repository outside the selected source directory is mounted.

From the repository root, choose fresh output directories so logs are never overwritten:

```sh
python3 docs/design/build-paper.py --output "$PWD/.verification/paper-pass1"
python3 docs/design/build-paper.py --output "$PWD/.verification/paper-pass2" --previous "$PWD/.verification/paper-pass1"
pdftoppm -scale-to 1300 -png .verification/paper-pass2/workflow-refinery.pdf .verification/paper-pass2/page
python3 tools/check_paper.py
python3 tools/check_scaffold.py
python3 -m unittest discover -s tests -p 'test_paper.py'
```

Review every rendered page after source changes and inspect the recorder, links,
metadata and active-content structure. A source/PDF change requires new hashes,
review and evidence; copying an old PDF is not a build. The PDF was built with a reviewed task-local native wrapper, not by executing
the committed CLI wrapper. Their compiler/confinement command and environment
assignments were compared structurally and match exactly. The committed wrapper
is linted and strictly type-checked, but its end-to-end execution remains unrun.
The recorded build used three bounded invocations: initial pagination, a rejected caption-layout attempt,
and an explicitly permitted paragraph-boundary correction. Only the third output
is accepted. All final figures stay with their explanatory text and captions.

No provider use, benchmark gains, serving, live acceptance, deployment or new
operational permissions follow from this document validation. Runtime work is
tracked separately in [implementation evidence](../IMPLEMENTATION_EVIDENCE.md).
