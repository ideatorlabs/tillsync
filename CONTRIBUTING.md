# Contributing

Thanks for helping. Bug reports, fixes, new connectors and better docs are all welcome.

## Set up

```bash
git clone https://github.com/ideatorlabs/tillsync
cd tillsync
uv venv --python 3.11 .venv
uv pip install -p .venv -e '.[dev,excel]'
```

## Check your change

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest --cov=tillsync
```

## Pull requests

- Keep each PR focused on one change, and add or update tests for it.
- Tests must not touch the network or real APIs: use `httpx.MockTransport` or a local server.
- Never commit real customer data, tokens or keys. Use clearly fictional examples.
- Update `CHANGELOG.md` under "Unreleased".
- No CLA or DCO sign-off is required. By opening a PR you agree your contribution is licensed under
  Apache-2.0.
