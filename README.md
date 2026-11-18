# REFINERY — MEASURED IMPROVEMENT WITH TRACEABLE EVIDENCE

Improve a defined operation, not an unbounded agent.

**Status: Durable authorized datasets and bounded synthetic DSPy/GEPA compile, heldout evaluation and recovery are implemented. Live model-quality and production serving qualification remain incomplete.**

Normal text describes implemented boundaries; *italic text marks upcoming work or qualification*. Neither is permission to activate services or use live accounts.

## REFINERY CONNECTS FEEDBACK TO A QUALIFIED CANDIDATE

**REFINERY** implements dataset lineage, retained-feedback controls, evaluation budgets, immutable candidate identity and serving-admission boundaries. An example stays attached to the exact source and output it evaluates; a score is evidence for review, never automatic authority to deploy.

DSPy supplies optimization machinery. **REFINERY** supplies the operation signature, permitted examples, locked partitions, cumulative budget and exact candidate identity around it. The implemented [BootstrapFewShot adapter](specs/dspy-bootstrap-optimizer.md) calls the framework compiler for a bounded program with a scripted provider. The [pinned native offline qualification](specs/dspy-native-qualification.md) now covers actual compilation, locked evaluation and fresh-process recovery with synthetic completions. It is not evidence of live model-quality gains.

**DURABLE EVIDENCE** uses SQLite for offline records and budget reservations. [Retained-feedback use](specs/retained-feedback-use.md) checks current permission separately from collection. [Explicit revalidation](specs/feedback-revalidation.md) can create a current-pin receipt only after fresh source checks and collection/training consent; old receipts remain immutable and do not inherit new permission. [Serving readback integrity](specs/serving-readback-integrity.md) keeps evaluated bytes distinct from claimed outcomes.

**CANDIDATE SELECTION** preserves dataset, split, provider and content identity. It does not turn a cached result into authority, a teacher answer into verified gold, or an uncertain budget reservation into free spending.

*Upcoming qualification includes representative real-data evaluation, actual provider cost/quality measurement and approved production serving. Collection, training use, evaluation and deployment remain separate decisions; there is no automatic production improvement claim.*

## CURRENT LEARNING DATA FLOW

An explicitly authorized caller starts collection and learning. A thumbs-up can
mark an exactly attributed answer as gold; it does not itself authorize private
content retention, training, export or deployment. There is no background trainer
or Temporal dependency in this local path.

| Step | Current component and boundary |
| --- | --- |
| Authenticate feedback | [Tether-pinned collection](refinery/feedback_collection.py) binds verified feedback to the exact actor, source and output revision; ambiguous/conflicting material is not silently promoted. |
| Retain authorized examples | [DatasetRegistry](refinery/dataset_materialization.py) materializes exact bytes only with current permission, original provenance and family/split fences. Withdrawal remains effective for subsequent use. |
| Freeze phase selections | [Batch capture](refinery/dataset_batch.py) produces immutable hash-bound train/evaluation selections, not a saved permission grant. |
| Admit the complete learning run | [Session.materialized_learning](specs/materialized-learning-admission.md) preflights both current phase batches before a new run spends budget; evaluation resume never reopens training. Admission is not a cached permission grant. |
| Compile and evaluate | The existing [durable Session](refinery/durable_dspy.py) owns the candidate, heldout seal and cumulative budget. [GEPA](specs/gepa-heldout.md) reflects on train-only evidence and evaluates the locked instruction without reopening training. |
| Recover or project | Completed replay rechecks current authority without new model effects. [Langfuse projection](specs/feedback-projection-outbox.md) is a separately authorized observability output, not training authority or a prerequisite for local learning. |

The [actual-framework heldout harness](specs/gepa-heldout-qualification.md)
distinguishes synthetic compile/evaluation/replay effects from live model calls.
*Representative real data, paid-provider quality/cost evaluation and approved
production promotion remain separate acceptance work.*

## FROM A CORRECTION TO AN EVALUATED CANDIDATE

The improvement loop must preserve what the person corrected and what they authorized. The following scenarios distinguish current evidence handling from *upcoming operation-specific/live qualification*.

### CORRECT THE PACK SIZE WITHOUT TRAINING ON THE WRONG ANSWER

A bakery operator corrects a pack-size assumption in the morning preparation result. **REFINERY** has [retained-feedback collection](specs/retained-feedback-collection.md) and [fresh use checks](specs/retained-feedback-use.md) to bind eligible material to exact source/output identity, consent and a locked split. A correction is not blanket endorsement of the original answer or permission to retain an entire session.

*The complete bakery workflow will interpret the task-specific correction, verify the revised quantities, keep conflicting or ambiguous feedback out of gold examples, and compare a new candidate on held-out preparation cases before any serving approval.* The outcome sought is a demonstrably better recurring preparation operation, not automatic deployment after a reaction. [Pack-size concept, slide 17](https://newdarwindev.github.io/runspindle_for_bloomberg/assets/Runspindle_Revised_Gallery.pdf#page=17) · [Optimizer boundary](specs/dspy-bootstrap-optimizer.md).

### KEEP FEEDBACK HISTORY HONEST THROUGH AN UPGRADE

A maintainer upgrades the pinned Tether source while retaining an earlier dataset receipt. **REFINERY'S EXPLICIT REVALIDATION**, merged in [PR #50](https://github.com/newdarwindev/runspindle-refinery/pull/50), leaves the old receipt immutable and denied by the current-pin use gate. For the one supported transition, fresh source observations and collection/training consent may create a new receipt linked to the old one only when the exact batch, fences, selections and split match.

The implemented outcome is attributable continuity, not rewritten history or inherited permission. Changed, deleted, forged or incomplete material fails closed; subsequent experiment use still needs fresh approval. [Upgrade contract](specs/feedback-revalidation.md) · [Old/new-process evidence](docs/feedback-revalidation-evidence.json). *Additional dependency transitions and live feedback/provider qualification require their own verification; this path does not generalize them automatically.*

## ENGINEERING MAP

| Responsibility | Contract and evidence |
| --- | --- |
| Offline criteria | [Issue #1 acceptance map](specs/issue1-acceptance.md) |
| Framework boundary | [BootstrapFewShot](specs/dspy-bootstrap-optimizer.md) · [Preparation candidate](specs/dspy-preparation-candidate.md) |
| Feedback continuity | [Revalidation contract](specs/feedback-revalidation.md) · [Evidence](docs/feedback-revalidation-evidence.json) |
| Data and budget durability | [Durable offline model](specs/durable-offline.md) · [Budget ownership](specs/budget-settlement-ownership.md) |
| Candidate use | [Serving authority](specs/serving-dispatch-authority.md) · [Readback integrity](specs/serving-readback-integrity.md) |

## VISUAL STORY AND PRODUCT CONTEXT

[![Conceptual observations and improvement; not a runtime screenshot](https://newdarwindev.github.io/runspindle_for_bloomberg/gallery/24.webp)](https://newdarwindev.github.io/runspindle_for_bloomberg/assets/Runspindle_Gallery_Index.html#page-24)

**Conceptual illustration, not implementation evidence:** “Observations and improvement,” gallery page 24. The asset remains hosted by the [Runspindle for Bloomberg source library](https://github.com/newdarwindev/runspindle_for_bloomberg/blob/a109172363736d4312d303e292f8c0d3a857e394/ASSETS.md); it is linked here, not copied or relicensed. The gallery uses fictional businesses and synthetic values. No Bloomberg affiliation, endorsement, live integration or measured savings is implied.

- [Open the exact slide in the 37-page gallery](https://newdarwindev.github.io/runspindle_for_bloomberg/assets/Runspindle_Revised_Gallery.pdf#page=24) · [Original 29-page UI gallery](https://newdarwindev.github.io/runspindle_for_bloomberg/assets/Runspindle-UI-gallery.pdf).
- [Product concept: 16-page brief](https://github.com/newdarwindev/runspindle_for_bloomberg/blob/a109172363736d4312d303e292f8c0d3a857e394/assets/runspindle_bloomberg_product.pdf) · [40 proposed workflow scenarios](https://github.com/newdarwindev/runspindle_for_bloomberg/blob/a109172363736d4312d303e292f8c0d3a857e394/assets/runspindle_bloomberg_workflows.csv).
- [Research proposal: maintained workflows under explicit authority](https://github.com/newdarwindev/runspindle_for_bloomberg/blob/a109172363736d4312d303e292f8c0d3a857e394/assets/runspindle-paper.pdf) · [Full-cost model, not measured savings](https://github.com/newdarwindev/runspindle_for_bloomberg/blob/a109172363736d4312d303e292f8c0d3a857e394/economics/README.md).

These materials explain the product thesis: useful work should leave a checkable record. Their conceptual scenes do not supersede this repository's implementation evidence or acceptance gates.

## DEVELOPMENT AND ACCEPTANCE

This repository is one boundary of [Runspindle](https://github.com/newdarwindev/runspindle). Repository boundaries do not require one microservice each.

- Read [specifications](specs/README.md), [agent guidance](AGENTS.md), [provenance](provenance.json), [status](STATUS.json) and [legal status](LEGAL.md).
- Run `python3 tools/check_scaffold.py` for structural validation only.
- Run `python3.13 -m unittest discover -s tests -v` for synthetic lineage behavior tests (Python >=3.11 required). See [evidence and limits](docs/IMPLEMENTATION_EVIDENCE.md).
- Unimplemented operational entrypoints and live acceptance gates remain fail-closed; the implemented local APIs and qualification harnesses above have narrower explicit contracts.
- GitHub Actions use isolated **self-hosted local runners only**. No GitHub-hosted fallback. Runner capacity is not established by publishing workflows.
- No credentials, real sessions, messages, datasets, browser profiles or live host inventory belong here.

No old repository is retired by this scaffold. Migration and archival are separate verified gates.

Selected historical code is preserved as [inert migration references](docs/reference/README.md). It is not installed or executed.

New conceptual documentation is in [docs/design](docs/design/README.md). Its published PDFs are ReportLab adaptations; TeX compilation and runtime acceptance are not established.

The [pinned shared fixture consumer](docs/SHARED_FIXTURES.md) tests refinery feedback/candidate observations without sibling checkouts; runtime protocol integration remains provisional.

## Implemented validation

Run `python3 tools/validate.py` for all implemented structural, language, fixture, artifact and mutation checks in this repository. The same entrypoint runs on trusted `main` and `ci/*` pushes and manual qualification using disposable Strix x64 guests. Logs include the checkout SHA, image/tool identities, command results and applicable artifact checksums. Unimplemented builds and product acceptance remain explicit in `STATUS.json`; blocked acceptance workflows retain their failing conclusions. Selected Linux ARM64 DSPy/GEPA campaigns have separate exact-source synthetic receipts; this does not qualify every ARM64 or native macOS workload.


The [issue #1 acceptance map](specs/issue1-acceptance.md) identifies offline criteria and the separate pinned DSPy prerequisite.

Read [the component-specific technical intent](specs/desired-intent.md) for interfaces, privacy boundaries and acceptance obligations.
