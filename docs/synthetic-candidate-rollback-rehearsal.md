# Offline candidate rollback rehearsal

From the repository root:

```sh
for case in verified missing-predecessor wrong-approval wrong-lineage wrong-holdout wrong-approved-predecessor unknown-candidate wrong-candidate unknown-rollback wrong-rollback; do
  python3 tests/test_candidate_rollback_rehearsal.py "$case" || exit
done
python3 -m unittest discover -s tests -p 'test_candidate_rollback_rehearsal.py' -v
```

Invented approval must bind the exact candidate, lineage and holdout to the
reviewed predecessor. An in-memory table and separate scripted witness exercise
one candidate change and one rollback. A missing or mismatched candidate
receipt stops before rollback; missing or mismatched rollback evidence leaves
the result unresolved without retry. Clearing the local fixture is teardown,
not a substitute for a verified rollback receipt.

This exercise neither evaluates nor promotes a real model. It uses no external
service, credential, persistence, serving configuration or network. Live
acceptance still needs separately reviewed lineage and holdout evidence,
scoped approval, the exact retained predecessor and independent actual serving
and rollback readback.
