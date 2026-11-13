# Durable offline lifecycle

The native Python mapping implements `runspindle.maintained-evidence.v1`, extending
composition integration v1 without changing runtime/fixture v1. Unsupported
versions reject before writes or calls. `storage.LineageStorage` and
`serving.ServingAdapter` describe the bounded seams. The existing lineage,
evaluation transitions and scripted lane remain independently runnable.

`Store` owns a versioned SQLite database with WAL, FULL synchronous transactions,
immutable object triggers and explicit capacity (1,000 records per object kind,
1 MiB encoded record, 64 MiB total immutable payload; at most 4,096 attempts).
Snapshots include exact source/output/correction bytes, feedback observations,
collection evidence, quarantine, family identities and dataset/split locks.
Collection verification is injected, denies by default, and receives the exact
batch digest. Actor verification is independently injected; neither an actor
string nor collection consent grants transmission, task execution or deployment.
Cross-batch reuse of a feedback ID quarantines conflicting observations; an old
feedback target never labels a new output. Previously locked snapshots stay
immutable historical evidence. Related family IDs cannot be rebound across
transactions. There is no trusted Tether collection/authentication adapter.

`durable_evaluation.run` locks immutable dataset/split/configuration/candidate
bindings and claims an experiment before invoking the retained runner. A
`SQLiteBudget` reuses the original budget start, counters, cancellation and
pending reservations across processes. Caller clocks must preserve their epoch
across restart; clock regression fails closed. The local DSPy lane uses a
virtual clock; its fresh-process tests exercise unknown interrupted attempts,
not resumed virtual time. Each request and upper-bound reservation commits
atomically before invocation. A running experiment cannot be reclaimed or
automatically replayed, including interruption before the first call. Unknown
usage retains reservations. No automatic expiry, refund or eviction exists.
Completed report replay returns the stored report without provider calls.

The pinned DSPy 3.1.0 lane runs actual `Predict` and `BaseLM.forward` against a
deterministic local LM inside a Linux arm64 Python 3.14 container with no network.
It verifies 76 exact pinned wheel installations, notices, package/file digests
and the toolchain manifest before imports. Every local LM invocation shares the
durable budget and an unexpected internal retry is rejected. The configured
`dspy.predict.v1` preparation is a compatibility seam, not a newly implemented
optimization algorithm. Both lanes use scripted completions, a virtual latency
clock, synthetic cost units and exact-string metrics. They establish neither
provider quality nor actual monetary cost. No provider fallback is implemented.

`Registry.qualify` binds a durably completed locked report and exact candidate
bytes to explicit quality/improvement/resource thresholds and an environment.
`begin` requires independent approval verification, binds actor evidence to all
candidate/evaluation/policy/environment digests, and claims a generation with
compare-and-swap. Activation intent commits before dispatch; dispatch is claimed
before load. Only matching adapter activation/generation/digest readback makes
the registry active. Missing/conflicting readback retains unknown ownership and
blocks later promotion, even if the prior bytes appear active. No blind reload
follows an uncertain acknowledgement. An explicit atomic load rejection plus
prior-version readback clears the attempt as failed. Exceptions remain unknown.

`LocalServing` has its own SQLite database and parses actual immutable candidate
bytes as a bounded `synthetic.map.v1` lookup program. It is a disposable local
adapter, not a workflow/provider execution service. A single read obtains both
response bytes and their actual serving digest. Rollback needs an available,
compatible previously approved predecessor and re-verifies its bound approval.
Null predecessors, stale generations and unavailable bytes fail before loading.
Repeated rollback with the same generation cannot create another activation.

The SQLite file is a trusted component boundary. These checks cover process
interruption, concurrent SQLite connections and lost replies on a local
disposable filesystem. They do not qualify malicious database replacement,
production identity, power-loss storage, schema upgrades, retention/backup,
network serving, provider transmission or full-system/live acceptance.

Run `python3.13 -m unittest discover -s tests -q` and
`python3.13 tools/check_durable_mutations.py`. The separate DSPy lane is
`python3.13 tools/offline_dspy.py --report local/dspy-tests.json` after explicit
wheel preparation with `uv pip install --require-hashes -r
offline/requirements.lock --target local/python-deps --python-platform
aarch64-unknown-linux-gnu --python-version 3.14 --only-binary :all:
--no-compile-bytecode`. Execution never downloads. Python image/notices and the
dependency byte inventory are in `offline/`; keep downloaded packages in ignored
`local/`. Missing or changed preparation blocks execution.
