# Actual-framework adversarial qualification

The [sanitized receipt](../docs/dspy-native-adversarial-20261118.json) binds the
actual no-network native campaign to exact source
`d1ca35f9cfa712d38eba74e32bd3463fa5745b69` and tree
`4eee9fe75d728516196faad225cb0f6a6cacb666`.

Two separate launcher containers produced identical passing reports. Each
report contains two internal repetitions of five cases using real DSPy
BootstrapFewShot compilation and synthetic local completions. The exact adopted
closure passed its integrity gate before execution. The original 120-second
launcher limit, no-NIC guest, network-none containers and cleanup were retained.

| Case | New dispatches | Unresolved charges | Exact retained reason |
| --- | ---: | ---: | --- |
| Post-reservation cancellation | 0 | 1 | `cancelled` |
| Exhaustion after wrong trace | 1 | 0 | `calls_exhausted` |
| Unknown tokens | 1 | 1 | `unknown_or_invalid_usage` |
| Unknown cost | 1 | 1 | `unknown_or_invalid_usage` |
| Provider failure | 1 | 1 | `provider_unknown` |

Every case observed actual compilation, blocked without producing a candidate,
denied a later reservation without ledger changes, retained the expected attempt
outcome, dispatched only training identities and reported zero network attempts.

## Preserve the failed evidence

The first actual campaign at `70c41cdc` failed the strict oracle. Diagnostic-only
source `f56987d2` reproduced that failure and revealed `provider_failure` returned
`dispatch_binding_changed` while durable/retry state correctly retained
`provider_unknown`. One dispatch and one unresolved charge remained; no candidate
was produced. These were failed framework attempts, not passing qualifications.
Both original receipts/overlays remain retained and hash-bound in this receipt.

The reviewed correction preserves the first durable terminal reason while
maintaining unconditional binding denial. It does not adopt an altered cursor
or invoke a potentially replaced clock. The strict adversarial validator's AST
is unchanged from the failed campaign. Passing results therefore follow a
product correction, not oracle relaxation.

Independent review verified both reports, all ten source hashes, exact case
outcomes, unchanged inputs and cleanup. The guest exited cleanly in 192.39
seconds; no matching guest process remained.

This evidence does **not** qualify concurrent ownership, live providers,
production behavior, model quality, deployment or promotion. It used no provider
or model quota. Reproduction still depends on the retained sealed UDF wheelhouse;
distribution and rebuilding of that artifact remain separate claims.
