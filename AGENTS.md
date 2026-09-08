# AGENTS.md

Contributor and agent rules for this repository. Terse by design.

## Commits

Conventional Commits. The type sets the release:

| Type | Release |
| --- | --- |
| `fix:` | patch |
| `feat:` | minor |
| `feat!:` or a `BREAKING CHANGE:` footer | major |
| `chore:` `docs:` `test:` `ci:` `refactor:` `perf:` `build:` `style:` | none |

release-please parses merged commits and raises a release PR; a human merges it,
which tags `v<version>` and publishes. Non-conforming commits and PR titles fail
the `commitlint` check.

## Build, test, lint

```sh
uv sync --locked --python 3.13 --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run --python 3.13 pytest --cov --cov-report=term-missing
uv sync --locked --python 3.13 --extra dev --extra test-integration
uv run pytest -m integration
```

## Comments and docs

Terse, constraint-only, present tense. State the constraint and why it holds, not
what changed. No history notes and no ticket references in comments.

## Rules

- Release notes are generated onto the GitHub Release from commit messages;
  release-please is configured with `skip-changelog`, so `CHANGELOG.md` stays the
  empty stub.
- Loud errors: a failure is fatal. No silent fallbacks, no `|| true`, no
  swallowed exceptions, no compatibility shims.
- The workflows under `.github/workflows/` are the source of truth for commands;
  keep this file in step with them.
