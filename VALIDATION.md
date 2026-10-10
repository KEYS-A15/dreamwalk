# Backend milestone validation

Validated on Windows with Python 3.11.9 and uv 0.9.29.

- 28 unittest tests pass against both the editable install and the offline-installed
  `dreamwalk-0.5.0-py3-none-any.whl`.
- Ordinary `uv sync` and wheel build passed using locally available build tools.
- Installed `dreamwalk analyze` exported the order-service demo: 28 nodes,
  50 edges, zero diagnostics.
- Strict self-analysis of seven Dreamwalk source files exported 1,298 nodes, 3,141 edges
  and zero diagnostics.
- HTTP graph retrieval, invalid-node/depth handling and cross-origin rejection
  passed in the automated suite.
- The order-service sample recorded 36 observed events, all linked to static symbols,
  with exit code zero and no truncation.
- No other Python versions or operating systems were tested.

Tests cover nested scopes, parameter shadowing, rebinding, duplicate definitions,
wildcard imports, class namespace boundaries, definition-time calls, async facts,
implicit scopes, cross-file imports, direct and re-exported calls, branch roles,
loop break/continue/else paths, handlers, finally, context managers, match paths,
recording recursion, exceptions, generators, async functions, output preflight,
out-of-root rejection, parse errors, ignored directories,
non-execution, source encoding, Unicode spans, deterministic output, graph focus,
file limits, export overwrite protection and API behavior.

The current source adds a conservative function-level control-flow graph and one
opt-in Python-script recorder. It still has no arbitrary-command/subprocess
recording, semantic LLM explanations, second-language adapter or frontend.
