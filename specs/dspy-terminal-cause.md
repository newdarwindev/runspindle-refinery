# Preserve the first terminal optimizer failure

The actual-framework adverse-path run at `70c41cd` failed its unchanged oracle.
Diagnostic source `f56987d` established that an empty scripted provider left one
unresolved reservation and durable `provider_unknown`, but a later binding check
reported `dispatch_binding_changed` because the failed call advanced the provider
cursor. No candidate escaped and no extra dispatch occurred.

Binding rejection now preserves the first durable terminal reason through the
budget snapshot, with strict token validation and a binding-failure fallback.
It still unconditionally raises: it never adopts a foreign cursor, refunds usage,
retries a provider, or invokes a clock that may be the invalid binding itself.
An unstopped foreign cursor still reports `dispatch_binding_changed`.

Focused tests prove unchanged ledger/attempt bytes on retry, independent single
provider dispatch, prior terminal reasons, malformed-reason fallback, and no
replaced-clock invocation for either memory or SQLite budgets. These component
tests do not establish actual DSPy acceptance; the original five-case oracle
remains unchanged and needs a new exact-source native run.
