# STATUS.md — สถานะงานปัจจุบัน งานค้าง และข้อจำกัดของระบบ

อัปเดตล่าสุด: 2026-11-15

---

## 1. ภาพรวมสถานะปัจจุบัน (Current State)

โปรเจกต์ **"น้องโชคช่วย" (Nong ChokChuay) / OpenThai 2.0 OCR App** อยู่ในสถานะพร้อมใช้งานทั้งในเครื่อง (Local) และพร้อมสำหรับ Deploy บน Cloudflare Workers Paid + Cloudflare Containers (`https://nong-chokchuay-ocr.entrace-space.workers.dev`)

### ความสามารถที่เปิดใช้งานและมีชุดทดสอบรองรับแล้ว:
- **คลังเอกสาร OCR ภาษาไทย (Vault & FTS5):** อ่านข้อความภาษาไทย-อังกฤษจากภาพและ PDF ด้วยโมเดล OpenThai 2.0 ONNX บน CPU, เก็บไฟล์ต้นฉบับใน `data/uploads/` และค้นหาด้วย SQLite Trigram FTS5
- **ถาดรอตรวจ (Review Queue):** แยกเอกสารที่เพิ่งสแกน (`review_status = 'pending_review'`) ให้คุณครูกดตรวจยืนยันได้ในคลิกเดียว (`'reviewed'`)
- **สมุดเบิกจ่ายคุณครูอนุบาล (Expense & Reimbursement Tracker):** สกัดข้อมูลใบเสร็จ แยกสถานะรอเบิกคืนโรงเรียน (`pending_reimbursement`), เบิกคืนแล้ว (`reimbursed`), และรายจ่ายส่วนตัว (`personal`) พร้อมส่งออก CSV (UTF-8 BOM)
- **แฟ้มทะเบียนเด็กอนุบาลและแจ้งเตือนแพ้อาหาร (Student Profiles):** สกัดชื่อ-นามสกุล ชื่อเล่น เลขบัตรประชาชน 13 หลัก วันเกิด (พ.ศ. → ค.ศ.) ห้องเรียน เบอร์ผู้ปกครอง และป้ายเตือนแพ้อาหาร/โรคประจำตัว
- **ช่องทาง LINE OA และตัวจำลองแชท (LINE Webhook & Web Simulator):** รับภาพและคำสั่งค้นหาผ่าน `/api/line/webhook` (ตอบกลับฟรีด้วย `replyToken` เป็น Flex Card พร้อมปุ่ม Postback และ `openExternalBrowser=1`) และจำลองการคุยกับ "ครูพิ้งกี้" บนหน้าเว็บผ่าน `/api/line/simulate`
- **ระบบยืนยันตัวตนและความปลอดภัย (Google OAuth + LINE Allowlist):** จำกัดสิทธิ์เข้าใช้งานเว็บเฉพาะอีเมล Google ที่ยืนยันแล้ว 2 บัญชี (`ALLOWED_EMAILS`), เก็บ session ใน SQLite (`auth_sessions`) พร้อม `HttpOnly` cookie, ตรวจ Origin + CSRF token (`X-CSRF-Token`), ยกเลิก session ทันทีเมื่อ Logout, ปฏิเสธการเข้าถึงเป็นค่าเริ่มต้นเมื่อเปิด `AUTH_REQUIRED=true` (fail-closed) รวมถึงจำกัดผู้ส่ง LINE OA ด้วย `ALLOWED_LINE_USER_IDS` และแชทส่วนตัว 1-on-1 (`LINE_ALLOW_GROUP_CHATS=false`)
- **ระบบจัดเก็บข้อมูลถาวรบน Cloudflare R2 (`storage_service.py` + `VaultR2Gateway`, ADR 0003):** เมื่อรันบน Cloud (`R2_REQUIRED=true` / `VAULT_BUCKET = nong-chokchuay-vault`) ระบบกู้ snapshot SQLite ที่ผ่านการตรวจ SHA-256 และ schema (`user_version = 3`) ก่อนเปิดรับคำขอ, บันทึกไฟล์ต้นฉบับและ versioned SQLite snapshot ผ่าน conditional ETag manifest commit (`If-Match` / `If-None-Match: *`) ก่อนตอบสำเร็จหรือตอบ LINE, ดึงไฟล์ต้นฉบับกลับแบบ lazy พร้อมตรวจ SHA-256 และรองรับการลบไฟล์แบบรอรอบ retention (`SNAPSHOT_RETENTION_COUNT = 5`)
- **ตัวสกัดข้อมูลแบบผสม (Hybrid Extractor):** ใช้ Offline Thai Regex เป็นฐานหลัก และรองรับ Text-only LLM adapter (`gemini-2.5-flash` เป็นค่าเริ่มต้นเมื่อตั้งค่า `LLM_API_KEY` หรือ `GEMINI_API_KEY`)
- **ระบบกติกาการทำงานของ AI Agent:** ตั้งค่า `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `INDEX.md`, `GLOSSARY.md`, `CODING_STANDARDS.md`, `docs/STATUS.md`, `docs/adr/0003-cloudflare-r2-sqlite-persistence.md` และ `docs/release-workflow.md` ครบชุด

---

## 2. สิ่งที่ค้างอยู่และงานถัดไป (Pending Items)

1. **ตั้งค่า Google OAuth 2.0 Credentials และค่าลับบน Cloudflare (Wrangler Secrets):**
   - สร้าง OAuth Client ID (Web application) ใน Google Cloud Console พร้อม Authorized redirect URI: `https://nong-chokchuay-ocr.entrace-space.workers.dev/api/auth/google/callback`
   - ตั้งค่า secret ด้วย Wrangler ก่อนใช้งานจริง: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `AUTH_SESSION_SECRET`, `ALLOWED_LINE_USER_IDS`, `LINE_CHANNEL_SECRET`, `LINE_CHANNEL_ACCESS_TOKEN`, และ `LLM_API_KEY`
2. **สร้างและ Bootstrap R2 Bucket ส่วนตัวครั้งแรกหลัง Merge และได้รับอนุมัติ Deploy:**
   - สร้าง private R2 bucket ชื่อ `nong-chokchuay-vault` (ค่าตั้งต้นตรงกับ `wrangler.toml`) และคงการปิด Public Access
   - สำหรับครั้งแรกเมื่อ bucket ยังว่าง ให้ deploy ชั่วคราวพร้อม `R2_BOOTSTRAP_IF_EMPTY="true"` และเปิดหน้าเว็บหรือเรียก `/api/health` เพื่อสร้าง snapshot รุ่นแรก แล้วสลับ `R2_BOOTSTRAP_IF_EMPTY="false"` ทันทีเพื่อป้องกันการสร้างฐานว่างทับข้อมูลเดิมในอนาคต
   - **ตรวจสถานะจริงหลังวางระบบ:** ปัจจุบันโค้ด R2 persistence พร้อมทดสอบครบผ่าน Mock/Loopback R2 แล้ว ยังรอการอนุมัติจากเจ้าของเพื่อ merge PR และทดสอบกับ R2 จริงบน Cloudflare
3. **การเชื่อมต่อ LINE Webhook จริง:**
   - ผูก Webhook URL ของ LINE Developers Console เข้ากับ `<PUBLIC_BASE_URL>/api/line/webhook`, เพิ่ม LINE `userId` ทั้ง 2 บัญชีลงใน `ALLOWED_LINE_USER_IDS` และทดสอบส่งรูปบิล/ใบสมัครจากแอป LINE จริง (รวมถึงปุ่มเปิดเบราว์เซอร์ภายนอก)

---

## 3. ข้อจำกัดของระบบที่ต้องทราบ (Known Limitations)

- **เวลาเริ่มทำงานของ Cloudflare Container (Cold Start):** เนื่องจากตั้งค่า scale-to-zero เมื่อไม่มีการใช้งานครบ 3 นาที (`SLEEP_AFTER_MS = 180000` ใน `worker.js`) คำขอแรกหลังจากที่ Container หลับจะใช้เวลารอ Container ตื่นและกู้ snapshot จาก R2 ประมาณ 3–8 วินาที (ซึ่ง `worker.js` มีกลไกลองส่งคำขอซ้ำให้อัตโนมัติสูงสุด 25 ครั้ง และ `alarm()` จะเช็ก `_activeRequests` รวมถึงสถานะ `busy` / `RESTORING` จาก `/api/health` ก่อน `destroy()`)
- **ความทนทานของข้อมูลและงบประมาณ Cloudflare:** ในโหมด R2 (`R2_REQUIRED=true`) ข้อมูลเอกสาร, บิล, ทะเบียนเด็ก และ `auth_sessions` จะถูก commit ลง private R2 ก่อนตอบสำเร็จ จึงรอดจากการหลับ 3 นาทีและการ redeploy ได้ ส่วนค่าบริการ **Workers Paid (USD 5/เดือน)** เป็นค่าพื้นฐานพร้อมโควตาในตัว (รวม R2 free tier 10 GB-month, 1M Class A writes, 10M Class B reads) ไม่ใช่เพดานรับรองค่าใช้จ่ายสูงสุดหากใช้งานเกินโควตา
- **ข้อจำกัดของ LINE Reply Token:** LINE Messaging API กำหนดให้ใช้ `replyToken` ได้เพียงครั้งเดียว **ภายใน 1 นาที** และควรตอบกลับให้เร็วที่สุด ดังนั้นหาก cold start + OCR + R2 commit ใช้เวลาเกินกำหนด `replyToken` ของข้อความแรกอาจหมดอายุได้ (ระบบจะยังบันทึกสำเร็จหรือไม่ส่งผลหลอก แต่ข้อความตอบกลับผ่าน Reply API อาจไม่ถึงผู้ใช้)
- **โหมดพัฒนาในเครื่อง (Local Dev):** เมื่อไม่ได้ตั้ง `R2_GATEWAY_URL` (`R2_REQUIRED=false`) ระบบจะทำงานกับ SQLite และ `data/uploads/` ในเครื่องตามปกติโดยไม่ต้องใช้ R2 และหากต้องการพัฒนา UI แบบไม่ต้องล็อกอินบน `localhost`/`127.0.0.1` ให้ตั้ง `APP_ENV=development` และ `AUTH_REQUIRED=false` ใน `.env` ในเครื่องเท่านั้น
