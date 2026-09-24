# Contributing

## Publish model

`main` is the consumer-facing branch. Anything merged to `main` is considered
published. Each skill script carries a module-level `_SKILL_VERSION` constant —
the sole source of skill version identity. SKILL.md does **not** carry a version
field. The version-bump workflow rewrites `_SKILL_VERSION` on merge to `main`.

## PR workflow

1. Branch off `main`.
2. Open a PR back into `main`.
3. CI must pass (ruff, inline-deps, pytest, per-script `--help`).
4. Rebase onto `main` before merging — linear history is preferred.
5. Use the GitHub merge button.

Authors **must not** edit any `_SKILL_VERSION` constant by hand.

## Skill correctness tests

Per-skill tests live in `skills/<name>/tests/`. Shared helpers are in
`tests/conftest.py`. Run them with `uv sync --group dev && uv run pytest`.

## Version bumps

On push to `main`, `.github/workflows/version-bump.yml` bumps changed skills and
publishes a lean plugin payload to `plugin-dist`. Bump kind comes from PR labels:

| Label            | Bump kind |
| ---------------- | --------- |
| `release: major` | major     |
| `release: minor` | minor     |
| (none)           | patch     |

## Local development against weather-skills-core

```bash
tools/run_with_local_core.sh skills/subc-mme-fetch/scripts/fetch.py --help
```

Core is pinned to `main` in `pyproject.toml` and every
skill script's PEP 723 header.
