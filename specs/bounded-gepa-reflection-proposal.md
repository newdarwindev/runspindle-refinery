# Proposed bounded offline GEPA reflection adapter

**Status: design proposal only.** No reflective optimizer is implemented or
qualified by this document. It does not close [#16](https://github.com/newdarwindev/runspindle-refinery/issues/16),
change canonical pins, reinterpret earlier evidence, authorize live calls, or
replace the existing BootstrapFewShot adapter. Bootstrap trace retries are not
reflection. Existing native Bootstrap results retain their own exact-source scope.

## Decision and intended first capability

Add an explicitly selected, bounded instruction-optimization adapter using genuine
DSPy GEPA. It should observe a failed synthetic training execution, make an
attributed reflection call that proposes changed instruction text, and let the
actual GEPA engine evaluate/select the proposal. Returning a hand-selected
instruction without a reflection call is not acceptance. Synthetic score changes
are not evidence of real model-quality improvement.

The proposed initial profile is deliberately narrow:

| Boundary | Proposed admission rule |
|---|---|
| Program | One named predictor; fixed `case -> answer` input/output fields |
| Optimization data | One explicitly selected training-family example; no held-out input or gold |
| Reflection batch | Exactly one verified training trace for that predictor |
| Instruction | UTF-8 data, at most 4,096 bytes; no executable serialization |
| Reflection payload | Canonical bounded structure, at most 65,536 bytes |
| Proposal generation | At most one accepted proposal and two metered reflection attempts |
| Total effects | At most 16 provider dispatches across task, reflection, retries and qualification evaluation |
| Scheduling | Serial; fixed seed; no merge, external logging or framework checkpoint resume |
| Runtime | Existing reviewed isolated lane and 120-second outer deadline; no automatic expansion |

These are proposed ceilings, not a claim the pinned framework finishes within
them. The implementation must commit exact metric-call, virtual-time, token/cost
and per-call reservation bounds for its synthetic fixture. Native failure under a
ceiling is a failed qualification, not permission to loosen it silently. If the
engine requires another proposal, fail closed rather than running an unbounded
search. A reviewed internal stopping rule may end the search after the first
proposal; external callback objects and arbitrary passthrough options are forbidden.

## Primary API basis and unverified preparation details

The tagged upstream [DSPy 3.1.0 GEPA implementation](https://github.com/stanfordnlp/dspy/blob/3.1.0/dspy/teleprompt/gepa/gepa.py)
exposes metric-call bounds, reflection configuration, serial/thread settings and
an instruction-proposer hook. Its adaptive validation set influences selection;
it must not receive Refinery's final held-out cohort. The tagged
[GEPA 0.0.22 ProposalFn protocol](https://github.com/gepa-ai/gepa/blob/v0.0.22/src/gepa/core/adapter.py)
accepts a candidate, reflective examples and component names. DSPy's
[GEPA adapter](https://github.com/stanfordnlp/dspy/blob/3.1.0/dspy/teleprompt/gepa/gepa_utils.py)
constructs programs with proposed predictor instructions. These are the integration
seams to investigate, not proof that the retained prepared environment exposes
identical behavior.

The current requirements/installed-file manifests already name DSPy 3.1.0 and
GEPA 0.0.22, including hashed metadata/license paths. This proposal has not read
or executed those prepared modules or verified their license contents. Before
implementation adoption, verify exact installed API/source/license bytes under
the existing closure gate and record that review. No dependency download, pin
update, vendored third-party implementation or license conclusion is proposed.
Use published APIs through original adapter code; preserve upstream notices.

## Train-only proposal boundary

Both GEPA's training set and its adaptive validation set must consist solely of
admitted training-family examples. They are optimization observations, never
independent held-out evaluation. Build them from immutable dataset/split records;
reject foreign family, revision, source/output hash or component identity before
any task or reflection dispatch. Never pass the final held-out cohort into GEPA,
its feedback metric, reflection prompt, proposal history or adaptive scores.

A supported custom instruction proposer validates the engine's proposed reflection
input against the original training request, observed output receipt, deterministic
feedback and current parent-instruction digest. It then builds a new canonical
input from those admitted records, rather than forwarding arbitrary framework
trace dictionaries, exception messages or caller-supplied text. Extra components,
unknown traces, excessive input, wrong parent candidate or changed feedback deny.
The reflection predictor is real DSPy with an offline ScriptedProvider-backed LM;
the GEPA optimizer and prediction implementation are not replaced with doubles.

Instructions and reflected feedback remain untrusted data. An instruction cannot
change operation fields, authority, provider routing, budget, partition, tools or
serving permission. Do not claim semantic prompt sanitization as a security
boundary: enforce these rules outside generated text.

## Metering and first terminal cause

Reuse the existing SQLiteBudget reservation/settlement and current-use guards.
Every task call, reflection call, LM copy and retry must share one cumulative
budget and attributable request sequence. GEPA's metric counter is not a substitute
for effect accounting, particularly for reflection. A swallowed framework exception
or failure score cannot clear a terminal ledger reason or permit a candidate.

Define an explicit versioned reflection-request association binding the selected
training revision, proposal-input hash, parent-instruction hash, predictor identity,
configuration and attempt number. Persist that association with the existing
attempt/receipt mechanism; do not invent an unrelated budget or silently credit
unobserved calls. Reservation precedes dispatch. Unknown token/cost usage or
provider failure retains its upper-bound charge. Cancellation, exhaustion or
changed binding denies subsequent effects and outward success. LM copies must
not clone/refund the ledger or reset the provider cursor.

The current Bootstrap Dispatch admits only its existing stages. Introducing a
reflection stage requires a narrowly reviewed shared transport boundary or a
separate sealed adapter, with unchanged Bootstrap behavior and regression tests.
Do not broaden arbitrary stages or accept general callback-driven providers.

## Candidate versioning and durable identity

Introduce a distinct versioned instruction-candidate contract. It must bind the
fixed operation signature, named predictor, bounded instruction bytes, parent
candidate, proposal/transport receipt lineage, dataset/split, configuration and
source/dependency/toolchain digests. It is canonical data, not a pickled Module.
Reconstruction must verify exact fields and instruction bytes before any call.

Existing Bootstrap demo-only candidate bytes and stored records must keep their
original interpretation. A new candidate must not pass an old decoder merely
because both contain strings. Reject unknown versions and cross-strategy recovery.

The current Session lifecycle hardcodes Bootstrap settings and validation. Reuse
its transactions, ownership, safe Store codec, immutable objects, held-out seals
and result-identity checks through an explicitly reviewed sealed strategy contract.
Do not duplicate an unchecked second lifecycle or accept user-supplied validators.
Use a strategy-domain-separated identity/version; migration, if genuinely needed,
requires its own compatibility plan. Old records must remain readable and retain
their original authority boundaries.

Only after reflective compilation is sealed may locked held-out baseline/candidate
evaluation begin. It uses the same cumulative budget, fixed operation and exact
candidate digest. Fresh-process recovery restores the verified clock epoch and
known synthetic history without replaying effects. Completed result reuse performs
fresh authority checks and dispatches nothing. No scores authorize promotion.

## Staged implementation and acceptance

1. **Contract and adapter:** verify prepared APIs/rights, implement typed bounded
   configuration, train-only proposer admission, metered task/reflection transport
   and the versioned candidate codec. Source tests must cover malformed proposals,
   unknown versions, foreign revisions, held-out injection, extra components,
   copied cursors, excessive payloads and stopped budgets. This stage alone does
   not complete durable reflection or close #16.
2. **Durable composition:** integrate the sealed strategy with existing claims,
   receipts, candidate/family seals and current-use checks. Reject configuration,
   candidate, result and receipt tampering. Preserve old Bootstrap compatibility,
   interrupted-phase uncertainty and unknown reservations across fresh processes.
3. **Actual isolated qualification:** execute the original GEPA engine and real
   reflection/task predictors with synthetic completions. Independently observe
   compile/proposer entry and every original provider dispatch. Demonstrate a
   changed instruction selected from a genuine reflection proposal, then fresh-
   process locked evaluation and zero-effect replay. Include cancellation,
   exhaustion, unknown usage/cost, provider failure and controlled concurrent budget
   pressure. Deliberately remove train-only/identity guards and require independent
   observed-effect oracles to fail; import/syntax failure is not mutation detection.
4. **Review and evidence:** freeze exact source; pass normal/optimized component
   tests, unchanged full gates and independent review; retain actual native
   receipts, limits, failures and cleanup evidence. Distinguish synthetic oracle
   tests from real-framework runs. No skipped prerequisite becomes a pass.

Disable merge search, arbitrary proposer callbacks, telemetry, file-based framework
resume and pickle/cloudpickle persistence in the initial profile. A future broader
profile needs separate review and evidence. [#15 authenticated external feedback](https://github.com/newdarwindev/runspindle-refinery/issues/15)
and [#9 live-provider acceptance](https://github.com/newdarwindev/runspindle-refinery/issues/9)
remain separate; this design grants neither production access nor model spending.
