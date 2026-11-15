# INDEX.md — แผนผังไฟล์และสถาปัตยกรรมระบบ

เอกสารนี้เป็นแผนที่หลักของโปรเจกต์ **"น้องโชคช่วย" (Nong ChokChuay) / OpenThai 2.0 OCR App** สำหรับใช้ค้นหาตำแหน่งไฟล์และเข้าใจเส้นทางข้อมูลก่อนเปิดอ่านไฟล์ขนาดใหญ่

---

## 1. แผนผังการไหลของข้อมูล (Architecture Flow)

```
[ ช่องทางนำเข้า (Ingestion Channels) ]
  ├── Web / LIFF Upload  (/api/ocr, /api/ocr/sample/{preset})
  ├── Watch Folder       (watcher.py -> data/inbox/{teacher,me}/)
  └── LINE OA Webhook    (line_service.py -> /api/line/webhook, /api/line/simulate)
            │
            ▼
[ ตรรกะนำเข้าและจัดเก็บถาวร: ocr_service.py + storage_service.py ]
  ├── 1. เตรียมและสกัดข้อความ OpenThai 2.0 ONNX บน CPU (model/runtime/line_engine.py)
  ├── 2. สกัดข้อมูลอัตโนมัติ (extractor.py: Offline Thai Regex + Optional Text-only LLM)
  ├── 3. อัปโหลดไฟล์ต้นฉบับ (Local data/uploads/ และ/หรือ private R2 vault/uploads/)
  ├── 4. บันทึก SQLite + FTS5 (database.py -> data/documents.db)
  └── 5. เมื่อเปิด R2 (Cloud): ส่ง SQLite snapshot + commit vault/manifest.json แบบ conditional ETag ก่อนตอบสำเร็จ
            │
            ├──► ตาราง documents + documents_fts (คลังเอกสาร & Review Queue)
            ├──► ตาราง expenses                  (สมุดเบิกจ่ายคุณครู & งบโรงเรียน)
            └──► ตาราง students                  (แฟ้มทะเบียนเด็กอนุบาล & แพ้อาหาร)
```

---

## 2. โมดูลหลักฝั่ง Backend (Python / FastAPI)

| ไฟล์ | หน้าที่หลัก | จุดเชื่อมต่อสำคัญ |
| :--- | :--- | :--- |
| `app.py` | FastAPI Web Server และ REST API ทั้งหมด (Auth Middleware, Google OAuth, OCR, Vault, Review Queue, Expenses, Students, Watcher, LINE Webhook/Simulator) | เรียกใช้ `auth_service`, `VaultService`, `VaultDatabase`, `InboxWatcher`, `LineService` |
| `auth_service.py` | ระบบยืนยันตัวตน Google OAuth 2.0, ตรวจสิทธิ์ 2 อีเมล, Cookie session (HMAC-SHA256 + SQLite `auth_sessions`), และตรวจ Origin/CSRF | เชื่อม `app.py`, `database.py`, และ `config.py` |
| `config.py` | โหลดค่าตั้งต้นจาก `.env` และ Environment Variables สร้างเป็น `AppConfig` (แบบ fail-closed สำหรับค่า Auth และตั้งค่า R2 Gateway) | กำหนดพาธ `data/`, `AUTH_REQUIRED`, `ALLOWED_EMAILS`, Google OAuth, `ALLOWED_LINE_USER_IDS`, `R2_GATEWAY_URL`, `R2_REQUIRED`, `R2_BOOTSTRAP_IF_EMPTY`, `SNAPSHOT_RETENTION_COUNT`, LLM และ `TEACHER_NAME` |
| `database.py` | จัดการ SQLite (`data/documents.db`), FTS5 trigram index, CRUD และการทำ migration อัตโนมัติแบบรันซ้ำได้ (`PRAGMA user_version = 3`) | ตาราง `documents`, `documents_fts`, `expenses`, `students`, `auth_sessions`, `vault_operations`, `pending_r2_deletions`, ฟังก์ชัน snapshot/validate และส่งออก CSV (UTF-8 BOM) |
| `storage_service.py` | จัดการการจัดเก็บถาวรบน Cloudflare R2 (`VaultStorageCoordinator`, `R2GatewayClient`): startup restore gate, lock, rollback, conditional ETag manifest commit และ lazy restore | เชื่อม `app.py`, `ocr_service.py`, `watcher.py`, `line_service.py`, `auth_service.py` เข้ากับ Worker `VaultR2Gateway` (`http://r2.internal`) |
| `ocr_service.py` | ท่อประมวลผลกลาง (`VaultService`) สำหรับทุกช่องทางนำเข้า จัดการไฟล์ต้นฉบับ เรียก OCR และเชื่อมตัวสกัดข้อมูล | เชื่อม `LineOCR`, `VaultDatabase`, และ `extractor.py` |
| `extractor.py` | สกัดข้อมูลใบเสร็จ/บิลเบิกจ่าย หมวดหมู่เอกสาร ป้ายกำกับอัตโนมัติ และข้อมูลทะเบียนเด็กอนุบาล | รองรับทั้งโหมด Offline Thai Regex (`regex`) และ Text-only LLM (`llm` ผ่าน Gemini หรือ OpenAI-compatible API) |
| `line_service.py` | จัดการ LINE Messaging API Webhook, ตรวจ `X-Line-Signature`, ตรวจสิทธิ์ `ALLOWED_LINE_USER_IDS`/DM-only, แนบ `openExternalBrowser=1`, สร้าง Mascot Messaging Cards ทั้ง 7 สถานการณ์ (Bubble/Carousel + Quick Replies), จับคำถามธรรมชาติเรื่องยอดเบิก/แพ้อาหาร/รอตรวจก่อนเรียก LLM และจำลองแชท | เชื่อม `/api/line/webhook` และ `/api/line/simulate` เข้ากับ `VaultService` และ `VaultStorageCoordinator` |
| `watcher.py` | ตรวจจับไฟล์ใหม่ใน `data/inbox/` (รวมถึงโฟลเดอร์ย่อย `teacher/` และ `me/`) เพื่อนำเข้าคลังอัตโนมัติ และแยกไฟล์เสียเข้า `data/inbox_failed/` | รันเป็น Background thread ใน `app.py` หรือสั่งสแกนทันทีผ่าน `/api/watcher/scan-now` |

---

## 3. โมเดล OCR และ Runtime (`model/`)

| ไฟล์ | หน้าที่หลัก |
| :--- | :--- |
| `model/det.onnx` | โมเดลตรวจจับบรรทัดข้อความ (PP-OCRv6-tiny ขนาด ~1.8 MB) |
| `model/rec.onnx` | โมเดลอ่านตัวอักษรภาษาไทย-อังกฤษแบบ CTC (ขนาด ~2.4 MB) |
| `model/rec_dict.txt` | พจนานุกรมตัวอักษร 528 ตัวสำหรับถอดรหัสผลลัพธ์จาก `rec.onnx` |
| `model/config.json` | ค่าพารามิเตอร์และ threshold ของการตรวจจับและจัดเรียงบรรทัด |
| `model/runtime/line_engine.py` | คลาส `LineOCR` สำหรับรัน ONNX Runtime บน CPU, ปรับภาพเอียง (deskew), หมุนภาพอัตโนมัติ และจัดเรียงลำดับการอ่านหลายคอลัมน์ |
| `model/runtime/thai_norm.py` | จัดระเบียบลำดับสระและวรรณยุกต์ภาษาไทยให้เป็นมาตรฐานเดียวกัน |

---

## 4. ส่วนติดต่อผู้ใช้ฝั่งเว็บ (`static/`)

| ไฟล์ | หน้าที่หลัก |
| :--- | :--- |
| `static/index.html` | โครงสร้างหน้าเว็บหลักของ "น้องโชคช่วย" (Login Gate, แถบผู้ใช้/ออกจากระบบ, อัปโหลดเอกสาร, ถาดรอตรวจ, สมุดเบิกจ่าย, แฟ้มเด็กอนุบาล, และ LINE Simulator) |
| `static/styles.css` | สไตล์หน้าเว็บโทน Sunny Sky & Golden Hour รองรับทั้งหน้าจอคอมพิวเตอร์และมือถือ/LIFF |
| `static/app.js` | ตรรกะฝั่งเบราว์เซอร์ (Vanilla JS): ตรวจ `/api/auth/me` ก่อนโหลดข้อมูล, แนบ `X-CSRF-Token`, เตือน LINE WebView, อัปโหลดไฟล์, วาดกรอบ Bounding Box, แก้ไขข้อมูล และเรนเดอร์ Mascot Messaging Cards + Quick Replies ใน LINE Simulator |
| `static/mascot.jpg` | ภาพมาสคอตประจำระบบ "น้องโชคช่วย" บนหน้าเว็บหลัก |
| `static/mascot_cards/` | ชุดภาพประกอบมาสคอต "น้องโชคช่วย" พื้นหลังใส 9 ไฟล์ (`avatar.png` และ `hero_*.png`) สำหรับการ์ดข้อความ LINE OA ทั้ง 7 สถานการณ์ |

---

## 5. การรันเซิร์ฟเวอร์, Container และการ Deploy

| ไฟล์ | หน้าที่หลัก |
| :--- | :--- |
| `start.sh` | สคริปต์รันเซิร์ฟเวอร์ในเครื่องที่ `http://127.0.0.1:8765` |
| `requirements.txt` | รายการแพ็กเกจ Python ที่ต้องใช้ (FastAPI, Uvicorn, ONNX Runtime, OpenCV, PyMuPDF ฯลฯ) |
| `Dockerfile` | สร้างอิมเมจ Python 3.11-slim พร้อมไลบรารีระบบและโมเดล ONNX สำหรับรันบนพอร์ต `8765` |
| `worker.js` | Cloudflare Worker (`VaultR2Gateway` + Durable Object `NongChokChuayOCR` แบบ `singleton`) บังคับ Auth/HTTPS header, ยืนยัน internal sync token/epoch, คั่นคำขอ `http://r2.internal`, เช็ก `busy`/`RESTORING` ใน `alarm()`, และรีสตาร์ต Container เมื่อ secret เปลี่ยน |
| `wrangler.toml` | ค่าตั้งต้นสำหรับ Deploy ขึ้น Cloudflare Workers Paid (ค่าพื้นฐาน $5/เดือน พร้อมโควตา) + Containers (`max_instances = 1`) + Private R2 Bucket (`VAULT_BUCKET` / `nong-chokchuay-vault`) |
| `.github/workflows/deploy-cloudflare.yml` | GitHub Actions CI/CD สำหรับรันชุดทดสอบ Python + Node (`worker.js`, `static/app.js`) และสั่ง `npx wrangler deploy` เมื่อมี `CLOUDFLARE_API_TOKEN` |

---

## 6. เอกสารกติกา สถาปัตยกรรม และการติดตามงาน

| ไฟล์ / โฟลเดอร์ | หน้าที่หลัก |
| :--- | :--- |
| `AGENTS.md` | กติกาการทำงานหลักของ AI Agent ทุกตัวในโปรเจกต์ |
| `CLAUDE.md`, `GEMINI.md` | ไฟล์ชี้กลับไปที่ `AGENTS.md` สำหรับเครื่องมือตระกูล Claude และ Gemini / Antigravity |
| `docs/STATUS.md` | สถานะงานปัจจุบัน สิ่งที่ทำเสร็จแล้ว งานค้าง และข้อจำกัดของระบบ |
| `GLOSSARY.md`, `CONTEXT.md` | พจนานุกรมคำศัพท์โดเมนมาตรฐานของโปรเจกต์ |
| `CODING_STANDARDS.md` | มาตรฐานการเขียนโค้ด การจัดการข้อมูล AI/ค่าลับ และเกณฑ์รีวิว diff |
| `docs/release-workflow.md` | ขั้นตอนการเตรียมงาน รันตรวจ เปิด PR, CI, merge, deploy และตรวจระบบจริง |
| `docs/adr/` | บันทึกการตัดสินใจทางสถาปัตยกรรม (`0001`, `0002`, และ `0003`) |
| `docs/agents/` | คำแนะนำเสริมสำหรับการใช้คำศัพท์โดเมน (`domain.md`) และระบบติดตามงานในเครื่อง (`issue-tracker.md`) |
| `.scratch/` | บันทึกสเปกและรายการงานย่อยรายฟีเจอร์ในเครื่อง |

---

## 7. ชุดทดสอบและข้อมูลตัวอย่าง (`tests/`, `samples/`, `data/`)

| ไฟล์ / โฟลเดอร์ | หน้าที่หลัก |
| :--- | :--- |
| `tests/fakes.py` | ตัวจำลอง OCR (`FakeOCR`) และตัวช่วยสร้างข้อมูลทดสอบโดยไม่ต้องโหลดโมเดลจริงทุกเคส |
| `tests/test_smoke.py` | ทดสอบพื้นฐานของ API และหน้าเว็บหลัก |
| `tests/test_real_ocr_smoke.py` | ทดสอบรันโมเดล OpenThai 2.0 ONNX จริงกับไฟล์ภาพตัวอย่าง |
| `tests/test_vault_api.py` | ทดสอบการจัดเก็บเอกสาร Source File, การค้นหา FTS5, ป้ายกำกับ และการแก้ไขข้อมูล |
| `tests/test_watcher.py` | ทดสอบระบบ Watch Folder (`data/inbox/`, โฟลเดอร์ย่อย และการกักไฟล์เสีย) |
| `tests/test_extractor.py` | ทดสอบการสกัดข้อมูลใบเสร็จและหมวดหมู่ด้วย Offline Thai Regex |
| `tests/test_llm_hybrid.py` | ทดสอบ Text-only LLM adapter และการ fallback กลับมาใช้ Regex เมื่อ LLM ไม่พร้อม |
| `tests/test_nong_chokchuay.py` | ทดสอบฟีเจอร์คุณครูอนุบาลครบวงจร (การเบิกจ่าย, ทะเบียนเด็กอนุบาล, ถาดรอตรวจ, LINE Webhook/Simulator) |
| `tests/test_auth.py` | ทดสอบการยืนยันตัวตน Google OAuth, `auth_sessions`, CSRF/Origin, LINE sender allowlist และสัญญา UI |
| `tests/test_frontend_auth.mjs` | ทดสอบการทำงานจริงของ `static/app.js` ใน Node VM (login gate, `X-CSRF-Token`, และการล้างข้อมูลเมื่อ session หมดอายุ/401) |
| `tests/test_r2_persistence.py` | ทดสอบการจัดเก็บถาวรบน R2 (restart recovery, LINE/Watch Folder commit gate, rollback, ETag 412 conflict, corrupt/future-schema gate, lazy restore และ retention GC) |
| `tests/test_worker_js.mjs` | ทดสอบ `worker.js` (`VaultR2Gateway`, `VAULT_BUCKET`, `AUTH_REQUIRED="true"`, single DO `singleton`, header stripping และ `alarm()` `busy`/`RESTORING`) |
| `samples/` | ภาพเอกสารตัวอย่างสำหรับกดทดลองใช้งานทันที |
| `data/` | โฟลเดอร์เก็บข้อมูลจริงในเครื่อง (`documents.db`, `uploads/`, `inbox/`, `inbox_failed/`) — **ชุดทดสอบห้ามเขียนทับโฟลเดอร์นี้** |
