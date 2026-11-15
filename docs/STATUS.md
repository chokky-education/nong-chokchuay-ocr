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
- **ช่องทาง LINE OA และตัวจำลองแชท (LINE Webhook & Web Simulator):** รับภาพและคำสั่งค้นหาผ่าน `/api/line/webhook` (ตอบกลับฟรีด้วย `replyToken` เป็น Flex Card พร้อมปุ่ม Postback และ `openExternalBrowser=1`) และจำลองการคุยกับ "ครูพิ้งกี้" บนหน้าเว็บผ่าน `/api/line/simulate` ด้วยบุคลิกผู้ช่วยครูอนุบาลรุ่นน้องคู่ใจที่เป็นธรรมชาติ (`gemini-4-hydrogen`)
- **ระบบยืนยันตัวตนและความปลอดภัย (Google OAuth + LINE Allowlist):** จำกัดสิทธิ์เข้าใช้งานเว็บเฉพาะอีเมล Google ที่ยืนยันแล้ว 2 บัญชี (`ALLOWED_EMAILS`), เก็บ session ใน SQLite (`auth_sessions`) พร้อม `HttpOnly` cookie, ตรวจ Origin + CSRF token (`X-CSRF-Token`), ยกเลิก session ทันทีเมื่อ Logout, ปฏิเสธการเข้าถึงเป็นค่าเริ่มต้นเมื่อเปิด `AUTH_REQUIRED=true` (fail-closed) รวมถึงจำกัดผู้ส่ง LINE OA ด้วย `ALLOWED_LINE_USER_IDS` และแชทส่วนตัว 1-on-1 (`LINE_ALLOW_GROUP_CHATS=false`)
- **ตัวสกัดข้อมูลแบบผสม (Hybrid Extractor):** ใช้ Offline Thai Regex เป็นฐานหลัก และรองรับ Text-only LLM เสริม (`gemini-4-hydrogen` เป็นค่าเริ่มต้นพร้อมตัวกรองโหมดคิดวิเคราะห์ `thinking` เมื่อตั้งค่า `LLM_API_KEY` หรือ `GEMINI_API_KEY`)
- **ระบบกติกาการทำงานของ AI Agent:** ตั้งค่า `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `INDEX.md`, `GLOSSARY.md`, `CODING_STANDARDS.md`, `docs/STATUS.md` และ `docs/release-workflow.md` ครบชุด

---

## 2. สิ่งที่ค้างอยู่และงานถัดไป (Pending Items)

1. **ตั้งค่า Google OAuth 2.0 Credentials และค่าลับบน Cloudflare (Wrangler Secrets):**
   - สร้าง OAuth Client ID (Web application) ใน Google Cloud Console พร้อม Authorized redirect URI: `https://nong-chokchuay-ocr.entrace-space.workers.dev/api/auth/google/callback`
   - ตั้งค่า secret ด้วย Wrangler ก่อนใช้งานจริง: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `AUTH_SESSION_SECRET`, `ALLOWED_LINE_USER_IDS`, `LINE_CHANNEL_SECRET`, `LINE_CHANNEL_ACCESS_TOKEN`, และ `LLM_API_KEY`
2. **จัดเก็บข้อมูลถาวรก่อนนำข้อมูลเด็ก/บิลจริงขึ้น Cloudflare:**
   - **เงื่อนไขบังคับ:** ปัจจุบันฐานข้อมูล SQLite (`data/documents.db`) และไฟล์ต้นฉบับยังอยู่บนดิสก์ชั่วคราวของ Container จึงอนุญาตเฉพาะการทดลอง/สาธิตบน Cloudflare จนกว่าจะมี ADR และระบบสำรอง/จัดเก็บถาวร (เช่น R2) เพื่อไม่ให้ข้อมูลหายเมื่อ Container หลับครบ 3 นาทีหรือรีสตาร์ต
3. **การเชื่อมต่อ LINE Webhook จริง:**
   - ผูก Webhook URL ของ LINE Developers Console เข้ากับ `<PUBLIC_BASE_URL>/api/line/webhook`, เพิ่ม LINE `userId` ทั้ง 2 บัญชีลงใน `ALLOWED_LINE_USER_IDS` และทดสอบส่งรูปบิล/ใบสมัครจากแอป LINE จริง (รวมถึงปุ่มเปิดเบราว์เซอร์ภายนอก)

---

## 3. ข้อจำกัดของระบบที่ต้องทราบ (Known Limitations)

- **เวลาเริ่มทำงานของ Cloudflare Container (Cold Start):** เนื่องจากตั้งค่า scale-to-zero เมื่อไม่มีการใช้งานครบ 3 นาที (`SLEEP_AFTER_MS = 180000` ใน `worker.js`) คำขอแรกหลังจากที่ Container หลับจะใช้เวลารอ Container ตื่นประมาณ 3–8 วินาที (ซึ่ง `worker.js` มีกลไกลองส่งคำขอซ้ำให้อัตโนมัติสูงสุด 25 ครั้ง)
- **อายุข้อมูลใน Container ชั่วคราวบน Cloudflare (Demo/Test Only):** ดิสก์ภายใน Cloudflare Container เป็นพื้นที่ชั่วคราว (ephemeral) ข้อมูลใน `data/documents.db` (รวมถึง `auth_sessions` และเอกสาร/ข้อมูลเด็ก) และ `data/uploads/` จะหายเมื่อ Container ถูก `destroy()` หลังว่าง 3 นาที ระหว่าง redeploy หรือเมื่อ `worker.js` รีสตาร์ต Container จากการหมุน secret จึง**ยังไม่ควรใช้กับข้อมูลเด็กหรือบิลจริงบน Cloudflare** จนกว่าจะมีระบบจัดเก็บถาวรพร้อม ADR ใหม่
- **ข้อจำกัดของ LINE Reply Token:** การตอบกลับฟรีผ่าน `replyToken` ใช้ได้เฉพาะการตอบกลับทันทีเมื่อมีเหตุการณ์เข้ามาจากผู้ใช้ หาก Container ใช้เวลาตื่นนานเกินอายุของ `replyToken` การตอบกลับอัตโนมัติในข้อความแรกอาจไม่ทันเวลา
- **โหมดพัฒนาในเครื่อง (Local Dev):** การรัน `./start.sh` โดยไม่ตั้ง `AUTH_REQUIRED` ยังคงต้องมี `ALLOWED_EMAILS` + Google OAuth + `AUTH_SESSION_SECRET` หากเปิด `AUTH_REQUIRED=true` (ค่าเริ่มต้นของ `AppConfig.from_env()`) หรือหากต้องการพัฒนา UI แบบไม่ต้องล็อกอินบน `localhost`/`127.0.0.1` ให้ตั้ง `APP_ENV=development` และ `AUTH_REQUIRED=false` ใน `.env` ในเครื่องเท่านั้น
