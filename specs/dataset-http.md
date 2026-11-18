# Explicit dataset-only HTTPS transport

`DatasetHTTP` is an opt-in transport profile for the dataset export outbox. It
uses the existing per-request isolated child/TLS machinery, not a new store or
coordinator. Constructing a transport is not a content-export grant: current
retained authority and exact destination approval remain enforced by `Export`.
No environment credentials or account setup are added. Synthetic loopback tests
are not actual Langfuse dataset-service qualification or permission to upload.

The score `LangfuseHTTP` class and its existing request validator stay score-only.
The child protocol now carries an explicit `score-v1` or `dataset-v1` profile;
missing/unknown profiles and unknown transport subclasses fail closed. The score
body/response bound remains 65536 bytes, input envelope 160000, output envelope
90000. Dataset body/response limits are 262144, envelope limits 420000/350000.
Both parent and child validate profile-specific requests and deadline bounds.

Dataset profile allows only:

- GET `/api/public/v2/datasets/{opaqueName}` (1–64 ASCII letters/digits/_/-).
- GET `/api/public/dataset-items/{64-lowercase-hex-id}`.
- POST `/api/public/dataset-items` with canonical JSON, exact ACTIVE item fields,
  exact descriptor/content hashes and projection metadata matching item ID.

There is no dataset creation, archival, deletion, arbitrary query, redirect,
association, media upload or score/dataset crossover. Controls/media markers,
extra fields, changed content digests and noncanonical JSON are denied. The
standalone child validator uses only stdlib; it neither changes import paths nor
loads package code through environment startup hooks. Its body encoding is
cross-checked using the existing dataset contract's generated Unicode fixture.
Destination/project/split and exact remote content readback remain the separate
`DatasetProjection` responsibility; the transport is not an authorization token.

Child credentials travel only in bounded stdin, never argv or inherited env.
The child has a scrubbed environment, disabled proxies, no redirects/retries,
per-request TLS context and explicit local-test CA limited to loopback. The
parent's subprocess deadline kills and reaps an overdue child. Returned response
bytes are bounded; errors omit credentials, request content and server bodies.
The existing subprocess implementation and its limits remain the trust boundary,
not a claim of OS sandboxing against malicious local source.

Tests execute temporary loopback TLS requests with synthetic credentials for
both profiles, rejection before spawn, malformed direct-worker input, scrubbed
errors/env/argv, deadline reaping and oversized-response rejection. No real
Langfuse service or external destination is contacted. Existing outbox unknown
POST reconciliation and sticky local revocation semantics remain unchanged;
concrete transport does not make remote revocation atomic or erase remote data.
