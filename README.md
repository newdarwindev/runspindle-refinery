# runspindle-refinery

Python DSPy experiments, feedback lineage and approved candidate serving.

**Status: partial process-local lineage candidate; no deployed or production-ready implementation.**

This repository is one boundary of [Runspindle](https://github.com/newdarwindev/runspindle). Repository boundaries do not require one microservice each.

- Read [specifications](specs/README.md), [agent guidance](AGENTS.md), [provenance](provenance.json), [status](STATUS.json) and [legal status](LEGAL.md).
- Run `python3 tools/check_scaffold.py` for structural validation only.
- Run `python3.13 -m unittest discover -s tests -v` for synthetic lineage behavior tests (Python >=3.11 required). See [evidence and limits](docs/IMPLEMENTATION_EVIDENCE.md).
- Operational entrypoints and acceptance gates deliberately fail closed until implemented.
- GitHub Actions use isolated **self-hosted local runners only**. No GitHub-hosted fallback. Runner capacity is not established by publishing workflows.
- No credentials, real sessions, messages, datasets, browser profiles or live host inventory belong here.

No old repository is retired by this scaffold. Migration and archival are separate verified gates.

Selected historical code is preserved as [inert migration references](docs/reference/README.md). It is not installed or executed.

New conceptual documentation is in [docs/design](docs/design/README.md). Its published PDFs are ReportLab adaptations; TeX compilation and runtime acceptance are not established.

The [pinned shared fixture consumer](docs/SHARED_FIXTURES.md) tests refinery feedback/candidate observations without sibling checkouts; runtime protocol integration remains provisional.

## Implemented validation

Run `python3 tools/validate.py` for all implemented structural, language, fixture, artifact and mutation checks in this repository. The same entrypoint runs on trusted `main` and `ci/*` pushes and manual qualification using disposable Strix x64 guests. Logs include the checkout SHA, image/tool identities, command results and applicable artifact checksums. Unimplemented builds and product acceptance remain explicit in `STATUS.json`; blocked acceptance workflows retain their failing conclusions. ARM64 and native macOS qualification remain pending.

## Desired technical direction

Measured workflow improvement: this component contributes to turning authorized one-off work into
repeatable, maintained cross-application workflows. Scoped access, managed
sessions, explicit human exceptions and evaluated DSPy candidates are intended
to compose into that capability; no automatic improvement or live integration
is implied.

Read [the component-specific technical intent](specs/desired-intent.md) for its
interfaces, privacy boundaries, failure cases and acceptance obligations. Current
implementation and evidence limits above remain unchanged.
