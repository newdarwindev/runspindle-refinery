# Refinery design adaptation

`workflow-refinery.pdf` is a new concise ReportLab-rendered design document. `workflow-refinery.tex` is editable source, not compile-verified. These are not the historical source PDF or a faithful re-render, and they do not establish working DSPy serving.

The canonical [composition renderer](https://github.com/newdarwindev/runspindle/blob/main/docs/marketing/build_documents.py) regenerates the new document. See the composition [build disclosure](https://github.com/newdarwindev/runspindle/blob/main/docs/marketing/BUILD.md). Copies are tied to hashes in `artifacts.json`. Overall selected-source migration remains partial.

Retain [source notices](THIRD_PARTY_NOTICES.md) and the included MIT notice.

## Technical grounding for future presentations

Use [desired intent](../../specs/desired-intent.md) when revising conceptual
material. Explain how scoped access, session continuity, human exceptions and
evaluation can compose into maintained workflows. Keep planned capabilities
separate from measured outcomes and implemented components. Existing rendered
artifacts are unchanged; this link does not claim they were regenerated.
