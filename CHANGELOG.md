# Changelog

Dreamwalk follows [Semantic Versioning](https://semver.org/).

## 0.5.0 - 2026-10-09

- Add an opt-in Python script recorder with observed call, line, return, yield and exception events.
- Link recorded frames and executed lines back to the pre-run static snapshot when unambiguous.
- Serve one validated observed run at `GET /run` without mixing it into static graph evidence.
- Exclude arguments, local variables, values and exception messages from recordings.

## 0.4.0 - 2026-10-09

- Extend possible static control flow through loops, `try`/`finally`, `with` and `match`.
- Represent `break`, `continue`, exception candidates and unmatched exception paths explicitly.
- Preserve uncertainty where context managers or pending transfers through `finally` affect flow.

## 0.3.0 - 2026-10-09

- Resolve calls through unambiguous explicit local re-export chains.
- Add statement and implicit-exit nodes for function-level path exploration.
- Add possible static `flows_to` edges for function entry, sequencing and `if` branches.
- Keep unsupported compound control flow visibly unresolved rather than implying execution.

## 0.2.0 - 2026-10-09

- Analyze Python modules, classes, functions and structural blocks without executing them.
- Export a language-neutral, source-grounded graph through JSON, CLI commands and a read-only local API.
- Resolve conservative same-file and direct cross-module call candidates.
- Represent present branch groups explicitly while preserving unresolved relationships.
- Provide explain and focus views for progressive exploration.
