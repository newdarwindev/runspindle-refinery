# Issue #1: offline acceptance map

This maps the original seven deliverables in
[issue #1](https://github.com/newdarwindev/runspindle-refinery/issues/1) to the
existing implementation and executable assertions. It is not a production gate.
The PR records the exact final source commit and fresh aggregate results.
Independent final-head review and actual pinned DSPy compatibility are required
before closing the issue. Historical passing reports do not qualify a new head.

## Reproduction and fixture inventory

Use Python >=3.11 (this change is tested with 3.14.8), the exact packages in
`requirements-quality.txt`, and the Tether source revision and file hashes in
`dependencies/tether-feedback.json`. Set `RUNSPINDLE_TETHER_SOURCE` to that prepared
checkout. This is a read-only source dependency; tests use invented actors,
messages, secrets, approvals and disposable SQLite databases. No live Tether
account or production authority is needed. Missing dependencies fail the suite.

Run the existing aggregate command, without a second lifecycle harness:

```sh
python3 tools/validate.py --output .verification/issue1-final
```

This executes structure, artifact/reference integrity, repository hygiene,
pinned lint/format/type checks, complete unittest discovery and both behavioral
mutation runners. The static quality baseline is unchanged; existing reported
debt is not a claim of a completely typed codebase. The aggregate receipt records
the exact commit, clean/dirty state, tool versions, command statuses, durations
and configuration fingerprints. The PR records the UTC verification interval.
Verbose unittest output identifies individual scenario IDs; the mapped test
source below contains the assertions. Keep failures, blocked prerequisites and not-run
lanes separate from passed component checks.

Fixtures are original inline code in `tests/test_lineage.py` (`fixture`, seed
`seed-1`), `tests/test_evaluation.py` (`dataset_fixture`, seed `seed-2`), and
`tests/test_durable.py` (`setup`, seed `seed-2`). Threshold tests use
`boundary-seed`; holdout tests explicitly try `different-seed`. No random provider
or wall-clock latency determines the results. Shared exported fixtures and their
exact revision/hash manifest are in `fixtures/shared-v1.pin.json`. Configuration
and script/candidate/dataset/split bytes are hashed by the existing runners and
checked by assertions. Tests inject clocks, scripted responses and storage paths;
each case resets its own state. Socket-denying fixtures and fresh-process socket
guards reject unexpected Python network use; this is not a hostile native-code
network confinement claim. Run in a default-deny network environment as well.

## Criterion-by-criterion assertions

1. **Immutable lineage and quarantine.** `test_lineage.LineageTests` checks exact
   bytes/hashes, revisions, frozen records, actor verification, positive/negative
   conflict, corrections, missing context, unavailable edited/deleted targets,
   duplicate delivery, conflicting IDs and reason counts. Durable counterparts:
   `test_durable.DurableTests.test_lineage_exact_revision_quarantine_conflict_and_restart_bytes`,
   `test_feedback_id_cannot_rebind_across_committed_batches`, and
   `test_feedback_commit_interruption_is_atomic_in_fresh_process`.
2. **Related families and locked splits.** The lineage tests
   `test_related_revisions_cannot_change_family`,
   `test_grouped_split_deterministic_and_input_order_independent`,
   `test_lock_detects_dataset_mutation_and_exclusion_change` and
   `test_deliberate_split_assignment_mutation_fails_independent_verification`
   reject changed families/locks. `test_evaluation.EvaluationTests` rejects empty
   evaluation and deliberate leakage before calls. The new
   `test_issue1_acceptance.LineageProofTests.test_complete_rows_without_feedback_proof_never_dispatch`
   rejects complete rows lacking evidence even under a newly hashed lock.
   `test_durable_dspy.DurableDSPyTests.test_global_family_seal_blocks_second_candidate_and_legacy_use`,
   `test_enrollment_rejects_preexisting_heldout_exposure`, and
   `test_snapshot_seed_cannot_reassign_enrolled_families` cover unlocked/reassigned
   holdouts across experiments. These tests use a fake optimizer, not DSPy quality.
3. **Scripted optimizer/provider and cumulative budgets.**
   `test_evaluation.EvaluationTests.test_pass_reports_exact_boundaries_and_identical_locked_cases`
   independently compares the optimization/reflection/baseline/candidate call
   order and sums calls, tokens, virtual latency and synthetic cost.
   `test_calls_never_reset_between_stages`, `test_budget_is_shared_across_retries`,
   `test_timeout_exhaustion_and_script_error_do_not_blindly_continue`,
   `test_unknown_cost_tokens_safety_stop_downstream_calls`, and
   `test_reservation_overrun_malformed_usage_and_response_stop` assert refusal and
   downstream call counts for each failure class. `test_dspy_local_contract`
   tests the fake DSPy adapter contract and disabled implicit fallback separately.
4. **Cancellation, exact limits and concurrency.** Evaluation scenarios
   `test_capacity_rejected_before_provider_call`,
   `test_concurrent_reservations_cannot_oversubscribe`,
   `test_cancel_before_dispatch_and_inflight_charges_usage`,
   `test_elapsed_time_all_stages_and_deadline_boundary`, and
   `test_unresolved_shared_reservation_cannot_pass_gate` cover boundary behavior.
   Durable tests `test_multi_connection_budget_concurrency_and_no_reset` and
   `test_interrupted_reservation_retains_budget_and_never_replays` cover multiple
   connections and process death. `test_budget_settlement_ownership` rejects
   cross-ledger/experiment settlement and cancellation races. Unknown receipts
   remain charged; synthetic cost is not money measured from a provider.
5. **Independent approval, actual bytes and atomic registry.**
   `test_durable.ServingTests.test_exact_approval_default_deny_forgery_and_changed_target_zero_loads`
   checks fake independent authority, forged approval and changed target/digest.
   `test_concurrent_promotion_compare_and_swap_has_one_owner` checks generation
   ownership. `test_qualification_thresholds` checks exact rational threshold
   boundaries and missing/malformed scores without writes. The new
   `test_issue1_acceptance.IssueOneAcceptanceTests` tests changed evaluation/policy,
   digest-shaped invalid digests and changed actual bytes, plus an actual child
   process dying after approval insertion inside an uncommitted transaction.
   SQL snapshots prove the crash leaves no partial registry write; subsequent
   retry performs exactly one load. The `partial_registry_commit` mutation forces
   an early commit and must fail that independent snapshot assertion.
6. **Serving, failed loads and rollback.** `test_durable.ServingTests` covers
   `test_load_readback_actual_output_and_failed_load_preserves_prior`,
   `test_fresh_process_lost_activation_ack_reconciles_without_reload`,
   `test_before_load_process_death_fences_retry_without_effect`,
   `test_concurrent_rollback_and_failed_rollback_preserve_served_version`, and
   `test_rollback_exact_bytes_absent_history_repeated_and_unavailable`.
   Assertions read the loader's actual call log, bytes and output attribution,
   independently of registry intent. `test_serving_dispatch_authority`,
   `test_serving_readback_integrity` and `test_local_serving_schema` additionally
   reject stale approval, forged readback, incompatible/missing stores and
   callback mutations. Failed/unknown loads never imply a new active version.
7. **Narrow metrics and pinned actual DSPy compatibility.** Exact string metrics
   are explicitly synthetic in `test_quality_gate_does_not_assume_exact_string_is_general_quality`
   and in the report/configuration contracts. The separate canonical command is
   `python3 tools/offline_dspy.py --report local/dspy-tests.json`; it checks
   `offline/requirements.lock`, `offline/dependencies.json` and
   `offline/toolchain.json` before executing DSPy 3.1.0 in the pinned Linux ARM64
   Python 3.14 network-disabled container. This environment lacks the prepared
   byte-identical dependency tree: the current attempt fails with
   `incomplete dependency preparation` before framework execution. This criterion
   is **blocked**, not passed by fake tests or historical evidence. The separate
   x64 proposal has its own exact image/preparation and independent review
   prerequisites; it is not a fallback around the canonical gate.

## Remaining closure gates

Obtain the byte-identical canonical DSPy preparation and qualified pinned runtime
or separately review/qualify an explicit replacement lane; run that lane on the
final source. Complete independent final-head review. Production identities,
trusted live collection, live providers, model/optimizer quality and deployment
are excluded from issue #1 and are not prerequisites for its offline criteria.
No service, standing job, credential, security policy or backup is changed here.
