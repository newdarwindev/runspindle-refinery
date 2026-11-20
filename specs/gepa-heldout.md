# GEPA locked heldout extension

This implemented component extension has separate component and native
qualification boundaries. Component tests use real SQLite, original component
Dispatch and immutable hand-built compile/evaluation receipts; they are not DSPy
or GEPA execution receipts. The [reviewed heldout harness](gepa-heldout-qualification.md)
adds actual-framework observation without treating earlier compile-only evidence
as proof of evaluation.

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

## Source-bound native 5/2/0 qualification

The separate reviewed harness uses the unchanged pinned Python3.13.13 closure,
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

The bounded synthetic campaign at exact source `35b3715c66cac11d41f45c076367c1cc06c7b8fa`
observed this 5/2/0 sequence in two launcher runs, with independent source/receipt
review and cleanup checks. The source is published in [PR96](https://github.com/newdarwindev/runspindle-refinery/pull/96);
full native receipts remain separately retained private evidence. This statement
does not claim that this documentation descendant ran, that hosted CI passed,
or that live-provider or production-quality acceptance is complete.
