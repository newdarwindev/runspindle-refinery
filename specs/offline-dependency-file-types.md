# Prepared dependency file types

Partial issue #16 and its offline integrity contract. This change does not adopt
any candidate inventory, import DSPy or run a provider. Canonical dependency and
image pins remain unchanged.

The execution inventory previously followed a payload symlink when hashing host
bytes. Replacing a reviewed module with a symlink to identical host-side bytes
still matched the inventory. Absolute links can resolve differently inside the
container, and unrecorded directory links or special files must not disappear
from admission merely because they are not regular files.

Inventory now rejects symbolic links, including root/ancestor aliases, dangling
links and linked directories, plus FIFOs, sockets and other special nodes before
reading package metadata or dispatching any container command. Directory walking
fails closed on read errors instead of silently omitting a subtree. Only real
directories and regular files are accepted; existing bytecode rejection remains.
Callers must provide the actual canonical directory path, not a filesystem alias.

The manifest schema and clean regular-file inventory are unchanged. This is a
preflight over a trusted prepared filesystem, not a race-free snapshot or a lock
against a hostile concurrent host writer. The operator must still keep prepared
bytes immutable during admission and execution. No permission settings, mounts,
credentials, services or deployment configuration are changed.

Synthetic tests use 76 invented distribution records and harmless module text.
They cover equal-byte absolute/relative symlinks, root/ancestor/directory/dangling
links, FIFO/socket rejection without opening them, unreadable traversal and clean
inventory equality. A removed-file-type-guard mutation restores the old false
inventory match and fails the independent rejection expectation. Rejected inputs
produce zero subprocess/Docker calls. No dependency payload is imported.

Run `python3 -m unittest tests.test_offline_inventory -v` and the same command with
`python3 -O`. These are source-component tests, not native DSPy qualification.
