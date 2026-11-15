# STATUS.md — สถานะงานปัจจุบัน งานค้าง และข้อจำกัดของระบบ

> กำลังเตรียม PR: Rich Menu 8 ปุ่มตามภาพที่เจ้าของเลือก พร้อมหน้าตั้งค่าชื่อเรียก ห้องเรียน และสถานะเบิกจ่ายเริ่มต้น ยังไม่ merge/deploy หรือติดตั้งเมนูบน LINE OA จริง

อัปเดตล่าสุด: 2026-11-15

---

## 1. ภาพรวมสถานะปัจจุบัน (Current State)

โปรเจกต์ **"น้องโชคช่วย" (Nong ChokChuay) / OpenThai 2.0 OCR App** อยู่ในสถานะพร้อมใช้งานทั้งในเครื่อง (Local) และพร้อมสำหรับ Deploy บน Cloudflare Workers Paid + Cloudflare Containers (`https://nong-chokchuay-ocr.entrace-space.workers.dev`)

### ความสามารถที่เปิดใช้งานและมีชุดทดสอบรองรับแล้ว:
- **คลังเอกสาร OCR ภาษาไทย (Vault & FTS5):** อ่านข้อความภาษาไทย-อังกฤษจากภาพและ PDF ด้วยโมเดล OpenThai 2.0 ONNX บน CPU, เก็บไฟล์ต้นฉบับใน `data/uploads/` และค้นหาด้วย SQLite Trigram FTS5
- **ถาดรอตรวจ (Review Queue):** แยกเอกสารที่เพิ่งสแกน (`review_status = 'pending_review'`) ให้คุณครูกดตรวจยืนยันได้ในคลิกเดียว (`'reviewed'`)
- **สมุดเบิกจ่ายคุณครูอนุบาล (Expense & Reimbursement Tracker):** สกัดข้อมูลใบเสร็จ แยกสถานะรอเบิกคืนโรงเรียน (`pending_reimbursement`), เบิกคืนแล้ว (`reimbursed`), และรายจ่ายส่วนตัว (`personal`) พร้อมส่งออก CSV (UTF-8 BOM)
- **แฟ้มทะเบียนเด็กอนุบาลและแจ้งเตือนแพ้อาหาร (Student Profiles):** สกัดชื่อ-นามสกุล ชื่อเล่น เลขบัตรประชาชน 13 หลัก วันเกิด (พ.ศ. → ค.ศ.) ห้องเรียน เบอร์ผู้ปกครอง และป้ายเตือนแพ้อาหาร/โรคประจำตัว
- **ช่องทาง LINE OA, Mascot Messaging Cards 7 สถานการณ์ และตัวจำลองแชท (LINE Webhook & Web Simulator):** รับภาพและคำสั่งผ่าน `/api/line/webhook` และ `/api/line/simulate` ตอบกลับด้วย **Mascot Messaging Cards** ทั้ง 7 สถานการณ์ (`receipt`, `student`, `pending_carousel`, `expense_summary`, `allergy_watchlist`, `search_carousel`, `menu` รวมถึงสถานะกดยืนยัน `confirmed`) พร้อมภาพมาสคอตพื้นหลังใส (`static/mascot_cards/*.png`, `aspectMode: "fit"` เฉพาะเมื่อ `PUBLIC_BASE_URL` เป็น `https://`), แถบปุ่มลัด Quick Replies, ปุ่ม Postback เข้ารหัส `urlencode` (`confirm_review`, `mark_reimbursed`, `run_cmd`), ตัวตัดความยาวปุ่ม 20 ตัวอักษรและตรวจลิงก์ `tel:` ตามสเปก LINE API, ลิงก์เปิดเว็บภายนอก `openExternalBrowser=1`, การซิงก์ Lock `execute_read`/`execute_mutation` ร่วมกับ R2 และการคุยธรรมชาติกับ "ครูพิ้งกี้" ผ่าน `gemini-4-hydrogen`
- **ระบบยืนยันตัวตนและความปลอดภัย (Google OAuth + LINE Allowlist):** จำกัดสิทธิ์เข้าใช้งานเว็บเฉพาะอีเมล Google ที่ยืนยันแล้ว 2 บัญชี (`ALLOWED_EMAILS`), เก็บ session ใน SQLite (`auth_sessions`) พร้อม `HttpOnly` cookie, ตรวจ Origin + CSRF token (`X-CSRF-Token`), ยกเลิก session ทันทีเมื่อ Logout, ปฏิเสธการเข้าถึงเป็นค่าเริ่มต้นเมื่อเปิด `AUTH_REQUIRED=true` (fail-closed) รวมถึงจำกัดผู้ส่ง LINE OA ด้วย `ALLOWED_LINE_USER_IDS` และแชทส่วนตัว 1-on-1 (`LINE_ALLOW_GROUP_CHATS=false`)
- **ระบบจัดเก็บข้อมูลถาวรบน Cloudflare R2 (`storage_service.py` + `VaultR2Gateway`, ADR 0003):** เมื่อรันบน Cloud (`R2_REQUIRED=true` / `VAULT_BUCKET = nong-chokchuay-vault`) ระบบกู้ snapshot SQLite ที่ผ่านการตรวจ SHA-256 และ schema (`user_version = 3`) ก่อนเปิดรับคำขอ, บันทึกไฟล์ต้นฉบับและ versioned SQLite snapshot ผ่าน conditional ETag manifest commit (`If-Match` / `If-None-Match: *`) ก่อนตอบสำเร็จหรือตอบ LINE, ดึงไฟล์ต้นฉบับกลับแบบ lazy พร้อมตรวจ SHA-256 และรองรับการลบไฟล์แบบรอรอบ retention (`SNAPSHOT_RETENTION_COUNT = 5`)
- **ตัวสกัดข้อมูลแบบผสม (Hybrid Extractor):** ใช้ Offline Thai Regex เป็นฐานหลัก และรองรับ Text-only LLM เสริม (`gemini-4-hydrogen` เป็นค่าเริ่มต้นพร้อมตัวกรองโหมดคิดวิเคราะห์ `thinking` เมื่อตั้งค่า `LLM_API_KEY` หรือ `GEMINI_API_KEY`)
- **ระบบกติกาการทำงานของ AI Agent:** ตั้งค่า `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `INDEX.md`, `GLOSSARY.md`, `CODING_STANDARDS.md`, `docs/STATUS.md`, `docs/adr/0003-cloudflare-r2-sqlite-persistence.md` และ `docs/release-workflow.md` ครบชุด

---

### Rich Menu 8 ปุ่มและตั้งค่าคุณครู (เตรียมใน PR)

- เชื่อมส่งเอกสาร, สรุปยอดเบิก, รายการบิล, แฟ้มเด็ก, รอตรวจ, ค้นหา, ตั้งค่า และวิธีใช้ ใช้พิกัด/action กลางจาก `rich_menu.py` ร่วมกับตัวจำลองบนเว็บ ปุ่มส่งเอกสารตอบ Camera/Camera Roll Quick Replies และปุ่มค้นหาเปิดคีย์บอร์ดพร้อมคำค้น
- เพิ่มหน้า `/?view=settings` กับ API ที่อยู่หลัง auth/CSRF เดิม เก็บค่าแบบใช้ร่วมกันทั้ง Vault ใน SQLite `teacher_settings` (schema 4) และ commit R2 ก่อนตอบสำเร็จ ค่าเริ่มต้นใช้กับข้อมูลใหม่ทุกช่องทาง ไม่เขียนทับข้อมูลเก่า ห้องเรียนที่อ่านจากเอกสารได้ชัดยังมีสิทธิ์ก่อนค่าเริ่มต้น
- รายการบิล/แฟ้มเด็ก/ตั้งค่าเปิดเว็บภายนอกจาก LINE และรักษาหน้าปลายทางข้าม OAuth ด้วย sessionStorage ของแท็บ
- เพิ่มคำสั่ง `scripts/line_rich_menu.py build` แบบ offline และ `install`/`restore` สำหรับ operator หลังอนุมัติ โดยจด default เดิมก่อนเปลี่ยน เปิดใช้หลัง upload ภาพสำเร็จ และไม่แตะ per-user links
- ตรวจในเครื่องด้วยฐานข้อมูลชั่วคราว: ทางเข้าครบทั้ง 8 บน Chromium 390×844, บันทึกตั้งค่า/อ่านกลับ, Python/Node regression และ R2 จำลอง (รายละเอียดผลสุดท้ายใน `docs/qa/2026-11-15-rich-menu-settings.md`)
- ข้อจำกัด: ยังไม่ได้ตรวจ Google sign-in ใหม่, native camera/album บนมือถือจริง หรือ LINE OA/production; การ rollback โค้ดที่รองรับเพียง schema 3 ต้องวางแผนให้รองรับ snapshot 4 ก่อน ดู [ขั้นตอนเปิดใช้และย้อนกลับ](rich-menu.md)

### งานคำถามธรรมชาติ → Mascot Messaging Cards (release แล้ว 2026-11-15)

- เพิ่มกฎอ่านข้อความใน `line_service.py` ก่อนเรียก LLM สำหรับคำถามที่ขอดูยอดเบิก/รายชื่อเด็กแพ้อาหาร/เอกสารรอตรวจ เช่น “มียอดรอเบิกเท่าไหร่”, “ใครแพ้อาหารบ้าง” และ “มีเอกสารรอตรวจไหม” พร้อมคำลงท้ายสุภาพและการเว้นวรรค
- ใช้ตัวสร้างการ์ดเดิมและ `storage.execute_read` ร่วมกันทั้ง LINE Webhook และตัวจำลองแชท จึงใช้ข้อมูลจาก SQLite โดยไม่เรียกบริการ LLM สำหรับคำถามที่รองรับ
- กฎจับทั้งประโยค ไม่จับเพียงคำที่โผล่อยู่ในข้อความ คำสั่งค้นหาโดยตรงและคำขอให้ AI ช่วยเขียนยังใช้เส้นทางเดิม ส่วนประโยคที่มีหลายคำถามหรืออยู่นอกกฎยังอาจตอบเป็นข้อความจาก AI
- ผลตรวจในเครื่อง: Python syntax และชุดทดสอบ 59 ข้อผ่าน, JavaScript syntax และชุดทดสอบ Worker/Frontend 13 ข้อผ่าน รวม regression tests สำหรับเปิด/ปิด LLM, ข้อมูลยอดเงิน/ชื่อเด็กในฐานทดสอบ, ฐานข้อมูลว่าง, LINE Flex payload, read lock และการคงพฤติกรรมค้นหา/ช่วยเขียน
- ตรวจผ่าน HTTP กับเซิร์ฟเวอร์และฐานข้อมูลทดสอบที่แยกต่างหาก: คำถามตัวอย่างทั้ง 3 แบบได้ HTTP 200 และ LINE Flex Card ตามประเภท รีสตาร์ตเซิร์ฟเวอร์หลักในเครื่องพอร์ต 8765 เป็นโค้ดล่าสุดแล้ว (`/api/health` ตอบ `READY`) แต่ตัวในเครื่องยังไม่มี Google OAuth จึงตอบ 401 เมื่อเรียก Simulator โดยไม่ล็อกอิน ไม่ได้เปลี่ยนค่าล็อกอินเพื่อข้ามการตรวจนี้
- [PR #6](https://github.com/chokky-education/nong-chokchuay-ocr/pull/6) merge แล้วที่ `3c3b98be4952cb8ac0d1bc968af46337b5e16e1d` หลังเจ้าของอนุมัติ CI ของ PR และ merged commit ผ่านครบ GitHub Actions ข้าม deploy เพราะไม่มี `CLOUDFLARE_API_TOKEN` จึง deploy จาก merged checkout ด้วย Wrangler 4.114.0 ที่ล็อกอินอยู่
- Worker รุ่น `5c29a541-b0bd-4d95-aa6c-3a447c4ab42d` ใช้งาน 100% และ Container เปลี่ยนเป็น image digest `sha256:c50c13490ade6c2299fcd7367d8a81da6aad5fc6a7319a8c1e76fd8e686a1282` ตรวจ Simulator บน Cloudflare ด้วย session ที่ล็อกอินอยู่แล้ว คำถามทั้ง 3 ประเภทได้การ์ดมาสคอตพร้อมข้อมูลจากระบบ ข้อมูลเดิมยังอยู่ และ R2 revision คงเดิมที่ 3
- ระหว่างตรวจพบ browser cache ของหน้าจอรุ่นเก่า แก้ด้วย hard reload และพบ HTTP 503 หนึ่งครั้งช่วง Container เปลี่ยนรุ่น/เริ่มทำงาน ก่อนทดสอบซ้ำผ่านครบ ยังไม่ได้ส่งข้อความจากแอป LINE จริง ดูหลักฐานและข้อจำกัดใน [บันทึก release PR #6](releases/2026-11-15-pr6-natural-question-cards.md)

---

## 2. สิ่งที่ค้างอยู่และงานถัดไป (Pending Items)

1. **ตรวจ sign-in ใหม่และเตรียม Google OAuth ในเครื่อง:** ระบบจริงมี OAuth/LINE/allowlist/secrets แล้วตาม health และชื่อ bindings ที่ตรวจได้ แต่รอบ PR #6 ใช้ session เดิม ไม่ได้ทดสอบ Google sign-in ใหม่ ส่วน localhost ยังไม่มี Google OAuth
2. **การ deploy อัตโนมัติผ่าน GitHub Actions:** ยังไม่มี `CLOUDFLARE_API_TOKEN` ใน GitHub Actions จึงข้าม deploy แม้ job สำเร็จ หากจะเปิดใช้งานต้องมีแผนและได้รับอนุมัติการเพิ่ม secret ตาม release workflow; รอบ PR #6 deploy จากเครื่องแล้ว
3. **ตรวจ LINE OA จากแอปจริง:** health ระบุว่า LINE OA และ allowlist ตั้งค่าแล้ว แต่ยังไม่ได้ตรวจ URL ใน LINE Developers Console หรือบัญชีผู้ใช้ทุกบัญชีในรอบนี้ ต้องทดสอบข้อความ/รูปบิล/ใบสมัครและลิงก์เปิดเว็บจากแอป LINE จริงเพิ่มเติม
4. **ตรวจ R2 เพิ่มเติมตาม flow:** ระบบจริงใช้ R2 และกู้ข้อมูลเดิมหลังเปลี่ยน Container ได้แล้ว โดย `R2_BOOTSTRAP_IF_EMPTY=false` ไม่ต้อง bootstrap ซ้ำ การตรวจ upload/ยืนยันบิล/Watch Folder/restore จาก snapshot รุ่นก่อนหน้าบน production ยังอยู่นอกการตรวจ PR #6

---

## 3. ข้อจำกัดของระบบที่ต้องทราบ (Known Limitations)

- **เวลาเริ่มทำงานของ Cloudflare Container (Cold Start):** เนื่องจากตั้งค่า scale-to-zero เมื่อไม่มีการใช้งานครบ 3 นาที (`SLEEP_AFTER_MS = 180000` ใน `worker.js`) คำขอแรกหลังจากที่ Container หลับจะใช้เวลารอ Container ตื่นและกู้ snapshot จาก R2 ประมาณ 3–8 วินาที (ซึ่ง `worker.js` มีกลไกลองส่งคำขอซ้ำให้อัตโนมัติสูงสุด 25 ครั้ง และ `alarm()` จะเช็ก `_activeRequests` รวมถึงสถานะ `busy` / `RESTORING` จาก `/api/health` ก่อน `destroy()`)
- **ความทนทานของข้อมูลและงบประมาณ Cloudflare:** ในโหมด R2 (`R2_REQUIRED=true`) ข้อมูลเอกสาร, บิล, ทะเบียนเด็ก และ `auth_sessions` จะถูก commit ลง private R2 ก่อนตอบสำเร็จ จึงรอดจากการหลับ 3 นาทีและการ redeploy ได้ ส่วนค่าบริการ **Workers Paid (USD 5/เดือน)** เป็นค่าพื้นฐานพร้อมโควตาในตัว (รวม R2 free tier 10 GB-month, 1M Class A writes, 10M Class B reads) ไม่ใช่เพดานรับรองค่าใช้จ่ายสูงสุดหากใช้งานเกินโควตา
- **ข้อจำกัดของ LINE Reply Token:** LINE Messaging API กำหนดให้ใช้ `replyToken` ได้เพียงครั้งเดียว **ภายใน 1 นาที** และควรตอบกลับให้เร็วที่สุด ดังนั้นหาก cold start + OCR + R2 commit ใช้เวลาเกินกำหนด `replyToken` ของข้อความแรกอาจหมดอายุได้ (ระบบจะยังบันทึกสำเร็จหรือไม่ส่งผลหลอก แต่ข้อความตอบกลับผ่าน Reply API อาจไม่ถึงผู้ใช้)
- **โหมดพัฒนาในเครื่อง (Local Dev):** เมื่อไม่ได้ตั้ง `R2_GATEWAY_URL` (`R2_REQUIRED=false`) ระบบจะทำงานกับ SQLite และ `data/uploads/` ในเครื่องตามปกติโดยไม่ต้องใช้ R2 และหากต้องการพัฒนา UI แบบไม่ต้องล็อกอินบน `localhost`/`127.0.0.1` ให้ตั้ง `APP_ENV=development` และ `AUTH_REQUIRED=false` ใน `.env` ในเครื่องเท่านั้น
