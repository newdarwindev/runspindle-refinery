# Paper visual comparison contract

This directory is separate from the retained `docs/design` source, assets and
historical build receipt. The checker does not compile TeX or confer runtime
acceptance. A separately hashed source-build receipt can document a fresh build;
`this_command_tex_build: not_run` describes only this rendering/check command.

## Candidate and reviewed states

`tools/paper_visual.py` accepts a versioned JSON manifest, authored and reviewed
explicitly. There is no accept/update-baseline mode. The manifest requires:

- `schema: runspindle.paper-visual.v1`, `scale: 1300`, integer
  `pixel_tolerance: 0` and `status: candidate` or `reviewed`.
- `tools`: exact paths exported by `TOOLS` in the checker. `runtime_files` maps
  those executables **and the captured loaded library, OS/font configuration
  closure** to SHA-256. Capture resolved system targets; do not replace a pin
  after a mismatch. Record versions and environment capture in the review receipt.
- `sources`: SHA-256 for every path in `SOURCES`, with no omitted or extra entry.
  These bind the unchanged paper, TeX, eight figures and historical evidence.
- `source_build_receipt: {path, sha256}`: an additive receipt under this directory.
  It must distinguish actual new compilation, reuse of a retained compilation,
  unavailable tools and the precise source revision. A hash verifies receipt
  identity, not the truth of claims inside it; review remains required.
- `text_anchors`: ordered nonempty strings for all names in `NAMES`. Pages must
  include their headings and figure captions; standalone figures must include
  their visible labels. Normalize whitespace only. Record reading-order review
  separately; these assertions cannot establish accessible PDF semantics.
- `links`: ordered `{page, url}` records from actual `pdfinfo -url`, including
  duplicate annotations. The current paper has six annotations/five unique URLs.
- For `reviewed` state, `references`: exactly 16 records keyed by `NAMES`, each
  containing `width`, `height`, `mode: RGB`, and `pixel_sha256`. Also supply a
  nonempty `review_receipt` identifying the explicit review. The tool checks the
  state, but cannot authenticate a human reviewer.

Generate only a candidate into a nonexistent directory with a canonical existing
parent. Run in the exact pinned Linux environment; no dependencies are installed:

```sh
python3 tools/paper_visual.py render --manifest docs/paper-visual/manifest.json --output "$PWD/.verification/paper-actual-1"
python3 tools/paper_visual.py check --manifest docs/paper-visual/manifest.json --output "$PWD/.verification/paper-actual-1"
```

The second command rejects a candidate manifest. Review actual pages 1–8 and all
eight figures, fit-width and enlarged, plus contact sheets. Only after review may
an authorized maintainer explicitly select the reference pixel hashes and attach
a review receipt. Keep preview PNGs/contact sheets and their hashes separately;
raw PPM files are temporary and need not be published. A second independent render
must compare against the frozen reference, never regenerate it. The checker
reports differing named rasters and preserves actual outputs for overlays/diffs.

Each raster is canonical P6 RGB at a maximum 1300-pixel edge, bounded dimensions
and exact byte count. Pixel-hash and geometry equality is zero tolerance. Each
subprocess runs in a new network-isolated bubblewrap namespace, with 30-second
wall/CPU bounds, 512 MiB address-space and 32 MiB per-file bounds. The complete
output is bounded at 80 MiB. Inputs and outputs reject symlinks and special files;
trusted system library symlinks resolve to hashed package targets. Source PDFs
are fixed, reviewed inputs; arbitrary PDF uploads are not supported.

Partial failures leave a fresh output directory for diagnosis; retry into another
fresh directory. After review, remove only those explicitly chosen temporary
output directories using normal local cleanup. No automatic cleanup or baseline
overwrite occurs. Pin updates, baseline selection and thresholds require review.

## Evidence limits and remaining review

Source tests use invented 2×2 RGB data. Whitening its black label proves the
comparison rejects a missing-label defect without modifying expected hashes. It
is a synthetic checker mutation, **not** a defective rebuilt paper. Real-paper
clipping mutation, page/figure dispositions, PNG previews/contact sheets,
source-build receipt, full runtime pins and reviewed baseline selection are
separate qualification deliverables, not supplied by these unit tests.

Text anchors, actual URI records, Letter page geometry and embedded/subset/Unicode
font flags are checked independently of pixels. These do not prove semantic
reading order, sufficient contrast or screen-reader accessibility. The current
paper is untagged; unsupported accessibility must be explicit. Browser viewport,
DOM, responsive-layout and keyboard claims do not apply to this fixed-layout PDF.
No provider execution, performance result, production or parent-issue acceptance
follows from visual comparison.
