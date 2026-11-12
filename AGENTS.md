# OpenThai 2.0 OCR Studio — Solo SaaS

Local-first Thai OCR Paperless Vault & Expense Tracker built on FastAPI, SQLite FTS5, and OpenThai 2.0 ONNX CPU models.

## Running & Testing

- Start local server: `./start.sh` (or `./.venv/bin/python app.py`)
- Run test suite: `./.venv/bin/python -m unittest discover -s tests -v`

## Agent skills

### Issue tracker

Local markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context repo (`CONTEXT.md` and `docs/adr/` at the repo root). See `docs/agents/domain.md`.
