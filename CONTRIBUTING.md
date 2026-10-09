# Contributing

Dreamwalk is an early Python-first project. Keep changes source-grounded,
deterministic and explicit about uncertainty.

## Development

```powershell
uv sync
uv run python -m unittest discover -s tests -v
uv run dreamwalk analyze examples --strict
```

Include a focused test for non-trivial analyzer behavior. Do not describe a
static candidate as observed execution, and do not add another language until
the shared graph contract is stable.
