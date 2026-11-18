# Actual optimizer fresh-process qualification harness

Source proposal for issue #16. This source change is **not a qualification result**.
No DSPy dependency is imported or container started by its source-unit tests.
Actual execution must use the reviewed adopted closure and the existing isolated
Linux ARM64 launcher. Historical same-process receipts remain unchanged.

The actual optimizer qualification command now adds three sequential fresh Python
interpreters inside its existing network-disabled bounded container:

1. **Compile:** create a disposable real SQLite store with synthetic lineage,
   invoke actual DSPy BootstrapFewShot, observe the rejected trace and retry,
   and persist its compiled checkpoint and shared ledger before exiting.
2. **Evaluate:** open that store in a new interpreter, bind the same snapshot,
   configuration, candidate and budget, verify persisted checkpoint/ledger hashes,
   restore the virtual clock from the compiled checkpoint and ledger, then run
   actual locked held-out baseline/candidate evaluation without recompilation.
3. **Replay:** open the completed result in another new interpreter, forbid the
   framework entrypoint, and require the identical report and ledger with zero new
   provider dispatch and no DSPy module import.

Each child activates network-denial instrumentation before component/framework
imports. Its environment is an explicit synthetic allowlist, not inherited account
credentials. Fixed input source hashes include the harness, optimizer entrypoint,
fixtures, storage/budget/adapter source and adopted dependency declarations.
Child execution is bounded at 25 seconds per phase under the launcher's unchanged
120-second overall timeout and container resource limits. The complete command
runs the trio twice; the outer deadline takes precedence over the sum of six
child limits. The 25-second bound has not yet been qualified on the native lane;
any timeout is a failed qualification, never an automatic timeout expansion. Missing dependencies,
wrong platform/Python, failure, timeout, malformed receipts and mismatched phase
bindings fail; no skip or fake framework can qualify this command.

Provider dispatch is independently observed at `ScriptedProvider.complete`.
`Session.claim_evaluation` restores a historical `provider.calls` prefix; those
entries are **not new calls**. The evaluation receipt therefore counts only new
held-out dispatch and checks its increase against the persisted shared ledger.
Fresh process IDs are checked during orchestration and omitted from the normalized
report so two complete runs can compare exactly. Temporary stores, journals and
child capture files are cleaned on success and failure.

Source tests simulate only subprocess receipts to test the harness's fail-closed
behavior. They reject a same-process receipt, missing/wrong phase, nonzero exit,
timeout, invalid JSON, changed candidate/snapshot/configuration/ledger bindings,
incorrect restored clock, hidden framework use and counting restored history as
new dispatch. These tests are explicitly not actual optimizer execution evidence.

Final native evidence must record the exact reviewed source, actual A/B/C receipts,
repeat equality, forbidden-call observations, cleanup and all existing mutation
and cancellation/budget tests. Fresh-process success does not imply live-provider,
quality-improvement, serving, deployment or parent-issue acceptance.
