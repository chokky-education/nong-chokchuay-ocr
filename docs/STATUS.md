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
- **ช่องทาง LINE OA และตัวจำลองแชท (LINE Webhook & Web Simulator):** รับภาพและคำสั่งค้นหาผ่าน `/api/line/webhook` (ตอบกลับฟรีด้วย `replyToken` เป็น Flex Card พร้อมปุ่ม Postback) และจำลองการคุยกับ "ครูพิ้งกี้" บนหน้าเว็บผ่าน `/api/line/simulate`
- **ตัวสกัดข้อมูลแบบผสม (Hybrid Extractor):** ใช้ Offline Thai Regex เป็นฐานหลัก และรองรับ Text-only LLM เสริม (`gemini-2.5-flash` เป็นค่าเริ่มต้นเมื่อตั้งค่า `LLM_API_KEY` หรือ `GEMINI_API_KEY`)
- **ระบบกติกาการทำงานของ AI Agent:** ตั้งค่า `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `INDEX.md`, `GLOSSARY.md`, `CODING_STANDARDS.md`, `docs/STATUS.md` และ `docs/release-workflow.md` ครบชุด

---

## 2. สิ่งที่ค้างอยู่และงานถัดไป (Pending Items)

1. **การตรวจยืนยันค่าลับบนระบบจริง (Cloudflare Secrets):**
   - ต้องตรวจสอบหรือตั้งค่า `LINE_CHANNEL_SECRET`, `LINE_CHANNEL_ACCESS_TOKEN`, และ `LLM_API_KEY` บน Cloudflare Worker (`npx wrangler secret put ...`) เมื่อเชื่อมกับบัญชี LINE Official Account จริงของโรงเรียน
2. **การเชื่อมต่อ LINE Webhook จริง:**
   - ผูก Webhook URL ของ LINE Developers Console เข้ากับ `<PUBLIC_BASE_URL>/api/line/webhook` และทดสอบส่งรูปบิล/ใบสมัครจากแอป LINE จริง

---

## 3. ข้อจำกัดของระบบที่ต้องทราบ (Known Limitations)

- **เวลาเริ่มทำงานของ Cloudflare Container (Cold Start):** เนื่องจากตั้งค่า scale-to-zero เมื่อไม่มีการใช้งานครบ 3 นาที (`SLEEP_AFTER_MS = 180000` ใน `worker.js`) คำขอแรกหลังจากที่ Container หลับจะใช้เวลารอ Container ตื่นประมาณ 3–8 วินาที (ซึ่ง `worker.js` มีกลไกลองส่งคำขอซ้ำให้อัตโนมัติสูงสุด 25 ครั้ง)
- **อายุข้อมูลใน Container ชั่วคราวบน Cloudflare:** การเก็บ SQLite (`data/documents.db`) และไฟล์อัปโหลดไว้ในดิสก์ภายใน Container จะอยู่ตราบเท่าที่อินสแตนซ์ยังไม่ถูกรีเซ็ตใหม่ทั้งหมด หากต้องการเก็บข้อมูลถาวรระยะยาวบนคลาวด์โดยไม่รันบนเครื่องตัวเอง อาจต้องพิจารณาเชื่อมระบบสำรองข้อมูลออกไปยัง Object Storage หรือฐานข้อมูลถาวรในอนาคต (ต้องขออนุมัติและบันทึก ADR ใหม่ก่อนเปลี่ยนสถาปัตยกรรม)
- **ข้อจำกัดของ LINE Reply Token:** การตอบกลับฟรีผ่าน `replyToken` ใช้ได้เฉพาะการตอบกลับทันทีเมื่อมีเหตุการณ์เข้ามาจากผู้ใช้ หาก Container ใช้เวลาตื่นนานเกินอายุของ `replyToken` การตอบกลับอัตโนมัติในข้อความแรกอาจไม่ทันเวลา
