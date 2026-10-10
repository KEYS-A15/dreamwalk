# Backend milestone validation

Validated on Windows with Python 3.11.9 and uv 0.9.29.

- 26 unittest tests pass against both the editable install and the offline-installed
  `dreamwalk-0.4.0-py3-none-any.whl`.
- Ordinary `uv sync` and wheel build passed using locally available build tools.
- Installed `dreamwalk analyze` exported the order-service demo: 28 nodes,
  50 edges, zero diagnostics.
- Strict self-analysis of six Dreamwalk source files exported 1,046 nodes, 2,514 edges
  and zero diagnostics.
- HTTP graph retrieval, invalid-node/depth handling and cross-origin rejection
  passed in the automated suite.
- No other Python versions or operating systems were tested.

Tests cover nested scopes, parameter shadowing, rebinding, duplicate definitions,
wildcard imports, class namespace boundaries, definition-time calls, async facts,
implicit scopes, cross-file imports, direct and re-exported calls, branch roles,
loop break/continue/else paths, handlers, finally, context managers, match paths,
parse errors, ignored directories,
non-execution, source encoding, Unicode spans, deterministic output, graph focus,
file limits, export overwrite protection and API behavior.

The current source adds a conservative function-level control-flow graph through
loops, handlers, finally, context managers and match cases. It still has no runtime
recorder, semantic LLM explanations, second-language adapter or frontend.
