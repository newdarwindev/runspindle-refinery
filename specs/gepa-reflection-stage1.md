# GEPA reflection stage 1: process-local source candidate

Status: implemented source candidate, not native qualification and not durable
acceptance. The existing Bootstrap codec, Session records and historical receipts
remain unchanged. This source does not close issue #16.

`refinery.gepa_optimizer.optimize` constructs the genuine DSPy GEPA optimizer and
uses its custom instruction-proposer extension to invoke a real DSPy `Predict`
with `evidence -> instruction`. GEPA, not the harness, selects the returned
program. It must select the attributed changed instruction after successful
training evaluation; unchanged or unverified outputs fail closed. One explicitly
selected training example is both trainset and adaptive valset. Final heldout
examples cannot enter task transport, metric, proposal input or the new codec.

The code shares the existing reserve/complete/settle implementation with Bootstrap
through a behavior-preserving `_complete` extraction, not a second ledger or
provider. Task and reflection framing are explicit and distinct. A single cursor
covers task calls, reflection calls and retries. Each request artifact is domain
separated by strategy version, configuration, stage, parent/current instruction
and exact canonical input. Proposal input contains only one observed training
failure, its exact attempt receipt and a deterministic correct/incorrect label.
Raw framework exception text or arbitrary feedback is not forwarded.

Bounds are one predictor, one changed proposal, at most two reflection dispatches,
16 total dispatches, six GEPA metric calls, one training example, 4096-byte output
instructions and 65536-byte reflection input. Serial fixed-seed execution disables
merging, framework resume and external logging. GEPA stop callbacks act between
iterations; independent shared ledger admission remains authoritative. Failures,
unknown usage, cancellation and cursor changes cannot produce a candidate.

`refinery.gepa_codec` defines a new `refinery.gepa.instruction.v1` format. Decoding
requires exact candidate/configuration digests and the retained complete attempt
history. The candidate binds a canonical digest of every ordered full attempt,
including final task usage and latency; changed or appended history is rejected.
It verifies training lineage, the failed parent trace, metered reflection
output, request order, no foreign effects, and successful post-proposal task
execution. It does not widen the old demonstration codec or durable Session.
Instructions remain data in a fixed signature, never executable code or authority.

## Separate supported runtime strategy

The retained GEPA 0.0.22 metadata excludes Python 3.14, so the original Bootstrap
closure is not execution authority for this adapter. The separately reviewed
[Python 3.13 strategy lane](gepa313-runtime-qualification.md) replaces the initial
closed gate with fixed adoption, exact version/architecture and complete byte/mode
verification before imports/effects. It does not modify the original canonical
closure or add caller-controlled manifests, installers or fallbacks. Actual GEPA
qualification remains unrun until an independent native receipt establishes it.

Synthetic source tests exercise the real transport, SQLite accounting and codec,
not a fake GEPA engine. They are not native framework evidence. Remaining stages:
review the extraction/adapter/codec and supported closure, run bounded real
GEPA under the isolated launcher and independent effect spies, then integrate a
sealed durable strategy and fresh-process recovery. The outer 120-second native
launcher deadline remains required when execution is enabled. No heldout score,
production support, authenticated external feedback or promotion is claimed.
