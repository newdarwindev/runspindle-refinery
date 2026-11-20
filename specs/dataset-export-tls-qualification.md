# Composed synthetic dataset export TLS qualification

This test-only lane composes real signed local materialization, `Export`,
`DatasetProjection`, `DatasetHTTP`, its isolated subprocess, and a temporary
loopback TLS server. It does not run actual Langfuse or authorize external
uploads. Original fixture defaults and product source are unchanged.

The server requires the exact existing-dataset and item endpoint identities and
returns the expected project, dataset, split metadata and exact item bytes.
Tests cover exact POST body/auth and current grant denial; accepted POST with
immediate lost acknowledgement; reopening the same durable Store/outbox;
not-visible readback remaining uncertain; subsequent exact readback settling
without a second POST; registry withdrawal after an accepted POST preventing
settlement; original signed source invalidation; sticky local revocation; and
wrong project/split preventing any POST. Remote bytes intentionally remain after
local withdrawal, so no remote erasure or atomic revocation is claimed.

The signed fixture's deny-all socket default is preserved. This suite scopes a
loopback-listener-only parent socket class around its test-owned server: binding
outside 127.0.0.1, non-IPv4/non-stream sockets, and parent outbound
connect/connect_ex/sendto are denied. This is a test-scoped adapter, not OS
isolation or a general malicious-code sandbox. Actual HTTP
requests occur only in the existing isolated transport child with an explicit
loopback origin, ephemeral test CA and synthetic Basic authorization. No proxy,
redirect, timeout or reaping behavior is replaced. Lost acknowledgement closes
the TLS connection after recording the accepted body instead of sleeping.
The server uses its own registry connection for post-acceptance withdrawal,
closes it, and every test shuts down/closes/joins its server thread. All files
belong to the existing temporary synthetic fixture directory and are cleaned up.

The existing HTTP Protocol describes a writable origin while the concrete
transport intentionally exposes a frozen origin. The test uses a type-only cast
at composition; there is no runtime transport wrapper or permission change.

These tests establish this bounded composition against a synthetic protocol
peer, not actual Langfuse normalization/version compatibility, real-account
credentials, paid-model behavior, remote durability or live production readiness.
