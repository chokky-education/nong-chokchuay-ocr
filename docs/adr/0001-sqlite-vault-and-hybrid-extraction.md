# 1. Single SQLite Vault + Filesystem Source Files + Text-Only Hybrid Extraction

Status: accepted

We store all Vault Document metadata, FTS5 trigram indexes, and Expense Records in a single SQLite database (`data/documents.db`), keep Source Files on local disk (`data/uploads/`), and implement an offline Thai Regex extractor first with an optional text-only LLM REST adapter. This avoids external DB/broker processes on a solo machine, keeps raw document images 100% local even when optional LLM extraction is enabled, and allows single-folder backup while preserving existing database rows via idempotent migrations.
