# Bounded hygiene process lifetime

This Refinery-only port of the reviewed root PR66 design preserves bounded synthetic
verification and truthful cleanup receipts. It does not change execution gates,
component pins or other component copies. No production incident or PID-reuse
event is claimed.

## Ownership and bounds

`run_entry` starts a new POSIX session and is the sole waiter for its child.
It requires default SIGCHLD handling and rejects an observed non-default policy;
it does not install or change a signal handler. Calling it with concurrent external
`waitpid` calls or concurrent SIGCHLD changes is unsupported. An unreaped leader
reserves its PID; after `poll` or `wait` reaps it, that number is no longer authority
to signal its former process group.

Output is read through a nonblocking, unbuffered pipe in the owning thread. The
retained tail remains at most 65,536 bytes. Each loop checks the monotonic deadline,
even with continuous output. There is no blocked daemon reader or buffered-reader
lock to wait on when closing stdout.

- A normal completed, drained check gets no cleanup signal.
- A timeout attempts one group SIGKILL only while the leader remains unreaped,
  followed by a wait bounded to two seconds and output draining bounded to two
  seconds. A failed/uncertain stop is not retried with a possibly reused identifier.
- A leader that exits while another writer retains the pipe gets at most two
  seconds of draining, then a failed `output_incomplete` receipt. The wrapper closes
  its own pipe and reports `cleanup: unknown`; it never signals the reaped group.
- Cancellation preserves the original exception while attempting bounded cleanup
  of an owned, unreaped leader and closing the output pipe.

The bounds cover waits and pipe reads after successful process creation, not OS
process creation itself or an uninterruptible kernel operation. This is not process
tree containment: descendants may escape the session or close their output while
remaining alive. EOF plus a reaped leader is not proof that every descendant exited.
Caller-provided process isolation remains separate.

## Receipt compatibility

Original nonzero exit codes, timeout reasons, sanitized failure diagnostics,
unittest/Node summaries and resource/profiling fields are retained. `output_complete`
is an additive observation of pipe EOF. An incomplete/read-failed/close-failed output
cannot turn exit zero into a passing check. Unknown cleanup is explicit; no field
claims complete descendant cleanup.

Signal, reap, read and close failures retain only fixed error codes in
`signal_error`, `reap_error`, `output_read_error` and `output_close_error`. These
separate permission denial, missing process group, interruption, missing child,
timeout and other OS errors without copying exception text, paths or child output.
Cleanup details do not overwrite the primary timeout or original exit code.

## Reproduction and limits

Run directly, without the aggregate validator:

```
python3 -B -m unittest discover -s tests -p 'test_hygiene*.py' -v
python3 -B -O -m unittest discover -s tests -p 'test_hygiene*.py' -v
python3 -B tools/check_scaffold.py
```

The lifecycle tests observe signal/reap ordering and forbidden late signals with
fake processes and an injected clock. This port uses synthetic process/stream/selector tests only, including a bounded
output flood. It does not execute the real pipe/process cases from root PR66 or
exercise PID reuse, native sessions, live providers, registry access or all-process
containment.
Repository-wide aggregate and isolated-runner qualification remain separate gates.
