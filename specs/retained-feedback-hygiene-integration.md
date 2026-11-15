# Retained-feedback runtime in the strict hygiene candidate

This integration merges approved runtime PR #26 main
`278ebdfdbaa1a7cc4f5c8b1f4aef430ca6b11023` into published draft PR #18 head
`ec164a3a3a6f9c9df840e07975a9f8ed186ccdfb`, preserving both parents and the
existing hygiene ancestry. Runtime scope remains the default-deny use-time
eligibility gate and exact cached-report association checks documented in
[retained-feedback-use.md](retained-feedback-use.md).

## Strict-quality reconciliation

The merge was conflict-free. Its first strict-quality run exposed ten new
diagnostics without changing the existing debt baseline. The follow-up fixes
touch only `refinery/durable_evaluation.py`, `refinery/evaluation.py` and
`tests/test_feedback_use.py`:

- Normalize imports and formatting. Formatting the previously unformatted
  evaluation module also relocated seven historical type diagnostics; explicit
  annotations and identity casts resolve those instead of rewriting the baseline.
- Describe the legacy storage/encoding calls with narrow callable casts while
  retaining use-time resolution of the original call targets.
- Describe the use gate's existing `check` and `stop` methods with a typing
  Protocol. The cancellation checks, callbacks and exception path stay unchanged.
- Resolve the same unittest discovery fixture modules with `import_module`,
  preserving the verified Tether dependency singleton and every negative assertion.

Normalized executable-AST comparisons match the approved runtime after removing
imports, typing-only annotations/Protocol and identity casts, and accounting for
the same discovery fixture imports. No production branch, call order, condition,
argument or test assertion changes. The original runtime receipt remains an
unchanged snapshot of the original runtime source; it is not silently rebound to
the normalized source hashes.

## Local evidence and remaining boundaries

The [separate integration receipt](../docs/retained-feedback-hygiene-integration-evidence.json)
records exact ancestry, current source hashes, protected bytes and clean local
checks. The mandatory aggregate passed with 183 tests and zero skips; the full
optimized suite also passed all 183 tests with zero skips. The 15 use-gate tests
cover fresh authority, changed evidence, callbacks, cached associations,
reservation/cancellation preservation, restart and synthetic rebatching. Both
removed-dispatch-guard mutants expose independently observed forbidden calls in
each interpreter mode. The existing three evaluation and four durable mutations
remain detected.

The strict repository baseline gate has zero new diagnostics. Scoped strict
mypy passes four typed runtime/test files, isolated strict Ruff passes seven
runtime/typed-test files, and formatting passes all nine changed Python files.
A broader raw Ruff run still reports three inherited I001 findings in
`tests/test_durable.py` and `tools/test_dspy_local.py`; that standalone lint run
did not pass. The existing baseline is unchanged, with no added exclusions,
suppression comments or regenerated debt allowance.

Use the existing pinned quality environment and Tether checkout
`670989839a0fccbcf2014d01609778ae6e4df424` via `RUNSPINDLE_TETHER_SOURCE`:

- `python3 tools/validate.py`
- `python3 -O -m unittest discover -s tests -v`
- `python3 tools/static_quality.py`
- `mypy --strict --follow-imports=silent --no-incremental refinery/feedback_use.py refinery/optimization.py refinery/durable_optimization.py tests/test_feedback_use.py`

Historical receipts, all design/paper bytes, workflow and image controls, strict
inventories, quality baseline and dependency pins are unchanged. No PDF import
or publication is part of this work. Cargo is unavailable, and this component's
mandatory inventory contains no Rust command. Original runtime CI applies only
to its own head, not this integrated candidate. Independent integration review
and exact-head qualified-image CI remain separate gates. No native build, image
or fleet change, dispatch, real provider/training use, deployment or remote merge
is claimed; synthetic authorities are not external consent authentication.
