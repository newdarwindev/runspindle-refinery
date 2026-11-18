# Durable GEPA compile/recovery qualification lane

This is **source for a pending native qualification**, not a completed run. It
builds on the reviewed compile-only Session and worker-owned Store integration.
It does not implement heldout evaluation, promotion, live providers, or external
identity authority. Prior process-local reflection evidence remains unchanged.

`tools/offline_gepa.py --suite durable --prepared PATH --report NEW_FILE` uses the
same sealed Python 3.13 image, dependency byte/mode admission, no-network container,
read-only inputs, resource limits, positive container-absence cleanup and fixed
120-second outer deadline as the default reflection suite. Unknown suites fail
before runtime access. The default reflection suite keeps its original two runs.

The durable suite instead executes **one sequential A/B pair** per launcher:

1. Fresh interpreter A creates the synthetic fixture and calls `Session.compile()`.
   The original GEPA compile and reflection Predict are transparently observed,
   never replaced. The unchanged five-call oracle requires two initial failures,
   one metered reflection and two selected-instruction successes, exact shared
   accounting, training-only requests and zero network attempts. Session persists
   the actual returned result and transaction-bound request witnesses.
2. Fresh interpreter B opens the same Store and reconstructs the same Session.
   A meta-path gate forbids DSPy/GEPA imports, and provider completion entry is
   independently denied. `Session.compile()` must recover the completed checkpoint
   through original authority and stored-history validation, without new effects.
   Candidate/configuration/binding/result/attempt/witness/usage/clock identities
   must match A. Exact logical SQLite row payloads for objects, budgets,
   experiments and attempts must remain unchanged. Physical WAL layout is not
   treated as a portable byte identity.

Each child has an explicit 50-second deadline and a 1 MiB combined output bound.
There is no inherited credential environment, shell, retry or automatic deadline
expansion. Kill/reap and temporary-store cleanup apply on failures. The 120-second
container cap has precedence: child cleanup/startup overhead is not a promise
that both 50-second maxima can always finish. External repeat launchers may compare
stable identities; actual distinct PIDs are retained as observations, not required
to match across launches. No PID is used in the candidate or persistent identity.

Source tests exercise invalid receipts, same-process rejection, changed stored
identities, unexpected effects/imports, child failure/timeout, cleanup and fixed
sanitized environments. They do not pretend to run a framework. Actual native
acceptance, fresh-process evidence and independent review remain pending.
