# Actual two-process ownership qualification

The [sanitized receipt](../docs/dspy-native-ownership-20261118.json) retains both
actual launcher reports at source `fafe787d51e09075e5edcfad98b763a86d165fc1`,
tree `fb17fcb14294076585dfb890b70e1fe3aa452c25`.

Two separate network-none launcher containers exercised simultaneous native
Python processes contending for one synthetic durable lifecycle. Each contender
was an actual fresh process; the harness checked launched process identities,
barrier coordination, durable reservation evidence and cleanup before returning.

| Phase | Actual compilation entries | New dispatches | Durable charge |
| --- | ---: | ---: | --- |
| Losing contender denied | 0 | 0 | 1 unresolved reservation retained |
| Winning contender compiled | 1 | 2 | 2 settled calls |
| Fresh process recovered | 0 | 0 | Same 2 settled calls |

Only one experiment and one compiled object existed. The loser could not produce
a candidate, retry, reset the ledger or erase the winner's pending reservation.
Recovery preserved candidate, configuration, ledger, attempts and stored report
without further dispatch. All phases reported zero network attempts.

## Preserve actual scheduling observations

The comparison permits only `worker_role` and `initial_rejection` to differ;
the source validator independently checks allowed ownership rejection outcomes.
All other report fields must match across launcher runs. Both full raw reports
are retained in the receipt rather than replaced by normalized reports.

These two observed runs happened to be fully identical: **right won, left was
denied** in both, with initial rejection `interrupted_or_owned_dspy_phase`.
They do not establish observation of every possible interleaving.

The adopted closure, image and sealed UDF pins matched. Existing no-NIC guest,
network-none containers and 120-second launcher bound remained unchanged.
The guest exited cleanly in 195.088 seconds, and no matching guest process remained.
Independent review reconciled all 19 source hashes, both raw reports, durable
invariants, unchanged input hashes and cleanup.

This qualifies a bounded actual-framework ownership/recovery case with synthetic
local completions. Reflection, arbitrary multi-host races, live providers,
production performance, deployment and promotion remain unqualified or
unauthorized. No provider/model quota was used. Reproduction depends on the
retained sealed wheelhouse; its distribution/reconstruction remains separate.
