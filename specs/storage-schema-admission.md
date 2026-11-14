# Component-owned SQLite schema admission

Partial Refinery #1/#15 repair. Existing v1 storage statements and codec remain
unchanged. Empty v0 files may initialize; v1 files must contain exactly the supported
seven tables, automatic indexes and four immutable-record triggers. No missing
object is recreated. Unknown views/indexes/tables or altered columns, constraints,
index ordering or trigger SQL deny admission, including semantically equivalent
custom schemas. This is an explicit compatibility boundary, not a migration engine.

Schema comparison creates the canonical expected schema in temporary in-memory
SQLite and compares sqlite_master plus table/index/foreign-key metadata. Validation
runs under BEGIN IMMEDIATE during open, transaction entry and before commit; reads
use the current transaction or acquire their own guard. Thus another connection's
committed DDL is rejected before use and callback DDL inside a transaction rolls
back together with lineage, family locks and provenance receipt writes.

SQL aggregates bound retained row counts, individual payload lengths and total
payload bytes before any payload is fetched or decoded. Objects require canonical
codec data and matching content digest; family mappings require valid identities.
Other persisted payloads are checked for bounded canonical encoding. Higher-level
budget, selection, serving and lineage association invariants remain their existing
component contracts; this does not prove universal semantic integrity of arbitrary
mutated storage. Existing data and schema version are not upgraded or repaired.

Callbacks are trusted code and must not independently commit, rollback or close the
component connection. No claim covers a malicious in-process actor, hostile SQLite
engine/file, immutable WAL bytes, concurrent external filesystem edits or power loss.
SQLite opening/checkpointing may change physical journal files even when logical
schema admission rejects; tests observe logical records and schema, not file-byte
preservation. Only synthetic scratch databases are used. No backups are created.

Tests cover dropped tables and triggers, no-op trigger replacement, missing uniqueness,
extra column/index/view, version mismatch, corrupt/oversized payloads, invalid family
rows, post-open schema changes, rollback of callback DDL and receipt writes, actual
fresh-process reopen, concurrent opens and a deliberate removed-validator mutation.
Run `python3 -m unittest discover -s tests -p test_storage_schema.py -v`.
Full validation uses the existing exact Tether dependency pin and no fallback.
