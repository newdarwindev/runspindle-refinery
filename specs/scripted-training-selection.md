# Scripted training-only candidate selection

Bounded original implementation for issues #1 and #5. This extends the existing
synthetic evaluation adapters rather than introducing a provider or serving path.
Issues remain open. Authenticated collection, actual DSPy optimizer qualification
(#16), model quality, live execution and production serving remain unavailable.

`refinery.optimization.optimize` fixes a tuple of 1–16 nonempty candidate byte
strings before dispatch. Duplicate digests are rejected. The existing lineage
validator checks exact dataset/split identity, grouped families and locked splits.
Only training cases enter `optimization` requests; requests contain immutable
identity/digest fields, not source text or gold answers. Candidates are visited in
SHA-256 order. Highest training exact-match count wins; ties choose lowest digest.
The result binds dataset, split, candidate inventory, scripted-provider bytes,
limits, reservation and retry count. Selection returns exact bytes and digest,
never promotion authority or a quality claim.

Pass the existing `Budget` or component-owned `SQLiteBudget` and exact
`ScriptedProvider` instance. No live provider subclass or fallback is supported.
Every attempt reserves before dispatch and settles using existing usage checks.
Retries (maximum three per case) share that ledger. Unknown usage retains its
reservation; provider failure, malformed response, unsafe/unknown safety and
exhausted retries return blocked with no selected candidate. Cancellation and
budget limits remain the existing ledger's admission boundary. They do not cancel
an already dispatched operation. At most 4096 potential attempts are accepted.

A caller may use the same provider/budget for existing held-out `evaluate` after
selection; evaluation does not receive a fresh budget. Tests demonstrate that
training consumption can exhaust the later evaluation. This API does not itself
run held-out evaluation, persist selection, enforce a global one-time evaluation
policy, or prevent an authorized caller from separately invoking other APIs.
Results and selection state are process-local. Concurrent searches sharing one
scripted provider cursor are unsupported; ledger concurrency remains the existing
budget adapter contract. SQLite budget durability remains
owned by the existing adapter, not a new guarantee from this selector. Actual DSPy
program optimization and production adapters are not implemented here.

BDD summary:
- Given locked training/held-out families, when finite candidates are compared,
  then actual provider calls contain training identities only.
- Given shared budget consumption, retry, cancellation or unknown usage, when
  another attempt or downstream evaluation runs, then no budget is reset and
  failed selection has no candidate output.
- Given altered lineage or repeated candidate identity, when search is requested,
  then reject before provider calls.
- Given a deliberate train/held-out substitution, the independently observed
  provider call ledger exposes forbidden held-out identities.

Run `python3 -m unittest discover -s tests -p test_optimization.py -v` and repeat
with `python3 -O`. All content, costs and latency in these tests are synthetic.
Network calls are forbidden by an independent socket spy. The existing fixture
metric is exact-string matching, not a benchmark of real model quality.

Optional persistence is now provided by [the durable wrapper](durable-training-selection.md).
The base `optimize` function above remains process-local; wrapper guarantees must
not be attributed to direct callers.
