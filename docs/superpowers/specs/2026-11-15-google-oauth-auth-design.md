# Design Spec: ระบบยืนยันตัวตนและความปลอดภัยครบวงจรสำหรับ "น้องโชคช่วย" (Nong ChokChuay)

**Date:** 2026-11-15
**Status:** Revised Draft v2 (Pending Owner Approval)
**Target Users:** Shared 2-user workspace restricted strictly to `cgikchik07@gmail.com` and `ppksaruenee@gmail.com` (Web/API) and their verified LINE User IDs (LINE OA)

---

## 1. Intent & Why

ระบบ **"น้องโชคช่วย" (Nong ChokChuay)** จัดเก็บข้อมูลที่มีความอ่อนไหวสูง ได้แก่ สำเนาใบสมัครและทะเบียนเด็กอนุบาล (เลขบัตรประชาชน 13 หลัก, วันเกิด, เบอร์ผู้ปกครอง, ข้อมูลแพ้อาหาร) และใบเสร็จ/ข้อมูลการเบิกจ่ายเงินของคุณครู

ปัจจุบันหน้าเว็บหลัก (`/`), REST API ข้อมูลภายใน (`/api/documents`, `/api/expenses`, `/api/students`, `/api/database/download`, `/api/ocr`, `/api/line/simulate`), ช่องทาง LINE OA (`/api/line/webhook` ที่ยังไม่ได้จำกัด `userId` ของผู้ส่ง), และโดเมนสาธารณะบน Cloudflare Workers (`https://nong-chokchuay-ocr.entrace-space.workers.dev`) ยังขาดชั้น Auth และการป้องกันสิทธิ์ครบวงจร จึงต้องเพิ่มระบบ Authentication & Authorization ทั้งฝั่งเว็บ/API และฝั่ง LINE OA ให้เข้าใช้งานได้เฉพาะผู้ใช้ที่ได้รับอนุญาต 2 คนเท่านั้น โดยยังใช้งานคลังข้อมูลร่วมกันได้เหมือนเดิม

---

## 2. Goals, Non-Goals & Anti-Goals

- **Goals:**
  1. **Strict Fail-Closed Auth โดยค่าเริ่มต้น:** `AppConfig.from_env()` บังคับเปิด Auth (`auth_required = True`) เสมอ; ค่าที่พิมพ์ผิดหรือไม่รู้จักใน `AUTH_REQUIRED` (เช่น `tru`) ต้อง **error และหยุดเริ่มระบบ** ทันที และห้ามปิด Auth เมื่อ `PUBLIC_BASE_URL` เป็นโดเมนจริง/HTTPS หรือใน `worker.js`
  2. **Google OAuth 2.0 + Email Allowlist:** ยืนยันตัวตนผ่าน Google OAuth 2.0 โดยรับเฉพาะอีเมลที่ `email_verified == true` และอยู่ใน `ALLOWED_EMAILS` (`cgikchik07@gmail.com`, `ppksaruenee@gmail.com`) แบบ case-insensitive; ปฏิเสธอีเมลอื่นด้วยสถานะไม่ผ่านสิทธิ์
  3. **Server-Side Revocable Sessions ใน SQLite:** เก็บ session ในตาราง `auth_sessions` ของ SQLite เดิมควบคู่กับ Signed `HttpOnly` Cookie เพื่อให้นำไปตรวจสอบและเพิกถอนได้จริงทันทีเมื่อกด Logout (`revoked_at`)
  4. **HTTPS `Secure` Cookies & Trusted `PUBLIC_BASE_URL`:** กำหนด `redirect_uri`, `Origin` check, และ flag `Secure` บน Cookie จาก `PUBLIC_BASE_URL` ที่เชื่อถือได้ (ไม่พึ่งพา internal `http://container` ที่ `worker.js` ส่งเข้า FastAPI)
  5. **CSRF & Origin Protection:** ป้องกันคำขอเปลี่ยนแปลงข้อมูล (`POST`, `PUT`, `PATCH`, `DELETE`) ด้วย Session-bound `X-CSRF-Token` และตรวจ `Origin`/`Referer` นอกเหนือจาก `SameSite=Lax` และ CORS allowlist
  6. **LINE OA Sender Authorization:** ตรวจทั้งลายเซ็น `X-Line-Signature` และสิทธิ์ผู้ส่ง (`ALLOWED_LINE_USER_IDS` และปิดรับแชทกลุ่มเป็นค่าเริ่มต้นด้วย `LINE_ALLOW_GROUP_CHATS=false`) ก่อนอ่านข้อมูลเด็ก รับไฟล์ หรือแก้ไขสถานะบิล
  7. **Safe Secret Rotation ใน Durable Object เดิม:** ใช้ Durable Object key คงที่ (`"singleton"`) และเก็บ `configFingerprint` ใน `this.ctx.storage` เพื่อสั่ง restart container ภายใน DO เดิมเมื่อเปลี่ยน secret โดยไม่แตก DO ใหม่กระจัดกระจาย พร้อมระบุข้อจำกัดและแผนเรื่องข้อมูลชั่วคราวบน Container อย่างชัดเจน
- **Non-Goals:**
  - ไม่แยกระดับสิทธิ์ (`admin` vs `teacher`) หรือแยกพื้นที่เก็บข้อมูลรายบุคคล — ทั้ง 2 บัญชีใช้คลังเอกสาร สมุดเบิกจ่าย และแฟ้มเด็กอนุบาลร่วมกันตามความต้องการของเจ้าของระบบ
  - ไม่สร้างระบบลงทะเบียนสาธารณะ, ระบบรหัสผ่าน, หรือระบบ Multi-tenant สำหรับโรงเรียนอื่น
  - **ไม่เปลี่ยนสถาปัตยกรรมจัดเก็บข้อมูลถาวร (R2 / External DB) ในงานนี้:** การเพิ่มระบบจัดเก็บถาวรนอก Container ต้องขออนุมัติเจ้าของและบันทึก ADR ใหม่แยกต่างหาก (ดูหัวข้อ 3 และ 6.3)
- **Anti-Goals:**
  - ห้ามใส่ `GOOGLE_CLIENT_SECRET`, `AUTH_SESSION_SECRET`, `LINE_CHANNEL_SECRET`, หรือค่าลับใด ๆ ลงใน Git หรือโค้ดฝั่งเบราว์เซอร์ (`static/`)
  - ห้ามเปิดให้ระบบข้าม Auth เงียบ ๆ (fail-open) เมื่อค่า config หายหรือพิมพ์ผิดทั้งฝั่ง Web/API และฝั่ง LINE OA
  - ห้ามทำให้ชุดทดสอบเดิม (`tests/test_smoke.py`, `tests/test_vault_api.py`, `tests/test_nong_chokchuay.py`) ที่สร้างแอปด้วย `AppConfig.from_dir(...)` โดยไม่เปิดโหมด Auth พัง

---

## 3. Constraints & Operational Prerequisite (Container Disk Ephemerality)

- **Single FastAPI + Single SQLite Architecture:** ทำงานภายในแอป FastAPI (`app.py`), `database.py`, และไลบรารีที่มีใน `requirements.txt` (`requests`, Python 3.11 stdlib `hmac`, `hashlib`, `secrets`, `urllib.parse`) โดยไม่เพิ่ม Node/TS backend หรือ Redis ภายนอก
- **ข้อจำกัดสำคัญ — ดิสก์ของ Cloudflare Container เป็นพื้นที่ชั่วคราว (Ephemeral Disk):**
  - ตามเอกสาร Cloudflare Containers ดิสก์ภายใน Container จะหายเมื่อ Container ถูกทำลายหลังว่างครบ 3 นาที (`SLEEP_AFTER_MS = 180000` ใน `worker.js`), เมื่อ Deploy เวอร์ชันใหม่, หรือเมื่อ Container restart หลังหมุนเปลี่ยน secret
  - ผลกระทบต่อ **Session (`auth_sessions`)**: หาก Container ถูกทำลาย session เดิมจะไม่พบในตารางใหม่และถูกปฏิเสธแบบ fail-closed (ผู้ใช้กด Sign in with Google ใหม่ได้ในคลิกเดียว)
  - ผลกระทบต่อ **ข้อมูลคลังเอกสาร/เด็ก/บิล (`data/documents.db` และ `data/uploads/`)**: ข้อมูลจริงที่อัปโหลดขึ้น Cloudflare Container ปัจจุบันจะหายทุกครั้งที่ Container ถูกทำลายหรือรีสตาร์ท **ดังนั้นสเปก Auth นี้แก้เรื่องการยืนยันตัวตนและความปลอดภัยของการเข้าถึงเท่านั้น แต่ยังไม่แก้เรื่องความคงทนของข้อมูลบนคลาวด์**
  - **เงื่อนไขก่อนใช้กับข้อมูลเด็ก/บิลจริง:**
    1. หากใช้งานจริงบนเครื่องส่วนตัว/เครื่องโรงเรียน (`./start.sh`): ข้อมูลอยู่บนดิสก์ถาวรของเครื่องจริง ปลอดภัยจากการรีสตาร์ท และสำรองด้วย `/api/database/download` ได้
    2. หากต้องการเก็บข้อมูลเด็ก/บิลจริงบน Cloudflare โดยไม่หายหลัง idle 3 นาทีหรือหลังหมุนเปลี่ยน secret: ต้องอนุมัติทำ **ADR + งานจัดเก็บถาวร (เช่น Sync/Backup `documents.db` และ `uploads/` ไปยัง Cloudflare R2)** แยกเป็นขั้นตอนถัดไปก่อนนำข้อมูลจริงขึ้นใช้งานบนคลาวด์
- **Backward Compatibility for Unit Tests:** `AppConfig.from_dir(...)` กำหนดค่าเริ่มต้น `auth_required=False` เพื่อให้ชุดทดสอบเดิมที่จำลองโฟลเดอร์ชั่วคราวทำงานได้ตามปกติ ส่วน `AppConfig.from_env()` กำหนดค่าเริ่มต้น `auth_required=True` อย่างเข้มงวด

---

## 4. Approaches Considered & Selected

1. **Approach 1 (Selected): Google OAuth 2.0 ใน FastAPI + SQLite-backed Revocable Session & Signed `HttpOnly` Cookie + LINE User ID Allowlist**
   - *Trade-offs:* ต้องสร้าง Google OAuth Client ID/Secret 1 ครั้ง และกำหนด `ALLOWED_LINE_USER_IDS` เพื่อปลดล็อกช่องทาง LINE OA แต่ไม่ต้องจำรหัสผ่าน รองรับการ revoke session บนเซิร์ฟเวอร์จริงเวลา Logout และปิดช่องทางเข้าถึงข้อมูลเด็กทั้งฝั่ง Web และ LINE
2. **Approach 2 (Rejected): Email + รหัสผ่านใน SQLite**
   - *Trade-offs:* ทำงานออฟไลน์ล้วนได้ แต่ต้องสร้างระบบตั้ง/เปลี่ยน/รีเซ็ตรหัสผ่านและเพิ่มภาระผู้ใช้ทั้ง 2 คน ทั้งที่ทั้งคู่ใช้ Gmail อยู่แล้ว
3. **Approach 3 (Rejected): Cloudflare Access (Zero Trust Email OTP / Google) หน้าด่านอย่างเดียว**
   - *Trade-offs:* ใช้กับ Local Dev (`./start.sh`) ไม่ได้ และไม่ช่วยตรวจสิทธิ์ `userId` ของผู้ส่งข้อความใน `/api/line/webhook`

---

## 5. Design Specification

### 5.1 Strict Configuration & Secrets (`config.py`, `.env.example`, `wrangler.toml`, `worker.js`)

เพิ่มฟิลด์และกฎการอ่านค่าใน `AppConfig` (`config.py`):

1. **`auth_required: bool` (Strict Fail-Closed Validation):**
   - ใน `AppConfig.from_dir(...)`: ค่าเริ่มต้น `False` (เฉพาะ unit test ที่ระบุโฟลเดอร์ชั่วคราวโดยตรง)
   - ใน `AppConfig.from_env()`: ใช้ตัวแปลงแบบเข้มงวด `_parse_strict_bool(raw, default=True, field_name="AUTH_REQUIRED")`:
     - หากไม่ได้ตั้งค่า (`None` หรือ `""`) -> คืน **`True`**
     - หากเป็น `"1"`, `"true"`, `"yes"`, `"on"` -> คืน **`True`**
     - หากเป็น `"0"`, `"false"`, `"no"`, `"off"` -> คืน **`False` เฉพาะเมื่อสภาพแวดล้อมเป็นโหมดพัฒนาในเครื่องเท่านั้น** (คือ `PUBLIC_BASE_URL` ว่าง หรือชี้ไป `http://127.0.0.1` / `http://localhost` และ `APP_ENV`/`ENVIRONMENT` ไม่ใช่ `production`) หากตั้ง `AUTH_REQUIRED=false` บนโดเมนจริง (`https://...` หรือ `APP_ENV=production`) ให้ **raise `ValueError` ทันที**
     - หากเป็นค่าอื่นที่ไม่รู้จัก (เช่น `"tru"`, `"enable"`, `"abc"`) -> **raise `ValueError` ทันที** เพื่อไม่ให้พิมพ์ผิดแล้วกลายเป็นการปิด Auth โดยไม่รู้ตัว
2. **ฟิลด์ Auth & LINE Access Control อื่น ๆ:**
   - `allowed_emails: Tuple[str, ...] = ()` — อ่านจาก `ALLOWED_EMAILS` คั่นด้วย `,` แล้ว normalize เป็นตัวพิมพ์เล็ก + trim
   - `google_client_id: str = ""` — อ่านจาก `GOOGLE_CLIENT_ID`
   - `google_client_secret: str = ""` — อ่านจาก `GOOGLE_CLIENT_SECRET`
   - `auth_session_secret: str = ""` — อ่านจาก `AUTH_SESSION_SECRET` (random secret >= 32 bytes สำหรับเซ็น Cookie)
   - `auth_session_ttl_seconds: int = 7 * 24 * 60 * 60` — ปรับได้ผ่าน `AUTH_SESSION_TTL_SECONDS` (ต้อง > 0)
   - `allowed_line_user_ids: Tuple[str, ...] = ()` — อ่านจาก `ALLOWED_LINE_USER_IDS` คั่นด้วย `,` และ trim ช่องว่าง
   - `line_allow_group_chats: bool = False` — อ่านจาก `LINE_ALLOW_GROUP_CHATS` ด้วย `_parse_strict_bool(..., default=False)` (รับเฉพาะแชท 1:1 เป็นค่าเริ่มต้น)

ใน `wrangler.toml` (`[vars]`):
- ตั้ง `APP_ENV = "production"`
- ตั้ง `AUTH_REQUIRED = "true"`
- ตั้ง `ALLOWED_EMAILS = "cgikchik07@gmail.com,ppksaruenee@gmail.com"`
- ตั้ง `LINE_ALLOW_GROUP_CHATS = "false"`
- ระบุ Secrets ที่ต้องตั้งผ่าน `npx wrangler secret put`:
  - `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `AUTH_SESSION_SECRET`, `ALLOWED_LINE_USER_IDS`, `LINE_CHANNEL_SECRET`, `LINE_CHANNEL_ACCESS_TOKEN`, `LLM_API_KEY`

ใน `worker.js`:
- **บังคับ Production Auth & Trusted Forwarded Context:**
  - ส่ง `APP_ENV: "production"` และ `AUTH_REQUIRED: "true"` (hard-enforced ใน Worker ไม่รับ override เป็น `"false"`) พร้อม `PUBLIC_BASE_URL` และค่า env/secrets ทั้งหมดเข้า `container.start({ env: { ... } })`
  - แนบ header `X-Forwarded-Proto` (เช่น `https` จาก `incomingUrl.protocol`) และ `X-Forwarded-Host` (`incomingUrl.host`) กับคำขอที่ส่งต่อไปยัง `http://container`
- **Secret Rotation ภายใน Durable Object เดิม (`idFromName("singleton")`):**
  - คงชื่อ Durable Object เดียว (`idFromName("singleton")`) เพื่อไม่สร้าง DO อินสแตนซ์ใหม่ทับซ้อนกันเมื่อเปลี่ยน secret
  - คำนวณ SHA-256 fingerprint จากค่า config/secret ปัจจุบันทั้งหมด แล้วเทียบกับค่า `configFingerprint` ที่บันทึกไว้ใน `this.ctx.storage`
  - หาก `configFingerprint` เปลี่ยน (ทั้งการตั้งครั้งแรกหรือการหมุนเปลี่ยนจากค่าเก่าเป็นค่าใหม่): สั่ง `await this.ctx.container.destroy()` บน DO เดิม (ถ้ากำลังรันอยู่), อัปเดต `configFingerprint` ใน `this.ctx.storage`, แล้วสั่ง `container.start(...)` ด้วยค่า env ชุดใหม่ทันที (หมายเหตุ: การ destroy container มีผลต่อดิสก์ชั่วคราวตามข้อ 3 จึงต้องสำรองข้อมูลหรือต่อ R2 ก่อนใช้กับข้อมูลจริงบนคลาวด์)

### 5.2 SQLite Session Table (`database.py`)

เพิ่มตาราง `auth_sessions` ใน `init_db(db_path)` แบบ idempotent (`CREATE TABLE IF NOT EXISTS`):
- `session_id TEXT PRIMARY KEY` (random 32-byte URL-safe ID)
- `email TEXT NOT NULL`
- `name TEXT DEFAULT ''`
- `picture TEXT DEFAULT ''`
- `csrf_token TEXT NOT NULL` (random 32-byte URL-safe token ผูกกับ session นี้)
- `created_at TEXT NOT NULL`
- `expires_at TEXT NOT NULL`
- `revoked_at TEXT DEFAULT ''`

ฟังก์ชันใน `database.py`:
- `create_auth_session(session_id, email, name, picture, csrf_token, expires_at, db_path=...)`
- `get_active_auth_session(session_id, now_iso=..., db_path=...)` — คืนแถวเฉพาะที่ `revoked_at = ''` และ `expires_at > now_iso`
- `revoke_auth_session(session_id, revoked_at=..., db_path=...)` — อัปเดต `revoked_at`
- `purge_expired_auth_sessions(now_iso=..., db_path=...)` — ล้างแถวที่หมดอายุแล้วเมื่อสร้าง session ใหม่

### 5.3 Authentication, Trusted External URL & CSRF (`auth_service.py`)

1. **การหา External Base URL และ HTTPS / `Secure` Cookie Policy:**
   - สร้างฟังก์ชัน `resolve_external_base_url(request, cfg)`:
     - **เมื่อตั้งค่า `cfg.public_base_url` ไว้** (เช่น บน production Cloudflare `https://nong-chokchuay-ocr.entrace-space.workers.dev`): ใช้ `cfg.public_base_url` เป็นฐานเสมอทั้งสำหรับ `redirect_uri`, allowed `Origin`, และตัดสินว่าเป็นการเชื่อมต่อ HTTPS หรือไม่ — **ไม่** ใช้ `request.base_url` ที่มาจาก `http://container` ภายใน
     - **เมื่อ `cfg.public_base_url` ว่าง (Local Dev):** ใช้ scheme + host ของ request เฉพาะเมื่อ host เป็น `127.0.0.1` หรือ `localhost` (ไม่เชื่อใจ `X-Forwarded-*` โดยอัตโนมัติหากไม่ได้ตั้ง `PUBLIC_BASE_URL` เพื่อกัน header spoofing)
   - การตั้งค่า `Secure` on Cookies (`nong_chok_session`, `nong_chok_csrf`, `nong_chok_oauth_state`):
     - เป็น `True` เสมอเมื่อ `cfg.public_base_url.startswith("https://")` หรือ `cfg.app_env == "production"` หรือ `request.url.scheme == "https"` (บน Local HTTP `http://127.0.0.1:8765` จะเป็น `False` เพื่อให้ทดสอบในเครื่องได้ ส่วนบน Cloudflare production จะบังคับ `Secure=True` เสมอแม้ Worker จะต่อเข้า Container ด้วย `http://container`)

2. **Signed Cookie & Server-Side Session Verification:**
   - **Session Cookie (`nong_chok_session`)**: เก็บ payload Base64URL (`sid`, `email`, `exp`) + ลายเซ็น HMAC-SHA256 ด้วย `AUTH_SESSION_SECRET` (ตรวจด้วย `hmac.compare_digest`)
   - การตรวจ session (`authenticate_request`):
     1. ตรวจว่า `auth_session_secret` และ `allowed_emails` ถูกตั้งค่าแล้ว
     2. ตรวจลายเซ็น HMAC และ `exp > now`
     3. ตรวจว่า `email.lower()` ยังอยู่ใน `cfg.allowed_emails`
     4. ตรวจ `sid` ในตาราง `auth_sessions` (`get_active_auth_session`) ว่ายังไม่ถูก `revoke` และยังไม่หมดอายุ

3. **Google OAuth 2.0 Endpoints:**
   - `GET /api/auth/me`: คืน `{ authenticated, auth_required, oauth_configured, csrf_token, user: { email, name, picture } | null }` (`csrf_token` คืนเฉพาะเมื่อ `authenticated == True`)
   - `GET /api/auth/google/login`: หาก config Auth ไม่ครบ -> redirect `/?auth_error=not_configured`; มิฉะนั้นเซ็ต `nong_chok_oauth_state` (`HttpOnly`, `SameSite=Lax`, `Secure=...`, 10 นาที) และ redirect ไป Google พร้อม `redirect_uri={external_base_url}/api/auth/google/callback`
   - `GET /api/auth/google/callback`: ตรวจ `state`, แลก `code` ที่ Google Token endpoint, ดึง `userinfo`, ตรวจ `email_verified is True` และ `email.lower() in cfg.allowed_emails`; หากผ่านสร้างแถวใน `auth_sessions` และเซ็ต `nong_chok_session` (`HttpOnly`, `SameSite=Lax`, `Secure=...`) + `nong_chok_csrf` (`SameSite=Lax`, `Secure=...`) แล้ว redirect กลับ `/`
   - `POST /api/auth/logout`: ผ่านการตรวจ CSRF + Origin check -> เรียก `database.revoke_auth_session(sid)` ใน SQLite ทันที -> ลบทั้ง `nong_chok_session` และ `nong_chok_csrf` cookie

4. **CSRF & Origin Validation (สำหรับทุก `POST`, `PUT`, `PATCH`, `DELETE` เมื่อ `auth_required=True`):**
   - ยกเว้นเฉพาะ `POST /api/line/webhook` (ซึ่งตรวจ `X-Line-Signature` + `ALLOWED_LINE_USER_IDS`)
   - สำหรับคำขอเปลี่ยนแปลงสถานะอื่นทั้งหมด:
     1. **Origin / Referer Check:** หากมี `Origin` (หรือ `Referer` เมื่อไม่มี `Origin`) ต้องตรงกับ allowed origin ที่คำนวณจาก `resolve_external_base_url(...)` เท่านั้น; หากขาดทั้ง `Origin` และ `Referer` หรือไม่ตรง -> ปฏิเสธ `403 Forbidden`
     2. **Session-bound CSRF Token Check:** Header `X-CSRF-Token` ต้องตรงกับ `csrf_token` ของ `auth_sessions` แถวปัจจุบันใน SQLite (`hmac.compare_digest`); หากขาดหรือผิด -> `403 Forbidden`

### 5.4 Route Protection & LINE OA Authorization (`app.py`, `line_service.py`)

1. **Web / API Allowlist เมื่อ `cfg.auth_required=True`:**
   - **Public (ไม่ต้องมี Session Cookie):**
     - `GET /` (SPA แสดง Login Gate เมื่อยังไม่ล็อกอิน; ไม่โหลดข้อมูลจริง)
     - `GET /api/health` (คืนเฉพาะ `status`, `brand`, `auth_required`, `oauth_configured`, `line_oa_configured`, `line_allowlist_configured` — ไม่มีข้อมูล DB/สถิติ)
     - `GET /api/auth/me`, `GET /api/auth/google/login`, `GET /api/auth/google/callback`
     - `POST /api/line/webhook` (ตรวจ `X-Line-Signature` + `ALLOWED_LINE_USER_IDS`)
     - ไฟล์ static เฉพาะ UI และภาพการ์ดสาธารณะ: `/static/styles.css`, `/static/app.js`, `/static/mascot.jpg`, `/static/mascot_cards/*`
   - **Protected (ต้องมี Session ถูกต้องใน `auth_sessions` และอีเมลใน `ALLOWED_EMAILS`; และหากเป็น `POST`/`PUT`/`PATCH`/`DELETE` ต้องผ่าน CSRF + Origin check ด้วย):**
     - `/api/auth/logout`, `/api/system`, `/api/samples`, `/api/sample/{filename}`, `/api/samples/kindergarten/{preset}`, `/api/documents*`, `/api/expenses*`, `/api/students*`, `/api/database/download`, `/api/watcher/*`, `/api/ocr`, `/api/line/simulate`
     - ไม่มี session / ถูก revoke / หมดอายุ -> `401 Unauthorized`

2. **LINE OA Sender Authorization (`/api/line/webhook` ใน `app.py`):**
   - เมื่อ `cfg.auth_required=True`:
     - หาก `LINE_CHANNEL_SECRET` ว่าง -> ปฏิเสธ `503 Service Unavailable`
     - ตรวจ `X-Line-Signature` ด้วย HMAC-SHA256 ก่อนเสมอ (ผิด -> `401`)
     - สำหรับแต่ละ event ใน `payload["events"]`:
       - ตรวจ `source_type` และ `user_id`
       - หาก `source_type != "user"` และ `cfg.line_allow_group_chats` เป็น `False` -> **ห้าม** อ่าน DB, รับไฟล์, หรือแก้ข้อมูล; ตอบปฏิเสธสั้น ๆ (หรือข้ามถ้าไม่มี `replyToken`)
       - หาก `cfg.allowed_line_user_ids` ว่าง หรือ `user_id` ไม่อยู่ใน `cfg.allowed_line_user_ids` -> **ห้าม** อ่าน DB, ดาวน์โหลดไฟล์, เรียก `process_line_text_command`, หรือเรียก `handle_line_postback`; ตอบกลับเฉพาะข้อความว่า *"ขออภัยครับ บัญชี LINE นี้ยังไม่ได้รับอนุญาตให้ใช้งานน้องโชคช่วยครับ"* (คืน HTTP `200` ตามสัญญา LINE Webhook พร้อมนับ `rejected_events`)

3. **CORS Hardening (`app.py`):**
   - เมื่อ `auth_required=True` จำกัด `allow_origins` เฉพาะ origin ที่อนุญาต (`cfg.public_base_url` ถ้ามี, `http://127.0.0.1:8765`, `http://localhost:8765`) แทน `*`

### 5.5 Frontend Login Gate, CSRF & Mobile LINE Support (`static/index.html`, `static/app.js`, `static/styles.css`, `line_service.py`)

1. **Login Gate & Session UX (`static/`):** เรียก `GET /api/auth/me` ก่อนโหลดข้อมูลใด ๆ; แสดงการ์ด Login โทนน้องโชคช่วยเมื่อยังไม่ล็อกอิน; เมื่อล็อกอินแล้วแสดงอีเมลผู้ใช้ + ปุ่มออกจากระบบ และแนบ `X-CSRF-Token` ใน `apiFetch` สำหรับ `POST`/`PUT`/`PATCH`/`DELETE` พร้อมเด้งกลับ Login Gate ทันทีเมื่อเจอ `401`
2. **LINE Flex Card External Browser (`line_service.py`, `static/app.js`) *(Agent's Technical Call)*:** เติม `openExternalBrowser=1` ในปุ่ม URI ของ Flex Card ใน `line_service.py` เพื่อเปิดใน Safari/Chrome แทน LINE WebView และแสดงคำแนะนำบนหน้า Login หากตรวจพบว่าเปิดใน WebView

### 5.6 What Is Left to the Builder

- รายละเอียด HTML/CSS ของการ์ด Login และ User Badge ใน Header
- การจัดแบ่ง helper functions ภายใน `auth_service.py` และชื่อไฟล์ทดสอบใน `tests/` (เช่น `tests/test_auth.py`)
- การ mock HTTP calls ของ Google OAuth ใน `tests/` (ต้องไม่เรียกเครือข่ายจริงตาม `CODING_STANDARDS.md`)

---

## 6. Verification & Rollout Plan

1. **Automated Tests (Local & CI):**
   - **Strict Fail-Closed Config Tests:**
     - `AppConfig.from_env()` เมื่อไม่มี env var ต้องได้ `auth_required=True`
     - `AUTH_REQUIRED=tru` (พิมพ์ผิด) ต้อง raise `ValueError`
     - `AUTH_REQUIRED=false` ร่วมกับ `PUBLIC_BASE_URL=https://...` หรือ `APP_ENV=production` ต้อง raise `ValueError`
   - **HTTPS / `Secure` Cookie & Worker Proxy Simulation:**
     - จำลอง request เข้า FastAPI แบบ `http://container/...` (เหมือนที่ `worker.js` ส่งเข้า Container) โดยตั้ง `PUBLIC_BASE_URL=https://nong-chokchuay-ocr.entrace-space.workers.dev` และ `APP_ENV=production`:
       - ยืนยันว่า `redirect_uri` ที่ส่งให้ Google ใช้ `https://nong-chokchuay-ocr.entrace-space.workers.dev/api/auth/google/callback`
       - ยืนยันว่า `Set-Cookie` ของ `nong_chok_session`, `nong_chok_csrf`, และ `nong_chok_oauth_state` มี attribute `Secure` และ `SameSite=Lax` ครบถ้วน
       - ยืนยันว่า `Origin: https://nong-chokchuay-ocr.entrace-space.workers.dev` ผ่านการตรวจ CSRF ในขณะที่ `Origin: http://container` หรือโดเมนอื่นถูกปฏิเสธ `403`
     - ตรวจ `worker.js` ด้วย Node test หรือ script ตรวจ syntax/พฤติกรรมว่าส่งต่อ `X-Forwarded-Proto`, `X-Forwarded-Host`, `AUTH_REQUIRED="true"`, และสั่งอัปเดต `configFingerprint` ใน DO เดิมเมื่อ secret เปลี่ยน
   - **Web/API Auth, Server-side Logout & CSRF:**
     - ทุก protected endpoint คืน `401` เมื่อไม่มี cookie
     - OAuth allowlist 2 อีเมลผ่าน, อีเมลอื่นหรือ `email_verified=False` ถูกปฏิเสธ
     - หลัง `POST /api/auth/logout` การส่ง cookie เดิมซ้ำต้องคืน `401`
     - คำขอ `POST`/`PUT`/`PATCH`/`DELETE` ที่ขาด/ผิด `X-CSRF-Token` หรือ `Origin` ผิด/หาย คืน `403`
   - **LINE OA Sender Authorization:**
     - ปฏิเสธไม่เรียก DB เมื่อ `ALLOWED_LINE_USER_IDS` ว่าง, `userId` ไม่อยู่ในรายการ, หรือมาจาก `group`/`room` (เมื่อ `line_allow_group_chats=False`)
     - อนุญาตครบทั้งอัปโหลดรูป คำสั่ง `"แพ้อาหาร"` / `"ยอดรอเบิก"` และ postback เมื่อมาจาก `userId` ที่ได้รับอนุญาตในแชท 1:1
   - **Regression:** ชุดทดสอบเดิมทั้งหมดต้องผ่านครบ
2. **Pre-Production Data Persistence Gate (ก่อนใช้ข้อมูลเด็ก/บิลจริงบน Cloudflare):**
   - ต้องตัดสินใจร่วมกับเจ้าของโปรเจกต์ระหว่าง:
     - **A:** ใช้ Auth + ระบบนี้เก็บข้อมูลจริงบนเครื่องส่วนตัว/โรงเรียน (`./start.sh`) ก่อน และใช้ Cloudflare สำหรับทดสอบ/เดโม จนกว่าจะทำระบบจัดเก็บถาวร (เช่น R2) เสร็จ
     - **B:** อนุมัติออกแบบ ADR + ระบบ Backup/Restore (`data/documents.db` + `data/uploads/`) บน Cloudflare R2 ต่อจากงาน Auth ก่อนเปิดใช้งานข้อมูลจริงบน Cloudflare
3. **Owner Setup & Verification After Deploy:**
   - สร้าง Google OAuth 2.0 Web Client และเพิ่ม Redirect URIs (`https://nong-chokchuay-ocr.entrace-space.workers.dev/api/auth/google/callback`, `http://127.0.0.1:8765/api/auth/google/callback`, `http://localhost:8765/api/auth/google/callback`)
   - ตั้ง Secrets บน Cloudflare (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `AUTH_SESSION_SECRET`, `ALLOWED_LINE_USER_IDS`, `LINE_CHANNEL_SECRET`, `LINE_CHANNEL_ACCESS_TOKEN`) แล้ว deploy
   - ทดสอบบน URL จริงผ่าน Worker (`https://nong-chokchuay-ocr.entrace-space.workers.dev`): ตรวจ `/api/health`, Google Login, attribute `Secure` ของ cookie ในเบราว์เซอร์, Logout + cookie เก่าใช้ไม่ได้, CSRF check, และส่งข้อความทดสอบผ่าน LINE จากทั้งบัญชีที่อนุญาตและบัญชีอื่น
