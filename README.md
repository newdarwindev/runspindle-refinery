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
