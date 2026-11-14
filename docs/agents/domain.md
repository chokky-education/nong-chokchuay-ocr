# Domain Docs

How the engineering skills and agents should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`AGENTS.md`** at the repo root (main agent rules and architecture boundaries).
- **`docs/STATUS.md`** and **`INDEX.md`** for current work and file map.
- **`GLOSSARY.md`** (and **`CONTEXT.md`**) at the repo root for canonical domain vocabulary.
- **`docs/adr/`**: read ADRs that touch the area you're about to work in.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `GLOSSARY.md` / `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly and ask the project owner before making changes rather than silently overriding.
