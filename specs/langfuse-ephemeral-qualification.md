# Bounded HTTPS transport and ephemeral score qualification

Status: source implementation and synthetic protocol tests. Actual Langfuse
execution requires a separately reviewed disposable server runner and its pinned
image/version/startup/cleanup evidence. A compatible loopback HTTP fixture is not
Langfuse qualification.

## Transport

`LangfuseHTTP` implements the existing injected transport. It requires an explicit
canonical HTTPS origin and supplied Basic authorization. No credentials are found
in files/environment automatically; no proxy environment, redirects, retries or
SDK defaults are used. Each request executes a fixed trusted Python worker with
isolated startup and a sanitized environment. Authorization travels only in bounded
stdin, never argv or diagnostics. The worker emits at most one bounded response.
The parent deadline covers DNS, TLS, response headers and body, kills/reaps the
worker on timeout, and treats every failure as an uncertain effect. It never retries.

TLS hostname/certificate verification remains enabled. A caller may supply exact
CA PEM bytes only for a numeric loopback HTTPS origin. The opt-in runner config
reads those bytes from a regular single-link file without following symlinks and
checks their SHA256. This is a per-client trust context, not global root-store or
OS security configuration. Temporary server keys/certificates are runner-owned.

## External runner interface

The runner provisions real Langfuse and its dependencies on an isolated temporary
network, creates one synthetic project/trace and ephemeral API keys, then writes a
mode-0600 regular JSON config containing exactly:

- `schema`: integer 1;
- `origin`: canonical loopback HTTPS origin with explicit non-default port;
- `authorization_file`: regular single-link mode-0600 file containing exact ephemeral
  Basic authorization bytes, local only;
- `project_id`, `trace_id`: the authorized synthetic destinations;
- `ca_file`, `ca_sha256`: exact local TLS certificate authority;
- `backend`: exact `manifest_sha256`, `config_sha256` (lowercase 64-hex digests)
  and bounded `version`. The runner separately verifies these image descriptors;
  this association is not proof inferred from an HTTP response.

Run `python tools/langfuse_ephemeral_check.py PRIVATE_CONFIG NEW_RECEIPT` in a
reviewed exact checkout, with `RUNSPINDLE_TETHER_SOURCE` set to the exact admitted
Tether source. The runner must impose an outer process-group deadline of 120
seconds, kill remaining descendants on failure, and always remove its own services,
network, volumes and private configuration. The workflow has a 90-second request
budget and at most 15 readback attempts per case; each request is capped at ten
seconds and the remaining budget. Startup/cleanup are separate runner obligations.
No persistent service or scheduled job is created by these tools.

## Actual workflow and honest fault scope

The existing signed synthetic Tether fixture supplies authenticated feedback and
immutable lineage. The metadata adapter/outbox then performs actual HTTPS POST
and exact v3 readback. The workflow requires three remote score writes:

1. Positive metadata score, with bounded polling for actual asynchronous visibility.
2. A new policy revision whose actual successful POST acknowledgement is deliberately
   dropped by a transparent test wrapper. Reopening the local ledger must reconcile
   by readback, never repeat that POST.
3. A signed reaction removal projected as a negative tombstone. An intervening
   export-consent withdrawal must cause zero remote writes.

The lost acknowledgement is injected; backend delay is observed rather than
fabricated. Restart means local ledger close/reopen, **not** server or fresh-process
restart. Server restart needs separately captured runner evidence. No model calls,
dataset content uploads, automatic training or model deployment are involved.
The receipt contains opaque IDs/hashes and counts, not credentials/content/private
server responses. Existing output is never overwritten; failure emits no success
receipt. An actual server/API mismatch must fail rather than weaken the readback
oracle or substitute a mock.

## Source tests

`tests/test_langfuse_http.py` uses a temporary loopback TLS protocol server to test
certificate validation, request framing, proxy exclusion, redirects, bounds and
wall timeout. `tests/test_langfuse_ephemeral_check.py` uses a synthetic transport
for orchestration negatives. Neither suite establishes actual Langfuse acceptance.
