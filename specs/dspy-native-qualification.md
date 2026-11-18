# Native offline DSPy qualification

The [sanitized receipt](../docs/dspy-native-qualification-20261118.json) records
actual isolated optimizer execution at source
`c5e08ec2da3d3f6e31b9dca7b540c29de45883ee`, tree
`33f720439ac2f3657c7bc4f9eb8420ad9f9c289e`.
It supplements earlier preparation-only evidence; it does not rewrite it.

The exact reviewed Linux ARM64 Python 3.14.0 image and adopted 11,877-file
closure passed the unchanged integrity gate. The sealed wheelhouse, transport
and base image hashes were unchanged. No dependency download occurred in the
guest, which had no NIC; containers used network-none isolation.

Two separate launcher containers produced identical passing reports. Each
report additionally asserts two internal repetitions. Real DSPy 3.1.0
`BootstrapFewShot.compile` ran using synthetic local completions, with shared
budget accounting and fixed held-out evaluation.

Fresh interpreter phases independently compiled, evaluated, and replayed the
durable result. New dispatches were 2, 4, and 0; persisted call counts were
2, 6, and 6. Replay retained the report and ledger without loading DSPy.
Every phase reported zero forbidden-network attempts. Source hashes, candidate,
configuration, snapshot and ledger bindings were checked by the harness.

The 25-second child and 120-second launcher timeouts were not expanded. Guest
execution including boot/preparation took 265.64 seconds and exited cleanly.
The launcher required container cleanup, and no matching guest process remained.
Independent review verified both reports, all 18 source hashes, closure binding,
unchanged inputs and cleanup.

This is **offline optimizer component qualification with synthetic exact-match
quality**, not measured model quality, live-provider acceptance, production
readiness, deployment or promotion authority. There were no live-provider or
model-quota calls. Reproduction depends on the retained sealed UDF wheelhouse;
its distribution and reconstruction are not established by this execution.
Original private host logs remain retained and hash-bound, not copied into Git.
