# Backend milestone validation

Validated on Windows with Python 3.11.9 and uv 0.9.29.

- 20 unittest tests pass against both the editable install and the offline-installed
  `dreamwalk-0.2.0-py3-none-any.whl`.
- Ordinary `uv sync` and wheel build passed using locally available build tools.
- Installed `dreamwalk analyze` exported the order-service demo: 15 nodes,
  23 edges, zero diagnostics.
- Strict self-analysis of six Dreamwalk source files exported 396 nodes, 755 edges
  and zero diagnostics.
- HTTP graph retrieval, invalid-node/depth handling and cross-origin rejection
  passed in the automated suite.
- No other Python versions or operating systems were tested.

Tests cover nested scopes, parameter shadowing, rebinding, duplicate definitions,
wildcard imports, class namespace boundaries, definition-time calls, async facts,
implicit scopes, cross-file imports and direct calls, branch roles, parse errors, ignored directories,
non-execution, source encoding, Unicode spans, deterministic output, graph focus,
file limits, export overwrite protection and API behavior.

The current source adds direct cross-module call candidates and explicit branch
groups. Compile checks, the full suite and strict self-analysis pass with zero
diagnostics. It still has no complete control-flow graph, runtime recorder, semantic
LLM explanations, second-language adapter or frontend.
