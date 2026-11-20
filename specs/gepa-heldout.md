# GEPA locked heldout extension

This is an implemented component extension requiring independent review and
actual-framework qualification. The tests use real SQLite, original component
Dispatch and immutable hand-built compile/evaluation receipts. They are not DSPy
or GEPA execution receipts. Existing native materialized compilation evidence is
preserved and does not automatically qualify this new evaluation path.

## One owner, candidate and budget

The existing GEPA instruction codec verifies the exact candidate digest, original
training lineage, selected instruction and complete compile-attempt digest. The
new evaluator admits only the exact GEPA compiled record. It never accepts a
Bootstrap demo candidate, invokes GEPA.compile again, or invokes reflection.

The baseline is the fixed INITIAL instruction; the candidate is the decoded
immutable instruction. Both use the fixed `case -> answer` program with one
predictor and zero demos. Original Dispatch meters every prediction against the
same provider cursor, VirtualClock and SQLiteBudget. Current use permission and
program/signature/demo identity are rechecked before effects. Baseline requests
bind a distinct `refinery.gepa.baseline.v1` artifact; candidate requests bind the
locked candidate SHA. No budget limit is raised or reset (GEPA remains at most
16 total calls). Larger cohorts may therefore fail closed through exhaustion.

Session keeps the existing claim/state machine and global family seal. GEPA
registers immutable optimization, reflection and evaluation authority routes.
Compilation checks optimization plus reflection; evaluation and completed replay
check evaluation only, never reopen training after sealing. Materialized
optimization/evaluation manifests cover their exact phase examples. The generic
owner-less read remains denied once the cohort is owned/sealed. Current registry
withdrawal still denies completed replay without changing the budget/checkpoint.

Stored GEPA reports use `gepa_evaluated`, separate from Bootstrap's unchanged
`dspy_evaluated` and byte-compatible lifecycle. Compile witnesses must equal only
the optimization/reflection ticket prefix. Baseline/candidate receipts form the
ordered suffix; no compile request may follow it and no baseline may follow a
candidate. Compile request sequence and existing per-case/per-stage evaluation
attempt counts retain their original semantics. Extra witnesses, missing
reservations, changed scripts and cross-phase receipts are rejected.

Unknown usage/provider failure retains charges; cancellation and exhaustion stop
new effects. Interrupted evaluation remains owned/blocked, not automatically
recompiled or retried. Successful completed replay imports no framework and
performs no provider dispatch. This is not promotion or live-quality evidence.

## Read verified held-out measurements

Call `Session.measured_heldout()` on the existing GEPA owner to read a completed
report. The read verifies current evaluation access, the durable report digest,
the exact candidate and fixed baseline, the sealed cohort, and its original
ordered attempt receipts. It uses the same verification path as completed
evaluation recovery. A compiled or blocked run cannot supply measurements.

The `runspindle.gepa-heldout-measurement.v1` document binds the report to its owner,
configuration, split, baseline and candidate by digest. The `rows` object contains
paired `baseline` and `candidate` arrays sorted by opaque case identity. Each case
identity is SHA-256 of the existing storage codec's output-ID/revision tuple.
Each row records correctness against the retained held-out gold digest and the
sum of settled `tokens` and `cost_micros` for that case and stage. Compilation and
reflection charges stay in the durable run ledger. The document discloses no
prediction text, gold text, or original output identifiers.

The source is currently the offline scripted provider. Its exact-match metric
and metered fixture charges remain synthetic. The read starts no model dispatch
or optimizer and grants no promotion authority. A consumer still needs an exact
case-to-cohort mapping and its independent quality, regression and cost gates.


## Adversarial component coverage

- Changed candidate/instruction/history/digest and cross-codec input before runtime.
- Current independent evaluation denial, post-reservation authority replacement,
  fixed program/signature/demo mutation, cancellation and real call exhaustion.
- Unknown provider charge retention and no retry; interrupted ownership recovery.
- Exact ordered compile-prefix/heldout-suffix, extra witness and baseline mismatch.
- Separate evaluated object kind, cumulative budget and no-import repeated replay.
- Actual signed mixed train/heldout materialization, exact physical owner binding,
  missing/changed evaluation batch, generic read denial and current withdrawal.
- Existing Bootstrap identity/configuration/serialization goldens remain unchanged.

## Proposed next native 5/2/0 qualification

A separate reviewed harness must use the unchanged pinned Python3.13.13 closure,
exact Tether1e8 and real Git input, network-none/no-NIC isolation and same physical
signed fixture files. Do not relabel Bootstrap execution as GEPA acceptance.

1. Fresh process A invokes original GEPA.compile and metered reflection on the
   signed train-only item: exactly five observed provider effects, locked
   candidate, original train manifest and ownership/budget receipts.
2. Fresh process B restores the verified persisted clock and provider history
   without counting restoration as dispatch. It seals the same owner's distinct
   signed heldout item and invokes original Predict for baseline and candidate:
   exactly two new effects, no compiler/proposer entry, cumulative calls seven.
3. Fresh process C forbids DSPy/GEPA imports and provider entries before component
   imports; it replays the exact report with zero effects. Current heldout
   withdrawal must then deny replay without rewriting ledger/checkpoint bytes.

All candidate/configuration/manifest/physical authority hashes must remain exact
within that run. Preserve full receipts across independent runs; never normalize
physical owner hashes to fabricate equality. Per-child limits and aggregate
cleanup must be independently reviewed within the existing 120-second container
bound; no timing sufficiency claim is made here. Actual source/input/effect and
cleanup receipts, including truthful failures, are required before qualification.
