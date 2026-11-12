# 01: Matt Pocock Setup and Test Seam

**What to build:** Initialize Git, domain glossary, ADR, local markdown issue tracker scaffolding, and the isolated automated test harness so every subsequent slice can be built test-first against real FastAPI and temporary SQLite/filesystem seams.

**Blocked by:** None (can start immediately)

**Status:** done

- [x] Git initialized with `.gitignore` excluding `.venv/`, `data/`, `.env`, and Python caches
- [x] `AGENTS.md`, `CONTEXT.md`, `docs/agents/*`, `docs/adr/0001-*.md`, and `.scratch/` spec/issues created
- [x] Running the test command executes cleanly inside `.venv` using isolated temporary storage without touching production `data/`
