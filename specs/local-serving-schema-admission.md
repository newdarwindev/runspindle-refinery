# Local serving schema admission

Partial issue #17 and parent #6. The existing disposable `LocalServing` SQLite
adapter is extended; this is not production storage or target qualification.

Previously an existing partial store silently gained missing tables on open.
An `active` table missing its primary key was accepted: a second load returned
success while inserting a second environment row, and readback/predict continued
to return the first candidate. The new admission boundary rejects these layouts
instead of interpreting them as the supported loader schema.

Only an entirely empty schema at `user_version=0` initializes. Existing stores
must contain exactly the original `active` and `calls` tables and their automatic
index, with exactly the original SQLite-retained SQL definitions. Unknown tables,
views, triggers, indexes, columns, constraints and schema versions fail closed.
Temporary schema objects are rejected as well, preventing same-connection TEMP
shadowing of the unqualified loader tables. Equivalent but unrecognized custom
SQL is intentionally not admitted. Original
legitimate version-zero stores reopen unchanged; no version bump, migration,
repair or data rewrite is introduced.

The constructor initializes/validates within `BEGIN IMMEDIATE` and closes its
connection on rejection. WAL configuration follows admission, not rejection of an
existing incompatible schema. Load, readback and predict acquire a local guarded
transaction and validate at entry and before commit. Readback/predict now also
acquire `BEGIN IMMEDIATE`, providing bounded SQLite serialization; they do not
load a candidate or invoke a provider. Committed post-open DDL is
therefore rejected before use, and DDL injected inside a load transaction rolls
back with that load's data and call record. Reads retain the existing one-row
byte/digest attribution rather than consulting registry intent.

The comparison uses SQLite's stored object definitions rather than introducing a
second metadata introspection engine. It assumes an ordinary trusted SQLite
engine and component-owned file. It is not defense against `writable_schema`,
hostile files, arbitrary in-process connection manipulation, power loss, or
concurrent filesystem edits. Physical journal/open effects are not claimed to be
byte-preserving. Tests observe logical schema/records and closed connections.
This change does not add payload bounds, retention, account identity, approval
issuers, external transactions or authentication of adapter readback.

Tests use disposable local files and synthetic content. They cover partial and
altered layouts, missing primary-key uniqueness, unknown objects/versions,
post-open drift across all three entrypoints, load+DDL rollback, legacy reopening
in a fresh optimized Python process without reloading/network calls, and a
removed-validator mutation that must fail the independent negative test. Existing
serving tests retain concurrent promotion, lost acknowledgments and rollback.

```
python3 -m unittest discover -s tests -p test_local_serving_schema.py -v
python3 -O -m unittest discover -s tests -p test_local_serving_schema.py -v
```

The full suite requires the exact existing Tether source pinned in
`dependencies/tether-feedback.json`, supplied through `RUNSPINDLE_TETHER_SOURCE`.
Results and missing-tool limits are in `docs/local-serving-schema-evidence.json`.
No production activation, migration, provider call, deployment, persistent job,
backup, package installation or access change is part of this work. #17/#6 remain
open; component evidence does not satisfy their production/live gates.
