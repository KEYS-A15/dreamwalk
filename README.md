# Dreamwalk

[![CI](https://github.com/KEYS-A15/dreamwalk/actions/workflows/ci.yml/badge.svg)](https://github.com/KEYS-A15/dreamwalk/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Explore your code. Follow its execution. Understand every step.

**Status: first backend milestone, not a finished execution explorer.** Python
static analysis and a local read-only API work in this release. The Iron Man
inspired interface, runtime recordings and additional languages are future work.

## Install in your Portfolio folder

Extract the contents of this archive into
`C:\Users\shrey\Documents\Projects\Portfolio\dreamwalk`.
The archive contains project files directly, without a nested project directory.

```powershell
cd C:\Users\shrey\Documents\Projects\Portfolio\dreamwalk
uv sync
uv run dreamwalk analyze examples --output demo.graph.json
uv run dreamwalk serve examples
```

Open `http://127.0.0.1:8765/graph` to inspect the demo graph. Installation uses
setuptools; the analyzer and API have no third-party runtime dependencies.

For another repository:

```powershell
uv run dreamwalk analyze C:\path\to\repository --output project.graph.json
uv run dreamwalk explain examples --id 'order_service.py:8:0:function'
uv run dreamwalk focus examples --id 'order_service.py:8:0:function' --depth 1
uv run python -m unittest discover -s tests -v
```

Use actual `nodes[].id` values from the graph (line numbers change when code changes).
Export refuses to overwrite existing files. `--strict` returns exit code 1 when
analysis reports diagnostics; normal analysis can return a partial graph with
diagnostics. Invalid requests return exit code 2.

## What works

- Modules, classes, functions, async functions and nested structural blocks.
- Local import module candidates, conservative same-file and imported call candidates.
- Explicit branch nodes for present `if`/loop/`try`/`match`/conditional-expression arms.
- Parameters, annotations, author docstrings, returns, assignments, awaits and raises.
- Relative source spans, UTF-8 byte columns and per-file SHA-256 identity.
- Function/block isolation with adjacent callers, callees and containers.
- JSON export; optional source embedding with `--include-source`.
- Parse diagnostics; ignored dependency/build folders and symlink boundaries.
- Deterministic analysis without importing or executing the target repository.

`explain` returns extracted facts and the author's docstring. It does not yet
generate a semantic natural-language explanation of arbitrary code or infer
business intent, actual return values, complete side effects or invariants.

## API contract

| Endpoint | Result |
| --- | --- |
| `GET /health` | API status and schema version |
| `GET /graph` | Complete analyzed snapshot |
| `GET /explain?id=<node-id>` | Source-grounded facts for one node |
| `GET /focus?id=<node-id>&depth=1` | Undirected graph neighborhood, depth 0–5 |

The API binds to `127.0.0.1`, serves one graph captured at startup, and accepts no
execution commands, filesystem paths or uploads. Restart to refresh. Cross-origin
browser requests are rejected; UI integration will need a same-origin route.
It is a local development service, not a hosted multi-user backend.

## Offline fallback

The included wheel was built and tested for this milestone. If package downloads
are unavailable, use it without resolving build dependencies:

```powershell
uv venv --python 3.11
uv pip install --offline dist\dreamwalk-0.2.0-py3-none-any.whl
uv run --no-sync dreamwalk analyze examples
uv run --no-sync python -m unittest discover -s tests -v
```

For development, prefer `uv sync` and its editable install. The wheel is a
snapshot; source changes require rebuilding or switching to an editable install.

## Evidence rules

`schema_version: 1.1` defines nodes, edges, files, diagnostics and limitations.
IDs are relative path + source position + kind; they are snapshot IDs, not stable
identities across edits. File hashes establish exact file versions.

- **syntax:** directly extracted containment or import syntax.
- **static_candidate:** one lexical or directly imported local function/class
  definition matches a call; runtime control flow, rebinding and decorators may
  still change the target.
- **unresolved:** external imports, dynamic attribute/method calls, ambiguous or
  shadowed names, and implicit comprehension/lambda scopes.

Calls are attributed to the nearest structural node. Function body facts exclude
nested definitions. Definition-time calls belong to the enclosing scope. A
function's focus graph includes related blocks; its explanation lists directly
attached calls and child IDs for further exploration.

**This is a structural map, not a complete control-flow graph.** `contains` and
`branches` do not mean “executes next.” No observed runtime edges exist yet.
Imported modules and their unambiguous top-level functions/classes can be static
candidates. Inheritance, instance dispatch, re-exports and multi-hop attribute
calls remain unresolved. Conditional definitions, `exec`, reflection,
monkey-patching and global/nonlocal rebinding require further analysis.

Python syntax support follows the interpreter used to run Dreamwalk. Python 3.11
cannot parse syntax introduced in later versions; failures appear as diagnostics.
Source is parsed, never executed, but this is not a hardened service for hostile
repositories. File/count limits are safeguards, not an OS resource sandbox.

Even without `--include-source`, docstrings, expressions and default values can
contain secrets. Review JSON exports before publication.

## Next backend milestones

1. Extend cross-module candidates beyond direct imports, then build a tested
   control-flow graph for a documented Python subset.
2. Opt-in recording of a user-specified command, with separate observed evidence
   and validation for recursion, exceptions, generators and async execution.
3. Grounded semantic explanations with source citations and an optional model.
4. Reviewed recruiter tours and curated export; then the Iron Man inspired UI.
5. A second language adapter using the same graph contract after the Python
   contract stabilizes. Only Python is currently supported.

## Positioning checkpoint

- [Code Charter](https://github.com/CRJFisher/code-charter) already combines SCIP
  call graphs, function summaries and movement between high- and low-level views.
- [CodeSee](https://docs.codesee.io/docs/getting-started) already offers collapsible
  dependency maps, annotations, tours and visual pull-request review.
- [AppMap](https://appmap.io/docs/appmap-docs) already records runtime behavior and
  presents source-linked diagrams; it also supports runtime sequence-diagram diffs.

Diagrams, summaries, progressive disclosure, tours and recordings are therefore not
novel by themselves. Dreamwalk's product hypothesis is one evidence model that lets a
reader compare syntax, possible static paths, unresolved relationships and a specific
observed run, then publish a cited, repository-owned portfolio walkthrough from that
same model. That distinction still needs user evaluation before it becomes a novelty
claim.
