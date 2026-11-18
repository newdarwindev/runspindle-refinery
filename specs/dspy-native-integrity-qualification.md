# Native framework integrity and mutation qualification

The [sanitized receipt](../docs/dspy-native-integrity-20261118.json) binds actual
native execution to source `232efb061899aae66c4f765348558b3445316da2`, tree
`8f7077483a0fcd6568989e2ff24ccedf2410fa17`.

Two network-none launcher containers returned identical passing reports for five
bounded actual-DSPy cases. Temporary negative mutants deliberately removed one
guard at a time; their observed violations are test detections, not successful
production admission. The original implementations were restored after each case.

| Case | Actual observation |
| --- | --- |
| Locked candidate tampering | Compiled candidate bytes changed; locked evaluation rejected the changed digest with zero new evaluation dispatches and unchanged ledger/attempts. |
| Result identity protected | Actual compile dispatched once; precommit mutation was rejected, no compiled object or stored result escaped, settled charge retained. |
| Result identity guard removed | Actual compile dispatched once; returned and stored result hashes diverged, exposing the intentionally removed check. |
| Heldout allowlist protected | Actual compile entered but denied forbidden partition before any dispatch or ledger charge. |
| Heldout allowlist guard removed | One heldout dispatch was independently observed; the no-heldout invariant failed as intended. Calls exhaustion stopped the attempt without a candidate. |

Every case recorded zero network attempts. The heldout mutant uses a unique-gold
precondition and calls-limit-1 containment. It does not establish a universal
absence of contaminated candidates under arbitrary budgets or datasets. Mutant
compiled objects remain confined to disposable synthetic test state.

The adopted closure and sealed input hashes matched. The no-NIC guest,
container network-none isolation, 120-second launcher timeout and cleanup stayed
unchanged. Guest execution including preparation took 191.440 seconds and exited
cleanly, with no matching guest process remaining. Both raw report stdout hashes
are `f8f8204aa34c82235817eccdb37e8a37c09a6edb3047980b43f9ef12bbe62251`.

This is bounded isolated integration evidence, not live-provider evaluation,
production quality, reflection, deployment or promotion. No provider/model quota
was used. Reproduction depends on the retained sealed UDF wheelhouse; its
reconstruction and distribution are not established. Private raw host evidence
is retained by hash rather than published. Earlier campaign evidence is unchanged.
