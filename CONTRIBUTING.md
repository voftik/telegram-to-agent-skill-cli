# Contributing

## Development Setup

```bash
git clone https://github.com/voftik/telegram-to-agent-skill-cli.git
cd telegram-to-agent-skill-cli
uv sync --group dev
```

## Local Checks

```bash
uv run ruff check .
uv run python -m pytest -q
uv build
uv run twine check dist/*
```

## Manual Smoke Test

These commands require valid `TG_API_ID`, `TG_API_HASH`, and a working Telegram session:

```bash
tg whoami
tg refresh --yaml
tg recent --hours 24 --limit 5 --yaml
tg search "test" --hours 24 --sync-first --yaml
```

## Pull Requests

- Keep changes focused and small
- Add or update tests for behavior changes
- Update `README.md` and `SKILL.md` when command behavior changes
- Avoid committing local session files, `.env`, or `data/`
