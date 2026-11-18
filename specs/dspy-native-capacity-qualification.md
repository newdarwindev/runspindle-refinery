# Controlled-overlap shared-capacity qualification

The [sanitized receipt](../docs/dspy-native-capacity-20261118.json) binds actual
native execution to source `3eeccf063be41b3391cc4d1acc4ce5d528ede063`, tree
`1f1ff7a52d56faeb037bb75d9e3ba2abdeccb404`.

Two network-none launcher containers returned identical passing reports for
known-usage and unknown-token variants. Each variant used two actual Python
processes with independent optimizer experiments and one shared calls-limit-1
budget. The first process held its real completion before settlement. Only then
did the second process enter actual compilation and fail to reserve capacity.
The controller independently verified the pending reservation before release.
This is **controlled overlap, not simultaneous acquisition**.

| Observation | Known usage | Unknown tokens |
| --- | --- | --- |
| First / second new dispatches | 1 / 0 | 1 / 0 |
| Actual compile entries per worker | 1 each | 1 each |
| Final calls / pending | 1 / 0 | 1 / 1 |
| Final public token / cost usage | 1 / 1 | unknown / unknown |
| Retained first terminal reason | `calls_exhausted` | `calls_exhausted` |
| Candidates produced | none | none |
| Network attempts | zero | zero |

Both workers denied later reservations. The second worker could not dispatch,
replace/reset the pending ledger, or create an attempt. Known usage settled once;
unknown-token usage retained its unresolved charge and actual receipt. The
initial exhaustion reason survived settlement. Synthetic completion latency was
zero; this is not nonzero-latency or wall-clock concurrency qualification.

The adopted closure and sealed input hashes matched. The original no-NIC guest,
container network-none settings, 120-second launcher limit and cleanup remained
unchanged. Guest execution including preparation took 230.418 seconds, exited
cleanly and left no matching guest process. Independent review checked all 20
source hashes, both reports, worker/durable invariants, inputs and cleanup.

Scope excludes simultaneous acquisition races, arbitrary multi-host workloads,
reflection, live-provider/model quota, production quality, deployment and
promotion. Reproduction depends on the retained sealed UDF wheelhouse; its
distribution and reconstruction remain separate claims. Private raw host logs
remain retained by hash rather than published.
