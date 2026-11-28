# Offline export review-order rehearsal

From the repository root:

```sh
for case in next-review stale-root-head missing-root-review stale-checks wrong-base; do
  python3 tests/test_synthetic_export_graph.py "$case" || exit
done
python3 -m unittest discover -s tests -p 'test_synthetic_export_graph.py' -v
```

The fixture labels match slots #99–#103 for orientation. All heads, bases,
reviews, check receipts and merge states are invented. An already-completed
predecessor needs an exact-head approval and passed checks before a later
fixture can be suggested as next reviewable. The suggestion is not an
approval or a decision to merge. Stale heads/checks, wrong bases and absent
review evidence stop the chain.

No GitHub call, exporter, data, deployment, credentials, or network is used.
Verify the actual PR branch graph, exact heads, reviews and required checks
separately before taking any action on the real stack.
