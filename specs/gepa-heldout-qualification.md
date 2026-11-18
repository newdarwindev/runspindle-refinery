# Proposed materialized GEPA heldout qualification

This separate harness is not an execution receipt. Component tests validate its
oracles, signed local storage reconstruction and launcher construction only.
The GEPA heldout runtime has a separate independent review and aggregate gate.
No live providers, private data uploads, deployment or automatic model promotion.

## Exact synthetic phases

A coordinator creates two genuinely signed Tether feedback events, materializes
one immutable training and one immutable heldout descriptor, and retains the
same local authority files. Current synthetic grants are explicitly reconstructed
in each process; saved permission references confer no authority.

1. Fresh compile process calls original GEPA.compile and reflection Predict.
   Original strict oracle requires five effects: two initial task failures,
   one reflection, two changed-instruction task successes. Both original use
   and materialization optimization/reflection grants must be observed.
2. Fresh evaluation process reopens the same files, candidate and phase batches.
   It runs original Predict with the fixed INITIAL baseline and locked candidate,
   one heldout example each, using the same durable ledger. New effects must be
   exactly two and cumulative calls/tokens/cost exactly seven, unresolved zero.
   GEPA.compile and reflection are denied; current evaluation grant and exact
   receipts, sealed ownership and candidate association remain mandatory.
3. Fresh replay process installs the framework-import blocker before component
   imports and provider denial before reconstruction. It must return the exact
   same evaluated result/checkpoint with zero imports, effects and network
   attempts. Training after seal must be denied. Actual heldout-item withdrawal
   then denies replay without changing the optimizer checkpoint or budget.

All three PIDs must differ. Physical identity, manifests and candidate hashes are
retained and compared within the run. Independent fresh runs create different
physical owner hashes: retain complete reports and do not normalize them to
manufacture cross-run equality. Scores are synthetic exact-match observations,
not evidence of production quality.

## Bounds and admission

Use `tools/offline_gepa.py --suite heldout` with the same explicit prepared
closure, exact Tether checkout and real pinned ARM64 Git as the materialized
lane. The original Python 3.13.13 image, dependency inventory and full source
seal are unchanged. The container remains network-none/read-only with its
existing 120-second outer limit and verified cleanup. The new suite runs three
sequential children capped at 30 seconds each. This is an explicit new-suite
bound, not a native performance claim; timeout fails, never expands itself.
The inherited reflection, durable and materialized lanes retain their behavior.

The harness binds all Refinery modules and transitive signed fixtures in its
report. An independently reviewed outer artifact must additionally bind the
complete source and self-contained Tether Git tree, retain all raw reports,
verify strict inventories before and after, and preserve failures and cleanup.
No native execution is authorized merely by a passing component oracle test.
