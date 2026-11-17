# Retained local serving identity and bytes

Partial functional step for #17 and parent #6. The existing `LocalServing`
SQLite adapter still serves only the bounded synthetic lookup program; no
production target, credentials, provider or authenticated approval issuer is added.

Schema validation alone does not validate stored rows. Previously `predict`
checked only digest/content, so a corrupt activation identifier or generation could
still produce output. A new load could replace a corrupt predecessor without
validating its bytes/program. The adapter now rejects invalid retained state in
`load`, `readback` and `predict`, including idempotent loads. No automatic repair
or reset is performed. A rejected load rolls back its local call-log insertion
and preserves all prior rows, including corrupt evidence.

Within the existing immediate SQLite transaction, a common read validates the
activation token, positive integer generation, digest and matching bounded program
bytes. SQL uses byte lengths (not NUL-terminated TEXT lengths) and projects only short TEXT identity/digest values, an INTEGER generation,
and a BLOB between one byte and the existing 1 MiB limit. Invalid shapes project
NULL and fail validation without materializing oversized content into Python.
The normal readback and prediction response formats stay unchanged. A healthy
higher-generation replacement still checks the exact predecessor digest.

SQLite affinity can normalize values before storage, so this validates persisted
values, not the provenance of their original insertion. It cannot authenticate a
plausible forged row or an adversarial database/OS. The schema, store, SQL engine
and callbacks remain trusted; SQL storage/locking latency is not hard-time bounded.
No transaction spans registry and serving store. Historical registry outcomes,
production approval, actual inference and deployment qualification remain separate.

`tests/test_serving_row_integrity.py` observes actual disposable SQLite rows and
call counts, fails all three paths on corrupted state, exercises a fresh optimized
Python process, observes SQL projection of an oversized BLOB and NUL-suffixed oversized identities, checks healthy
replay/replacement/output attribution, and deliberately removes the generation
guard to prove that corrupt identity then permits output. Network sockets are
denied and independently asserted unused. No live service or provider is used.

Reproduce focused verification:

```sh
python -m unittest discover -s tests -p test_serving_row_integrity.py -v
python -O -m unittest discover -s tests -p test_serving_row_integrity.py -v
```

Full validation requires the unchanged exact Tether source identified by
`dependencies/tether-feedback.json` and the pinned quality tools. Set
`RUNSPINDLE_TETHER_SOURCE` to that prepared source and run `python tools/validate.py`.
A passing fixture suite does not close #17, #6 or their live qualification gates.
