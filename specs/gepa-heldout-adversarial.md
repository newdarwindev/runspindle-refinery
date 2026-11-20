# GEPA heldout accounting adversity

This is a qualification harness, not a native result. Component tests cover
oracles, immutable case setup, actual signed synthetic source reconstruction and
local Budget failure semantics. Actual DSPy/GEPA execution remains pending
independent review, full source gates and the separately sealed native runner.
No new runtime implementation, provider, deployment or promotion is added.

Use `tools/offline_gepa.py --suite heldout-adversarial --case CASE` with the
existing explicit prepared closure, Tether source and real Git inputs. Exactly
one case runs per unchanged 120-second container. Three fresh child processes
have separate 30-second limits, leaving outer cleanup/orchestration headroom.
This is a hard bound, not a claim of measured performance. The four cases must
be separate launcher invocations; no four-case packing or timeout widening.

Every case starts with genuine original GEPA compilation, original strict
five-effect/reflection oracle, signed mixed train/heldout materialization,
current separate use grants and one sealed Session owner/shared ledger.
The immutable seven-completion script and calls limit are fixed at construction,
before any effect. There are no strategy/callback/owner substitutions.

| Case | Evaluation | Retained budget | Fresh replay |
| --- | --- | --- | --- |
| cancelled | Stop existing ledger before actual evaluate; claim denied before seal or heldout effect | Five settled compile charges, reason cancelled; state compiled | Reattempt claim without framework import; cancellation remains, no effect |
| unknown_tokens | Actual first baseline dispatch returns unknown tokens | Six charges, five settled plus one unresolved with original receipt; total usage unknown | Same stored blocked result, no framework import/effect |
| unknown_cost | Actual first baseline dispatch returns unknown cost | Same retained accounting; different immutable script/configuration | Same stored blocked result, no framework import/effect |
| calls_exhausted | Limit six: compile five, baseline one, candidate reservation denied | Six settled charges, calls_exhausted, no candidate effect | Same stored blocked result, no framework import/effect |

The cancelled case deliberately does not claim completed-result replay: it has
no evaluated object. Other cases preserve the complete blocked report, not a
successful quality result. The existing compiled candidate remains immutable;
there is no promotion or hidden rerun. Repeated entry must preserve checkpoint
bytes and charges. Original optimization/reflection must not execute during
evaluation. Reports retain original source/input pins, phase PIDs, complete
physical owner hashes, compile candidate/configuration identity, heldout attempt
receipts and SQL settled/unknown counts. Within-run identities are exact; physical
hashes are not normalized for cross-run equality.

The existing successful heldout suite and its oracles are unchanged. Both native
network isolation and per-process socket attempt counters remain mandatory.
Any child failure is a failure, never a partial qualification receipt.
