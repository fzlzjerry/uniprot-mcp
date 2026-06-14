# Contributing & maintaining

Developer/maintainer notes for `uniprotkb-mcp`. End-user install/usage lives in
[README.md](README.md).

## Development setup

```bash
git clone https://github.com/fzlzjerry/uniprot-mcp
cd uniprot-mcp
uv sync          # creates .venv, installs fastmcp + httpx
```

The distribution/command is `uniprotkb-mcp`; the importable Python package is
`uniprot_mcp` (under `src/`).

## Tests

```bash
# Offline structural checks (no network): tools register, ctx hidden, resource present
uv run python -m tests.check_structure

# Live smoke test against rest.uniprot.org (prints output for eyeballing)
UNIPROT_MCP_CONTACT="you@example.org" uv run python -m tests.smoke
```

## Continuous integration

`.github/workflows/ci.yml` runs on every push / PR to `main`:

- **structure** (Python 3.10 & 3.13) — byte-compile + the offline checks above.
- **smoke** — the full live-API smoke test.

## Releasing (PyPI Trusted Publishing — no token)

Publishing uses **OIDC Trusted Publishing**, PyPI's recommended method: GitHub
Actions proves its identity to PyPI directly, so **no API token or secret is
stored anywhere**. `.github/workflows/publish.yml` builds and publishes on a
version tag.

**One-time PyPI setup** (already configured for this project; kept for reference /
re-setup) — at <https://pypi.org/manage/account/publishing/>, the trusted
publisher is:

| Field | Value |
|-------|-------|
| PyPI Project Name | `uniprotkb-mcp` |
| Owner | `fzlzjerry` |
| Repository name | `uniprot-mcp` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

> None of these values are secret — the workflow file is public and OIDC trust is
> enforced by GitHub↔PyPI, so only Actions runs in this repo can publish.

**Cut a release:**

```bash
# bump `version` in pyproject.toml, commit, then tag:
git tag v0.2.0
git push origin v0.2.0
```

The tag triggers `publish.yml`, which checks the tag matches the pyproject
version, builds the sdist + wheel, and uploads via OIDC.

> Manual fallback: `uv build && uv publish --token pypi-...` still works, but
> Trusted Publishing is the recommended, token-free path.
