# Google OAuth Auth & LINE Authorization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restrict access to "น้องโชคช่วย" (Web UI, REST APIs, and LINE OA Webhook) strictly to the two authorized users (`cgikchik07@gmail.com`, `ppksaruenee@gmail.com`, and their LINE User IDs) with server-side revocable SQLite sessions, CSRF/Origin checks, HTTPS `Secure` cookies behind Cloudflare Worker, and fail-closed defaults.

**Architecture:** Add `auth_service.py` for Google OAuth 2.0, HMAC-signed cookies backed by a revocable `auth_sessions` SQLite table in `database.py`, and Origin/CSRF validation; enforce route-level Auth middleware and LINE sender allowlist checks in `app.py`; update `static/` with a Sunny Sky login gate and `X-CSRF-Token` client wiring; update `worker.js` / `wrangler.toml` to forward HTTPS headers and rotate container env via `configFingerprint` inside a single `"singleton"` Durable Object; and gate Node + Python tests in CI before deployment.

**Tech Stack:** Python 3.11, FastAPI, SQLite, `requests`, `hmac`/`hashlib`/`secrets` (stdlib), Vanilla JS/HTML/CSS, Node.js 22 (`node --test`), Cloudflare Workers + Containers (`worker.js`).

**Spec:** [docs/superpowers/specs/2026-11-15-google-oauth-auth-design.md](../specs/2026-11-15-google-oauth-auth-design.md)

## Global Constraints

- **Git Isolation & Branch Base:** Work strictly inside an isolated git worktree branched from **`main`**, independent of the active WIP branch `codex/line-mascot-cards` (do NOT mix uncommitted mascot-card changes or `static/mascot_cards/` into the Auth branch).
- Single FastAPI app (`app.py`) + single SQLite DB (`data/documents.db`); no Redis, external DB, or separate Node/TS backend.
- Use Python 3.11 stdlib + existing `requirements.txt` packages (`requests`); do not bloat the Docker image.
- Never store secrets (`GOOGLE_CLIENT_SECRET`, `AUTH_SESSION_SECRET`, `LINE_CHANNEL_SECRET`, `ALLOWED_LINE_USER_IDS`) in Git or `static/`.
- Never send raw images/PDFs to external cloud/LLM services.
- Fail-closed by default in `AppConfig.from_env()` (`auth_required=True`); reject typos in `AUTH_REQUIRED` with `ValueError`; keep `AppConfig.from_dir(...)` default `auth_required=False` so existing isolated unit tests continue passing.
- Do NOT merge to `main`, configure Cloudflare secrets, or deploy without explicit Owner approval (note: pushing/merging to `main` triggers `.github/workflows/deploy-cloudflare.yml` automatically if `CLOUDFLARE_API_TOKEN` is set).

## Review Focus

1. **Typo or deceptive opt-out in `AUTH_REQUIRED`** (`AUTH_REQUIRED=tru`, or `AUTH_REQUIRED=false` with `APP_ENV=production` / non-local HTTPS `PUBLIC_BASE_URL`) -> must raise `ValueError` at config load, never fail-open. (Verified in Task 1)
2. **Stolen cookie reused after `POST /api/auth/logout` or after email removal from `ALLOWED_EMAILS`** -> must return `401 Unauthorized` immediately via SQLite `auth_sessions` lookup and allowlist check. (Verified in Task 3 & Task 4)
3. **Worker-proxied HTTPS requests arriving at FastAPI as `http://container/...` vs HTTPS client cookie jar** -> test both internal proxy `Set-Cookie`/`Location` headers (`http://container`) AND full OAuth -> API -> Logout flow using an HTTPS `TestClient` (`https://nong-chokchuay-ocr.entrace-space.workers.dev`), plus localhost HTTP (`http://127.0.0.1:8765`). (Verified in Task 4)
4. **CSRF / Cross-origin state mutation** (`POST`/`PUT`/`PATCH`/`DELETE` with valid session cookie but missing/wrong `X-CSRF-Token`, missing `Origin`/`Referer`, or foreign `Origin`) -> must return `403 Forbidden`. (Verified in Task 4)
5. **LINE Webhook with valid `X-Line-Signature` from an unauthorized `userId`, group/room chat (`source.type != "user"`), or empty `ALLOWED_LINE_USER_IDS`** -> must NOT touch DB, ingest files, leak student allergy/expense data, or trigger postbacks; and must return `503` when `auth_required=True` and `LINE_CHANNEL_SECRET` is empty. (Verified in Task 5)

---

### Task 0: Isolated Git Worktree Setup (Branched from `main`)

**Files:**
- Modify: `.git/info/exclude` (local only, keeps `codex/line-mascot-cards` clean)
- Modify in worktree: `.gitignore` (add `.worktrees/`)
- Move into worktree: `docs/superpowers/specs/2026-11-15-google-oauth-auth-design.md`, `docs/superpowers/plans/2026-11-15-google-oauth-auth.md`

- [ ] **Step 1: Ignore `.worktrees/` locally and create worktree `.worktrees/google-oauth-auth` on `codex/google-oauth-auth` from `main`**

Run from `/Users/chalisa/openthai-ocr-app`:
```bash
grep -qxF '.worktrees/' .git/info/exclude || echo '.worktrees/' >> .git/info/exclude
git worktree add .worktrees/google-oauth-auth -b codex/google-oauth-auth main
```
Expected: New worktree at `/Users/chalisa/openthai-ocr-app/.worktrees/google-oauth-auth` checked out on `codex/google-oauth-auth` at `main` (`0a0e35b`), with zero WIP changes from `codex/line-mascot-cards`.

- [ ] **Step 2: Move Auth spec & plan docs into the worktree and add `.worktrees/` to `.gitignore` in `codex/google-oauth-auth`**

Move `docs/superpowers/specs/2026-11-15-google-oauth-auth-design.md` and `docs/superpowers/plans/2026-11-15-google-oauth-auth.md` into `.worktrees/google-oauth-auth/docs/superpowers/...` so the `codex/line-mascot-cards` workspace stays clean of Auth files.

- [ ] **Step 3: Verify clean baseline inside `.worktrees/google-oauth-auth`**

Run inside `.worktrees/google-oauth-auth`:
`/Users/chalisa/openthai-ocr-app/.venv/bin/python -m unittest discover -s tests -v`
Expected: All baseline tests on `main` PASS.

- [ ] **Step 4: Commit spec, plan, and `.gitignore` update inside `.worktrees/google-oauth-auth`**

```bash
git add .gitignore docs/superpowers/specs/2026-11-15-google-oauth-auth-design.md docs/superpowers/plans/2026-11-15-google-oauth-auth.md
git commit -m "docs: add Google OAuth auth design spec and implementation plan"
```

---

### Task 1: Strict Fail-Closed Config & `.env.example`

**Files:**
- Modify: `config.py:36-116`
- Modify: `.env.example`
- Test: `tests/test_auth.py`

**Interfaces:**
- Produces in `config.py`:
  - `AppConfig.app_env: str = "development"`
  - `AppConfig.auth_required: bool = False` (in `from_dir`, default `False`; in `from_env()`, strict default `True`)
  - `AppConfig.allowed_emails: Tuple[str, ...] = ()`
  - `AppConfig.google_client_id: str = ""`
  - `AppConfig.google_client_secret: str = ""`
  - `AppConfig.auth_session_secret: str = ""`
  - `AppConfig.auth_session_ttl_seconds: int = 7 * 24 * 60 * 60`
  - `AppConfig.allowed_line_user_ids: Tuple[str, ...] = ()`
  - `AppConfig.line_allow_group_chats: bool = False`
  - `_parse_strict_bool(raw: Optional[str], default: bool, field_name: str) -> bool`
  - `_parse_csv_tuple(raw: Optional[str], lowercase: bool = False) -> Tuple[str, ...]`

- [ ] **Step 1: Write failing config tests in `tests/test_auth.py`**

Assert:
- `AppConfig.from_env()` with empty env defaults `auth_required=True` and `line_allow_group_chats=False`.
- `AUTH_REQUIRED=tru` raises `ValueError`.
- `AUTH_REQUIRED=false` with `PUBLIC_BASE_URL=https://nong-chokchuay-ocr.entrace-space.workers.dev` or `APP_ENV=production` raises `ValueError`.
- `AUTH_REQUIRED=false` with `PUBLIC_BASE_URL=http://127.0.0.1:8765` succeeds (`auth_required=False`), and `ALLOWED_EMAILS` / `ALLOWED_LINE_USER_IDS` normalize cleanly into tuples.

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/chalisa/openthai-ocr-app/.venv/bin/python -m unittest tests.test_auth.AuthConfigTests -v`
Expected: FAIL (`AttributeError: 'AppConfig' object has no attribute 'auth_required'`)

- [ ] **Step 3: Implement strict Auth/LINE fields and `_parse_strict_bool` in `config.py`, and update `.env.example`**

Add `_parse_strict_bool`, `_parse_csv_tuple`, and `_is_local_dev_url(url: str) -> bool` in `config.py`. Normalize `allowed_emails` / `allowed_line_user_ids` inside `from_dir(...)`. In `from_env()`, enforce `AUTH_REQUIRED` via `_parse_strict_bool(..., default=True)` and raise `ValueError` on `auth_required is False` in `production` or non-local `PUBLIC_BASE_URL`; parse `AUTH_SESSION_TTL_SECONDS` (> 0 int, else `ValueError`). Update `.env.example` with `AUTH_REQUIRED=true`, `ALLOWED_EMAILS=cgikchik07@gmail.com,ppksaruenee@gmail.com`, `GOOGLE_CLIENT_ID=`, `GOOGLE_CLIENT_SECRET=`, `AUTH_SESSION_SECRET=`, `ALLOWED_LINE_USER_IDS=`, `LINE_ALLOW_GROUP_CHATS=false`.

- [ ] **Step 4: Run config tests and `test_llm_hybrid.py`**

Run: `/Users/chalisa/openthai-ocr-app/.venv/bin/python -m unittest tests.test_auth tests.test_llm_hybrid -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config.py .env.example tests/test_auth.py
git commit -m "feat(auth): add strict fail-closed auth and LINE allowlist config"
```

---

### Task 2: SQLite Revocable `auth_sessions` Table & CRUD

**Files:**
- Modify: `database.py:224-248` (and append session CRUD helpers)
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `database.get_db_connection(db_path)`
- Produces in `database.py`:
  - Table `auth_sessions(session_id TEXT PRIMARY KEY, email TEXT NOT NULL, name TEXT DEFAULT '', picture TEXT DEFAULT '', csrf_token TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT DEFAULT '')`
  - `create_auth_session(session_id: str, email: str, name: str, picture: str, csrf_token: str, expires_at: str, db_path: Optional[str] = None) -> Dict[str, Any]`
  - `get_active_auth_session(session_id: str, now_iso: Optional[str] = None, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]`
  - `revoke_auth_session(session_id: str, revoked_at: Optional[str] = None, db_path: Optional[str] = None) -> bool`
  - `purge_expired_auth_sessions(now_iso: Optional[str] = None, db_path: Optional[str] = None) -> int`

- [ ] **Step 1: Add failing SQLite session tests to `tests/test_auth.py`**

Test `init_db` idempotency (calling twice preserves existing sessions), `create_auth_session` + `get_active_auth_session` returns row before expiry, returns `None` after `expires_at`, and returns `None` immediately after `revoke_auth_session`.

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/chalisa/openthai-ocr-app/.venv/bin/python -m unittest tests.test_auth -v`
Expected: FAIL (`AttributeError: module 'database' has no attribute 'create_auth_session'`)

- [ ] **Step 3: Implement `auth_sessions` schema and helpers in `database.py`**

Add `CREATE TABLE IF NOT EXISTS auth_sessions (...)` and index on `expires_at` inside `init_db(db_path)` using UTC ISO-8601 strings (`datetime.now(timezone.utc).isoformat()`), parameterized `?` SQL queries, and `purge_expired_auth_sessions` called inside `create_auth_session`.

- [ ] **Step 4: Run `test_auth.py` and `test_vault_api.py`**

Run: `/Users/chalisa/openthai-ocr-app/.venv/bin/python -m unittest tests.test_auth tests.test_vault_api -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_auth.py
git commit -m "feat(auth): add revocable auth_sessions table and CRUD in SQLite"
```

---

### Task 3: `auth_service.py` — Signed Cookies, Trusted External URL, Google OAuth & CSRF

**Files:**
- Create: `auth_service.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `AppConfig` (`config.py`), `database.{create_auth_session, get_active_auth_session, revoke_auth_session}`
- Produces in `auth_service.py`:
  - Constants: `SESSION_COOKIE_NAME = "nong_chok_session"`, `CSRF_COOKIE_NAME = "nong_chok_csrf"`, `OAUTH_STATE_COOKIE_NAME = "nong_chok_oauth_state"`
  - `is_oauth_configured(cfg: AppConfig) -> bool`
  - `resolve_external_base_url(request: Request, cfg: AppConfig) -> str`
  - `should_use_secure_cookie(request: Request, cfg: AppConfig) -> bool`
  - `get_allowed_origins(request: Request, cfg: AppConfig) -> Tuple[str, ...]`
  - `sign_payload(payload: Dict[str, Any], secret: str) -> str`
  - `verify_signed_payload(token: str, secret: str) -> Optional[Dict[str, Any]]`
  - `authenticate_request(request: Request, cfg: AppConfig) -> Optional[Dict[str, Any]]`
  - `validate_csrf_and_origin(request: Request, cfg: AppConfig, session: Dict[str, Any]) -> bool`
  - `build_google_authorize_url(redirect_uri: str, state: str, cfg: AppConfig) -> str`
  - `exchange_google_code(code: str, redirect_uri: str, cfg: AppConfig) -> Dict[str, Any]`
  - `fetch_google_userinfo(access_token: str) -> Dict[str, Any]`
  - `issue_login_session(response: Response, request: Request, cfg: AppConfig, email: str, name: str, picture: str) -> Dict[str, Any]`
  - `revoke_login_session(response: Response, request: Request, cfg: AppConfig) -> bool`

- [ ] **Step 1: Add failing unit tests for `auth_service` in `tests/test_auth.py`**

Test:
- `resolve_external_base_url` & `should_use_secure_cookie` with `http://container/...` + `cfg.public_base_url="https://nong-chokchuay-ocr.entrace-space.workers.dev"` returns `https://nong-chokchuay-ocr.entrace-space.workers.dev` and `True` (and ignores spoofed `Host` / `X-Forwarded-Host`).
- Tampered cookie signature, expired token, revoked SQLite session, and email removed from `cfg.allowed_emails` all return `None` from `authenticate_request`.
- `validate_csrf_and_origin` passes only when `Origin` (or `Referer` if `Origin` absent) matches `get_allowed_origins` AND `X-CSRF-Token` matches `session["csrf_token"]`.

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/chalisa/openthai-ocr-app/.venv/bin/python -m unittest tests.test_auth -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'auth_service'`)

- [ ] **Step 3: Implement `auth_service.py`**

Use stdlib `hmac`, `hashlib`, `base64`, `json`, `secrets`, `time`, `urllib.parse` and `requests` (10s timeout for Google token/userinfo calls). Keep helper functions small and single-purpose.

- [ ] **Step 4: Run `test_auth.py`**

Run: `/Users/chalisa/openthai-ocr-app/.venv/bin/python -m unittest tests.test_auth -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add auth_service.py tests/test_auth.py
git commit -m "feat(auth): implement signed revocable sessions, OAuth helpers, and CSRF checks"
```

---

### Task 4: FastAPI Auth Middleware, OAuth Endpoints, `/api/health`, & CORS Hardening

**Files:**
- Modify: `app.py:1-160`, `app.py:560-780`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `auth_service.*`, `config.AppConfig`
- Produces in `app.py`:
  - `GET /api/health` (public, returns `{status, brand, auth_required, oauth_configured, line_oa_configured, line_allowlist_configured}`, zero DB paths or counts)
  - `GET /api/auth/me`, `GET /api/auth/google/login`, `GET /api/auth/google/callback`, `POST /api/auth/logout`
  - HTTP middleware enforcing fail-closed auth (`401`) on all non-allowlisted routes and Origin/CSRF validation (`403`) on state-changing routes when `cfg.auth_required=True`
  - Restricted `CORSMiddleware` origins when `cfg.auth_required=True`

- [ ] **Step 1: Add failing API & security integration tests in `tests/test_auth.py`**

Split proxy-boundary & HTTPS end-to-end tests cleanly (do NOT depend on untracked `static/mascot_cards/`):
- **Unauthenticated & Allowlist Tests:**
  - `/api/system`, `/api/documents`, `/api/expenses`, `/api/students`, `/api/database/download`, `/api/watcher/status`, `/api/samples`, `/api/ocr`, `/api/line/simulate` return `401`.
  - `GET /`, `GET /api/health`, `GET /api/auth/me`, `GET /static/styles.css`, `GET /static/mascot.jpg` return `200`, and `/api/health` has no `database` or `path` keys.
  - Test `/static/mascot_cards/*` allowlist rule using a temporary `static` directory containing `mascot_cards/sample.jpg` (must return `200` unauthenticated, while `/static/private_note.txt` returns `401`).
- **Case A — Worker Proxy Internal URL & `Set-Cookie` Test (`TestClient(test_app, base_url="http://container")`, `public_base_url="https://nong-chokchuay-ocr.entrace-space.workers.dev"`, `app_env="production"`):**
  - `GET /api/auth/google/login` returns `302` with `Location` containing `redirect_uri=https%3A%2F%2Fnong-chokchuay-ocr.entrace-space.workers.dev%2Fapi%2Fauth%2Fgoogle%2Fcallback` and `Set-Cookie` for `nong_chok_oauth_state` containing `; Secure` and `HttpOnly`.
  - `GET /api/auth/google/callback` (sending the state cookie explicitly in `headers={"Cookie": ...}` because HTTP client won't auto-send `Secure` cookies over `http://container`) returns `Set-Cookie` for `nong_chok_session` (`HttpOnly; ...; Secure`) and `nong_chok_csrf` (`Secure`).
- **Case B — End-to-End HTTPS Cookie Jar Flow (`TestClient(test_app, base_url="https://nong-chokchuay-ocr.entrace-space.workers.dev")`, mocked Google token/userinfo):**
  - Mismatched `state` -> redirects to `/?auth_error=invalid_state` without session cookie.
  - Allowed emails (`cgikchik07@gmail.com` / `ppksaruenee@gmail.com`, case-insensitive, `email_verified=True`) -> full cookie-jar flow `login -> callback -> /api/auth/me -> /api/system -> POST /api/students (with Origin + X-CSRF-Token)` succeeds (`200`).
  - Disallowed email (`stranger@gmail.com`) or `email_verified=False` -> redirects to `/?auth_error=unauthorized_email` without session cookie.
  - **Stolen cookie revocation:** copy `nong_chok_session` before `POST /api/auth/logout`, call `POST /api/auth/logout` with valid `Origin` + `X-CSRF-Token`, then replay copied cookie -> returns `401`.
  - **CSRF & Origin validation:** authenticated `POST /api/students` without `X-CSRF-Token`, with wrong `X-CSRF-Token`, without `Origin`/`Referer`, or with `Origin: https://evil.example` -> returns `403`.
- **Case C — Localhost HTTP Flow (`TestClient(test_app, base_url="http://127.0.0.1:8765")`, `public_base_url=""`):**
  - Full OAuth flow works over local HTTP (`Secure` flag omitted on local HTTP cookies).

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/chalisa/openthai-ocr-app/.venv/bin/python -m unittest tests.test_auth -v`
Expected: FAIL

- [ ] **Step 3: Implement auth middleware, `/api/health`, `/api/auth/*`, and CORS tightening in `app.py`**

Allow `create_app(..., static_dir: Optional[str] = None)` (defaulting to `os.path.join(BASE_DIR, "static")`) so tests can mount a temporary static directory cleanly without touching tracked files. Implement public allowlist (`{"/", "/api/health", "/api/auth/me", "/api/auth/google/login", "/api/auth/google/callback", "/api/line/webhook"}` plus UI static allowlist `{"/static/styles.css", "/static/app.js", "/static/mascot.jpg"}` and prefix `"/static/mascot_cards/"`), attach `request.state.auth_user` on authenticated requests, run `validate_csrf_and_origin` on state-changing methods (excluding `/api/line/webhook`), and configure `CORSMiddleware` origins when `cfg.auth_required=True`.

- [ ] **Step 4: Run `test_auth.py`, `test_smoke.py`, and `test_vault_api.py`**

Run: `/Users/chalisa/openthai-ocr-app/.venv/bin/python -m unittest tests.test_auth tests.test_smoke tests.test_vault_api -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_auth.py
git commit -m "feat(auth): enforce fail-closed auth middleware, OAuth routes, /api/health, and CSRF"
```

---

### Task 5: LINE OA Sender Authorization (`app.py`) & `openExternalBrowser=1` (`line_service.py`)

**Files:**
- Modify: `app.py:564-663`
- Modify: `line_service.py` (add `append_external_browser_param` helper and apply to `http://`/`https://` Flex/action URLs)
- Test: `tests/test_auth.py`, `tests/test_nong_chokchuay.py`

**Interfaces:**
- Consumes: `cfg.auth_required`, `cfg.line_channel_secret`, `cfg.allowed_line_user_ids`, `cfg.line_allow_group_chats`
- Produces in `line_service.py`:
  - `append_external_browser_param(url: str) -> str`
- Produces in `app.py`:
  - `/api/line/webhook` returns `503` if `cfg.auth_required` and `not cfg.line_channel_secret`
  - Rejects any LINE event where `source.type != "user"` (when `not cfg.line_allow_group_chats`) OR `not cfg.allowed_line_user_ids` OR `user_id not in cfg.allowed_line_user_ids` before touching DB, OCR, or postback handlers, returning `rejected_events` in webhook JSON response.

- [ ] **Step 1: Add failing LINE authorization & external browser tests in `tests/test_auth.py`**

Test:
- With `auth_required=True` and empty `line_channel_secret`, `POST /api/line/webhook` returns `503`.
- With valid `X-Line-Signature` and empty `allowed_line_user_ids`, sending `"แพ้อาหาร"` does NOT read DB or leak student data (`rejected_events=1`, `processed_events=0`).
- With valid `X-Line-Signature` and `allowed_line_user_ids=("U-OWNER", "U-TEACHER")`:
  - `"U-STRANGER"` asking `"แพ้อาหาร"` or sending postback `action=mark_reimbursed&doc_id=...` or image is rejected (`rejected_events=1`, DB untouched).
  - `"U-OWNER"` in a group (`source.type == "group"`) when `line_allow_group_chats=False` is rejected (`rejected_events=1`, DB untouched).
  - `"U-TEACHER"` in 1:1 chat (`source.type == "user"`) succeeds (`processed_events=1`, Flex card URI contains `openExternalBrowser=1`).

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/chalisa/openthai-ocr-app/.venv/bin/python -m unittest tests.test_auth -v`
Expected: FAIL

- [ ] **Step 3: Implement LINE sender authorization in `app.py` and `append_external_browser_param` in `line_service.py`**

Implement `append_external_browser_param` in `line_service.py` using `urllib.parse` and apply it to public `http...` Flex/action URLs (keeping existing `tests/test_nong_chokchuay.py` expectations green). In `app.py` `/api/line/webhook`, enforce `503` when `auth_required` and `line_channel_secret` is missing, and validate `source.type` + `source.userId` before any DB/OCR/postback execution.

- [ ] **Step 4: Run `test_auth.py` and `test_nong_chokchuay.py`**

Run: `/Users/chalisa/openthai-ocr-app/.venv/bin/python -m unittest tests.test_auth tests.test_nong_chokchuay -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py line_service.py tests/test_auth.py
git commit -m "feat(auth): enforce LINE sender allowlist and openExternalBrowser Flex links"
```

---

### Task 6: Frontend Login Gate, User Email Badge, Logout & CSRF Client Wiring

**Files:**
- Modify: `static/index.html`
- Modify: `static/styles.css`
- Modify: `static/app.js`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `GET /api/auth/me`, `GET /api/auth/google/login`, `POST /api/auth/logout`, `csrf_token`
- Produces in `static/index.html` & `static/app.js`:
  - `#auth-gate` overlay + `#btn-google-login` + `#auth-webview-warning` + `#auth-error-banner`
  - `#auth-user-pill` (`#auth-user-email` + `#btn-logout`) in Header
  - `apiFetch` attaches `X-CSRF-Token` on `POST`/`PUT`/`PATCH`/`DELETE` and switches back to Login Gate on `401`.

- [ ] **Step 1: Add failing static UI contract tests in `tests/test_auth.py`**

Assert `GET /` includes `#auth-gate`, `#btn-google-login`, `#btn-logout`, and `static/app.js` contains `/api/auth/me`, `X-CSRF-Token`, `/api/auth/logout`, and zero secrets.

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/chalisa/openthai-ocr-app/.venv/bin/python -m unittest tests.test_auth -v`
Expected: FAIL

- [ ] **Step 3: Update `static/index.html`, `static/styles.css`, and `static/app.js`**

Hide workspace sections until `/api/auth/me` confirms `authenticated || !auth_required`; show `#auth-gate` styled with Sunny Sky & Golden Hour CI; detect LINE/in-app WebView on login gate; wire `csrfToken` into `apiFetch` for state-changing methods; clear `?auth_error=` after displaying Thai message via `textContent`.

- [ ] **Step 4: Run tests**

Run: `/Users/chalisa/openthai-ocr-app/.venv/bin/python -m unittest tests.test_auth tests.test_nong_chokchuay -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add static/index.html static/styles.css static/app.js tests/test_auth.py
git commit -m "feat(ui): add Google login gate, logout button, WebView hint, and CSRF header"
```

---

### Task 7: Cloudflare Worker, Wrangler Config, CI Workflow & Documentation

**Files:**
- Modify: `wrangler.toml`
- Modify: `worker.js`
- Modify: `.github/workflows/deploy-cloudflare.yml`
- Modify: `INDEX.md`
- Modify: `docs/STATUS.md`
- Create: `tests/test_worker_js.mjs`

**Interfaces:**
- Produces in `wrangler.toml`:
  - `[vars]` `APP_ENV = "production"`, `AUTH_REQUIRED = "true"`, `ALLOWED_EMAILS = "cgikchik07@gmail.com,ppksaruenee@gmail.com"`, `LINE_ALLOW_GROUP_CHATS = "false"`
  - Secret comments for `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `AUTH_SESSION_SECRET`, `ALLOWED_LINE_USER_IDS`
- Produces in `worker.js`:
  - `computeConfigFingerprint(env)` (SHA-256 via `crypto.subtle.digest`) and `buildContainerEnv(env)` (forces `APP_ENV: "production"`, `AUTH_REQUIRED: "true"`)
  - Single DO `env.NONG_CHOK_CONTAINER.idFromName("singleton")`
  - Lifecycle: compare `configFingerprint` vs `this.ctx.storage.get("configFingerprint")`; on mismatch, destroy running container on the same DO, store new fingerprint, and start container with new env; forward `X-Forwarded-Proto` & `X-Forwarded-Host`

- [ ] **Step 1: Write failing Node unit test `tests/test_worker_js.mjs` mocking `cloudflare:workers` and Container lifecycle**

Use Node `module.register` (or small `worker_helpers.js` / mock loader) to test `NongChokChuayOCR` + `default.fetch` directly:
- `buildContainerEnv({ AUTH_REQUIRED: "false", GOOGLE_CLIENT_ID: "cid" })` forces `AUTH_REQUIRED: "true"` and `APP_ENV: "production"`.
- `default.fetch` routes to `idFromName("singleton")` (not `Boolean(secret)`).
- Request 1 starts container with initial env and forwards `X-Forwarded-Proto: https` and `X-Forwarded-Host`.
- Request 2 with **same** secrets does **NOT** call `container.destroy()` or restart `container.start(...)`.
- Request 3 after rotating `AUTH_SESSION_SECRET` (`"secret-v1"` -> `"secret-v2"`) **DOES** call `container.destroy()`, saves new `configFingerprint`, and calls `container.start(...)` with `AUTH_SESSION_SECRET === "secret-v2"`.

- [ ] **Step 2: Run Node test to verify it fails**

Run: `node --test tests/test_worker_js.mjs`
Expected: FAIL

- [ ] **Step 3: Update `worker.js`, `wrangler.toml`, `.github/workflows/deploy-cloudflare.yml`, `INDEX.md`, and `docs/STATUS.md`**

Update `worker.js` and `wrangler.toml`; add `actions/setup-node@v4` (Node 22) + `node --check worker.js && node --test tests/test_worker_js.mjs` into the `test` job of `.github/workflows/deploy-cloudflare.yml` so CI blocks deploy if Node tests fail; update `INDEX.md` and `docs/STATUS.md` (including pending Google OAuth/Secrets setup and Container ephemeral disk demo-only constraint).

- [ ] **Step 4: Run full local verification suite inside `.worktrees/google-oauth-auth`**

Run:
1. `node --check worker.js && node --test tests/test_worker_js.mjs`
2. `/Users/chalisa/openthai-ocr-app/.venv/bin/python -m py_compile app.py auth_service.py config.py database.py extractor.py line_service.py ocr_service.py watcher.py`
3. `/Users/chalisa/openthai-ocr-app/.venv/bin/python -m unittest discover -s tests -v`
Expected: All syntax checks and all Python + Node tests PASS; `git status` in main workspace `/Users/chalisa/openthai-ocr-app` remains unchanged.

- [ ] **Step 5: Commit**

```bash
git add wrangler.toml worker.js .github/workflows/deploy-cloudflare.yml tests/test_worker_js.mjs INDEX.md docs/STATUS.md
git commit -m "chore(deploy): harden Cloudflare worker, CI Node tests, and docs"
```

---

### Task 8: PR, CI, Owner Approval Gate & Post-Deploy Verification

**Files:**
- Modify: None (Git push, PR, CI wait, and explicit Owner approval gate per `AGENTS.md` & `docs/release-workflow.md`)

- [ ] **Step 1: Review full diff (`git diff main..HEAD`) against `CODING_STANDARDS.md`**

Confirm zero `.env`, `*.db`, real secrets, or `codex/line-mascot-cards` changes leaked into `codex/google-oauth-auth`.

- [ ] **Step 2: Push `codex/google-oauth-auth` and open Pull Request targeting `main`**

Run:
```bash
git push -u origin codex/google-oauth-auth
gh pr create --base main --head codex/google-oauth-auth --title "feat(auth): Google OAuth login, revocable sessions, CSRF, and LINE allowlist" --body "..."
```
PR body MUST include:
- Non-secret Wrangler secrets & Google OAuth setup plan (variable names, purpose, verification, rollback)
- Explicit notice that Cloudflare Container disk is ephemeral (use Cloudflare for demo/testing until persistent R2 storage ADR is implemented)

- [ ] **Step 3: Wait for GitHub Actions `test` checks (Python + Node) to pass on the PR**

Run: `gh pr checks --watch`
Expected: All required checks PASS (`pull_request` does NOT trigger `deploy-cloudflare`).

- [ ] **Step 4: STOP & Request Explicit Owner Approval Before Merge / Secrets / Deploy**

Ask Owner explicitly before:
1. Merging PR into `main` (warning that merging to `main` triggers `.github/workflows/deploy-cloudflare.yml`'s `deploy-cloudflare` job automatically if `CLOUDFLARE_API_TOKEN` is configured)
2. Setting Cloudflare Wrangler Secrets (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `AUTH_SESSION_SECRET`, `ALLOWED_LINE_USER_IDS`, `LINE_CHANNEL_SECRET`, `LINE_CHANNEL_ACCESS_TOKEN`) and Google Cloud Console Redirect URIs
3. Running or verifying live deployment

- [ ] **Step 5: Post-Deploy Verification (After Owner Approval & Deploy)**

Verify on live Cloudflare URL (`https://nong-chokchuay-ocr.entrace-space.workers.dev`):
- `GET /api/health` returns `200` (`auth_required: true`, no DB paths/counts)
- `GET /api/system` returns `401` when unauthenticated
- Google Sign-In flow succeeds for allowed emails, rejects others, sets `Secure` + `HttpOnly` cookies, `POST /api/auth/logout` revokes cookie server-side (`401` on replay), CSRF rejects missing/wrong `X-CSRF-Token` (`403`), and LINE Webhook rejects unknown `userId` while allowing `ALLOWED_LINE_USER_IDS`.
