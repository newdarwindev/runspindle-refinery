# Pinned refinery fixture consumer

This test-only adoption partially addresses [refinery #1](https://github.com/newdarwindev/runspindle-refinery/issues/1)
and [composition #1](https://github.com/newdarwindev/runspindle/issues/1). Both remain
open. The lineage library's internal Python API is unchanged; this does not supply
a trusted runtime wire adapter or DSPy/provider/serving implementation.

`fixtures/shared-v1` is the unmodified standalone refinery subset exported from
`newdarwindev/runspindle` commit `e8cd5779968fede213443db13a591c992cd0e183`,
source directory `fixtures/v1`. It contains `feedback.observed` and
`candidate.observed` golden observations and all four applicable published negative
vectors. The schema and Python validator remain provisional synthetic contracts.
The export retains the original schema and validator; it does not remove unrelated
schema definitions or invent runtime compatibility.

[shared-v1.pin.json](../fixtures/shared-v1.pin.json) records the exact source commit,
schema, component, origin manifest hash and subset manifest hash. The full source
manifest is preserved beside the pin. Before export, all six source files were
read from that exact Git commit and verified against the source manifest. The
subset manifest binds the five exported files. The consumer independently checks
the pin, inventories, hashes and three unmodified source-file hashes before
executing the vendored validator. All fixture provenance remains synthetic and
public-safe. Source rights are retained; no blanket license is granted or inferred.
No historical source files were imported.

From this checkout, with Python >=3.11 and no sibling checkout or running service:

```sh
python3.13 -m unittest discover -s tests -v
python3 tools/check_scaffold.py
```

On Python 3.13.18, 31 tests passed: the existing 22 lineage tests and nine new
consumer tests. Exact test/config/source hashes, timestamp and observed results
are in [local-shared-fixture-tests.json](local-shared-fixture-tests.json).
The published vectors test unknown version and wrong host for both kinds. Additional
consumer checks mutate every identity field, candidate digest and serving receipt,
require independent approval bytes, reject incomplete statuses, malformed fields,
missing identity and altered manifests. Expected identities and receipt observations
are copied before adapter-output mutation. Internally consistent manifest rewrites
still fail against the separately reviewed pin. A temporary standalone copy runs in
a fresh bounded Python process without access to siblings; the temporary directory
is removed afterwards. Socket creation, connection and DNS are denied in tests and
the standalone process. This is Python-level isolation, not an OS network sandbox.

The candidate bytes used by the receipt oracle are the invented literal
`Synthetic candidate v1`. Golden output digests are identity references, not proof
that a real output was stored or its actor authenticated. Actual output-byte
verification remains exercised separately by the lineage tests. Receipt and
approval observations here are synthetic test oracles, never runtime authorization.

These tests establish pinned asset adoption and Python conformance only. Mapping
these observations into the lineage library needs a reviewed trusted collection
adapter and complete source/output context; candidate evaluation/serving remain
unimplemented. There is no actual DSPy/Temporal/native/browser qualification,
durable-store recovery, provider call, promotion, deployment or live acceptance.
GitHub CI behavior wiring remains blocked by the previously reported OAuth
`workflow` scope limitation; workflows are unchanged. Operational live acceptance
still exits 78. This fixture PR grants no collection, transmission, execution,
spending or deployment authority.

To upgrade, review the source commit and producer/consumer compatibility, verify
its full source manifest, export into a new directory, inspect the subset and pin
diffs, and run old and new vectors before accepting a new pin. Tests never fetch
floating URLs or update pins automatically.
