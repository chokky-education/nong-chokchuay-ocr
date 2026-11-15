# 3. Durable Cloudflare R2 + Versioned SQLite Snapshot Persistence

Status: accepted (amends ADR 0001 and ADR 0002 for Cloudflare Cloud mode)

## Context

Under ADR 0001 and ADR 0002, the application stores all Vault Document metadata, FTS5 trigram indexes, Expense Records, Student Profiles, and revocable `auth_sessions` in a single SQLite database (`data/documents.db`) and keeps original Source Files on local disk (`data/uploads/`). When deployed on Cloudflare Workers Paid + Cloudflare Containers, `worker.js` destroys idle container instances (`this.ctx.container.destroy()`) after `SLEEP_AFTER_MS` (3 minutes). Because Cloudflare Container disk storage is ephemeral, any database rows or uploaded Source Files written only to container disk are wiped on idle destroy or instance replacement.

## Decision

We retain FastAPI + OpenThai 2.0 ONNX + SQLite/FTS5 inside the single Python container while adding durable persistence backed by a private first-party Cloudflare R2 bucket (`VAULT_BUCKET` / `nong-chokchuay-vault`):

1. **Authorized Cloud Storage Scope (Owner Decision — Option 1):**
   - When running in Cloud mode (`R2_REQUIRED=true` / `R2_GATEWAY_URL` configured), the project's private Cloudflare R2 bucket (`nong-chokchuay-vault`) is authorized as first-party internal storage for:
     - Versioned SQLite snapshots (`vault/snapshots/rev-<revision:08d>-<uuid12>.db`, which include `image_preview` thumbnails and `auth_sessions`), and
     - Original `Source Files` (`vault/uploads/<storage_key>`).
   - The R2 bucket must remain strictly private with no public bucket URL; access happens solely through the Worker's `VAULT_BUCKET` binding after application authentication/authorization.
   - Raw images and PDFs are still never sent to external third-party clouds or LLMs (`extractor.py` remains strictly text-only).
   - Local mode (`./start.sh` and local tests without R2 configured) continues to use local SQLite (`data/documents.db`) and `data/uploads/` with zero network or R2 dependency.

2. **Outbound Gateway Without Deadlocks (`VaultR2Gateway` + Singleton Container):**
   - `wrangler.toml` enforces `max_instances = 1` and `compatibility_date = "2025-11-17"` with `[[r2_buckets]]` (`VAULT_BUCKET`).
   - `worker.js` exposes `VaultR2Gateway` (`WorkerEntrypoint`) and binds `container.interceptOutboundHttp("r2.internal", this.ctx.exports.VaultR2Gateway({ props: { internalToken, containerEpoch } }))`.
   - Python talks to `http://r2.internal` via `storage_service.py` using `X-Vault-Internal-Token` and `X-Vault-Container-Epoch` headers, avoiding S3 static keys, `boto3`, or DO lock re-entrancy deadlocks.
   - Durable Object re-instantiation while a container is already running reuses the persisted `internalSyncToken` and `containerEpoch` from `this.ctx.storage`.
   - `alarm()` checks both `_activeRequests > 0` and container `/api/health` `busy` status before calling `container.destroy()`.

3. **Durable Commit Protocol & Serialization ("Saved / Success" Rule):**
   - A single `VaultStorageCoordinator` serializes all state-changing operations across Web/API, Watch Folder (`watcher.py`), LINE OA Webhook/Postback (`line_service.py`), and OAuth sessions (`auth_service.py`).
   - No channel may return success, send a LINE confirmation reply, or delete a Watch Folder file until the durable commit completes:
     1. Upload any new Source File to `vault/uploads/<storage_key>` with verified byte size and SHA-256 checksum.
     2. Update local SQLite (including recording deterministic `operation_id` in `vault_operations`) and export a consistent SQLite snapshot via `sqlite3.Connection.backup()`, validated with `PRAGMA integrity_check` and `PRAGMA foreign_key_check`.
     3. Upload the versioned snapshot to `vault/snapshots/rev-<revision:08d>-<uuid12>.db`.
     4. Conditionally update `vault/manifest.json` using R2 `onlyIf: { etagMatches }` (or `etagDoesNotMatch: "*"` on first bootstrap). **This conditional manifest PUT is the single atomic commit point.**
   - If step 2 or 3 fails before the manifest commit, local SQLite is immediately rolled back from the last committed snapshot (`documents.db.committed`). If step 4 returns an ambiguous error, the coordinator reconciles against R2 before allowing further reads/writes.

4. **Startup Restore Gate (`BOOTING -> RESTORING -> READY | ERROR`):**
   - On startup in R2 mode, `watcher.py` stays stopped and protected routes return `503` until `VaultStorageCoordinator.startup()` completes.
   - `GET /manifest` distinguishes an empty bucket (`404`) from a non-empty bucket missing `vault/manifest.json` (`409` -> `ERROR`).
   - Bootstrapping an empty bucket requires explicit `R2_BOOTSTRAP_IF_EMPTY=true`; an uninitialized bucket with `R2_BOOTSTRAP_IF_EMPTY=false` enters `ERROR` (`503`) rather than silently creating an empty database over potential missing state.
   - Restored snapshots are validated for size, SHA-256 checksum, `PRAGMA integrity_check == "ok"`, `PRAGMA foreign_key_check`, and `PRAGMA user_version <= SUPPORTED_SCHEMA_VERSION` before atomically replacing `documents.db` (with SQLite `-wal`/`-shm`/`-journal` sidecars removed first).

5. **Deletion, Retention & Backup Policy:**
   - Deleting a document commits a new manifest revision first and records `(storage_key, deleted_in_revision)` in `pending_r2_deletions`.
   - `VAULT_BUCKET` retains the latest `SNAPSHOT_RETENTION_COUNT` (default `5`) snapshots in `manifest.json`.
   - R2 source objects in `pending_r2_deletions` are only deleted after `deleted_in_revision <= earliest_retained_revision` (once snapshots predating deletion have aged out of retention).
   - Restoring an older retained snapshot within the retention window recovers documents and Source Files that were deleted after that snapshot; after a snapshot ages past retention, its deleted Source Files are permanently purged.

6. **Authorized Lazy Source File Restore:**
   - Documents record portable `source_storage_key` and `source_sha256`.
   - `GET /api/documents/{doc_id}/file` executes FastAPI authentication and document existence checks first, then calls `storage.ensure_local_source_file(doc)` to fetch missing objects from `vault/uploads/<storage_key>`, verify SHA-256, and cache locally.

## Consequences, Costs & Operational Constraints

- **LINE Reply Token Timing:** LINE specifies that a `replyToken` is single-use and must be used within **1 minute** of receiving the webhook (and as fast as possible). Cold start + R2 restore + OpenThai ONNX OCR + R2 commit + LINE reply must be measured in production; if latency approaches the 1-minute window, a future UX decision (e.g., separate Push Message fallback) must be evaluated explicitly.
- **Cloudflare Cost Model:** Cloudflare Workers Paid (\$5/month) is a base fee with included quotas, not a hard spending cap. Cloudflare Containers bill separately for memory/CPU/disk usage beyond included allowances, Durable Objects bill for requests/storage, and R2 Standard includes free tiers of **10 GB-month** storage, **1 million Class A** writes/month, and **10 million Class B** reads/month before metered overages apply (which depend on snapshot size, `SNAPSHOT_RETENTION_COUNT`, and write frequency).

## Initial Migration, DO Identity Transition & Rollback Plan

1. **Before Deploying to Production (DO Identity & Existing Data Check):**
   - Inspect the live deployment before switching (`GET /api/system`, `GET /api/database/download`, and check if any documents/files exist in the currently running container). Note that changing DO naming or replacing an ephemeral container does not recover data from already-destroyed containers.
   - Create the private bucket once with `npx wrangler r2 bucket create nong-chokchuay-vault`.
   - If bootstrapping a brand-new empty bucket, set `R2_BOOTSTRAP_IF_EMPTY = "true"` temporarily for initial deploy (or restore a verified pre-migration SQLite snapshot + Source Files to R2 first), then verify `/api/health` reports `storage_state: "READY"` and set `R2_BOOTSTRAP_IF_EMPTY = "false"`.
2. **Rollback:**
   - Stop writes (temporarily disable incoming routes or set maintenance response), verify compatible revision in `vault/manifest.json` (`schema_version <= SUPPORTED_SCHEMA_VERSION`), roll back the Worker deployment (`npx wrangler rollback`), and confirm `/api/health` and core flows against the selected revision.
