# Bounded lineage implementation evidence

This candidate partially addresses [F11 #4](https://github.com/newdarwindev/runspindle-refinery/issues/4)
and the lineage cases of [mock suite #1](https://github.com/newdarwindev/runspindle-refinery/issues/1).
Both issues remain open. It is original Python code, not a port of SRC-012.

Wiring behavior tests into GitHub Actions is blocked: the connected OAuth app
rejected a workflow-file push because it lacks `workflow` scope. The optional
workflow edit was removed; the existing isolated self-hosted structural workflow
is unchanged. No new credentials or scope changes were requested.

`refinery.lineage` is a dependency-free, process-local library. Its Python dataclasses
are provisional internal interfaces (`refinery-lineage-local.v1` in split hashes),
not agreed cross-component wire envelopes. The lineage runtime does not consume composition envelopes yet; a separate
[pinned fixture consumer](SHARED_FIXTURES.md) now checks the shared synthetic assets
in isolation. No operational acceptance gate has changed.

The library verifies actual immutable source/output bytes against SHA256, binds
feedback to an exact output revision and digest, applies an explicit task/rule
revision, records exact feedback observations and actors, and quarantines missing,
unavailable, incomplete, unsupported or conflicting records with reason counts.
Feedback ID replay is deduplicated; conflicting replay cannot create gold. Reusing
an immutable event ID with different contents conservatively quarantines both
affected targets, even before actor verification; this trades availability for
exclusion rather than choosing one conflicting observation. A
negative observation or conflicting gold excludes the affected exact target.
An unverified actor cannot create gold. The verification callback defaults to deny
and callback outage denies the row. The library does not authenticate the callback
or establish that its observations are true; a trusted Tether observation adapter
and permission policy remain required before real input collection.

A single source/output revision cannot be redefined within a construction. Related
source or output revisions must have the same declared family. Cross-source
relationships must be supplied correctly by the trusted adapter; they cannot be
inferred from arbitrary text. Family splits are deterministic from injected seed
and family ID. Exact byte hashes, identities, task/rule, observed feedback, excluded
counts and the split assignment are bound to an immutable in-process lock. Changing
gold bytes, exclusions or an assignment invalidates verification. Empty partitions
remain empty and do not establish evaluation readiness. Creating another lock is
possible; an actual experiment registry must enforce use of the previously locked
manifest. There is no optimizer here.

Each input collection is capped at 1,000 records, each byte field at 1 MiB and total
source/output/correction bytes at 8 MiB. IDs are bounded to 128 simple ASCII
characters. The implementation receives already permitted bytes; it has no
collection, network, provider, execution, deployment or approval interface. Gold
data grants no such capability. All state vanishes with the process; no concurrent
writer, durable retention, cross-process replay or recovery guarantee is asserted.

## Executed checks

Use Python >=3.11 and run from this repository:

```sh
python3 tools/check_scaffold.py
python3.13 -m unittest discover -s tests -v
```

The exact source/test/config hashes, toolchain, timestamp and observed results are
in [local-lineage-tests.json](local-lineage-tests.json). The tests deny socket
construction and assert zero socket calls, call the actual lineage functions with
invented bytes, and verify deliberate gold/exclusion/split mutations fail.
There are no subprocesses, temporary stores or resources to clean up. Tests cover
old revisions versus latest output, changed hashes, actor denial/outage, duplicate
and conflicting events, missing context, correction rules, group leakage, locked
manifest mutation, bounds and the still-failing activation entrypoint.

The shell's `python3` is Python 3.9.6, below the project prerequisite. Its structural
checker ran, but behavior tests used the explicitly installed Python 3.13.18.
Python 3.11 and 3.12 were unavailable and were not tested. This is synthetic
component evidence, not a qualified DSPy/provider adapter, system integration,
GitHub Actions result, live acceptance or deployment.

## Remaining issue gates

| Issue | Concrete remaining prerequisite or gap |
|---|---|
| #1 mock suite | Lineage and offline scripted evaluation now have bounded component tests. Durable registry, independent approval/loader/rollback seams, JSON runtime wire conformance and pinned local DSPy compatibility remain absent. |
| #2 SRC-012 | Private exact source/dependency/rights mapping is not available in this checkout. Inert historical helper stays inert. Budget/evaluation/promotion/loader interfaces and source-function accounting remain unresolved. |
| #3 SRC-013 | Selected full white paper and eight figure dependencies plus third-party figure rights are retained privately, not available here. No pinned TeX build and page/figure validation of the selected artifact has occurred. |
| #4 F11 | Trusted Tether collection/actor/source relationship adapter, agreed shared envelopes, actual durable bounded store, concurrent writer/restart evidence and composition integration are absent. |
| #5 F14 | Offline runner, locked effective synthetic config and cumulative budgets now have component tests. Pinned DSPy/actual optimizer/provider adapter, independently trusted cost/safety measurements, durable locked registry and restart/composition evidence remain absent. |
| #6 F15 | Independently authenticated approval, exact evaluated candidate evidence, durable atomic registry/audit, actual serving load/readback and rollback adapter are absent. |

Provider transmission, paid evaluation, production activation, credentials and
migration retirement remain separate authorizations and evidence gates.


## Offline evaluation tranche

`refinery.evaluation` adds a synthetic evaluation runner over the existing locked
lineage, with baseline/candidate byte/config/script hashing, identical heldout
cases, exact-string scoring and one shared reserve/settle budget. This partially
addresses #1 and #5; both remain open. [Native runtime.v1 mapping and limits](../specs/offline-evaluation.md)
name every effective gate argument and distinguish this library from JSON wire
conformance, durable storage and actual DSPy/provider integration.

One local behavior-suite command is `python3.13 -m unittest discover -s tests -v`.
Run `python3.13 tools/check_evaluation_mutations.py` for deliberate invariant
mutations and `python3 tools/check_scaffold.py` for repository structural checks.
All 53 tests pass locally, including 20 evaluation cases; all three implementation
mutations are detected. Tests deny socket creation and independently assert exact
call count/order and zero downstream calls after exhaustion, cancellation,
unknown safety/usage/cost, malformed results and timeout exhaustion. Concurrent
reservation tests reject oversubscription. Both artifacts use the same locked
heldout cases; fixed seed/reset produces identical reports. Exact bytes/config,
changed split assignments and gold mutations reject before provider dispatch.
The fixture manifest and exact source hashes/toolchain are in
[local-evaluation-tests.json](local-evaluation-tests.json).

The runner is sequential and all state is process-local. Only reservation
accounting is thread-safe. There is no durable experiment registry, fresh-process
recovery, actual optimizer, trusted safety/cost receipt, real provider adapter,
promotion, serving or production authority. A passing synthetic metric remains
narrow byte-equality evidence. A caller can construct another manifest; authority
to select and retain the originally approved lock remains a future registry gate.
No source migration, deployment, external transmission, paid call or CI runner
execution is implied.

## Durable offline candidate

See [exact durable test evidence](durable-offline-tests.json) and [mapping](../specs/durable-offline.md). Earlier process-local statements describe the retained original lane; the new SQLite and pinned local DSPy lanes are separate. Production/provider/Tether trust and live acceptance remain unqualified.
