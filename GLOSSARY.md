# GLOSSARY.md — พจนานุกรมคำศัพท์โดเมน (Domain Glossary)

เอกสารนี้รวบรวมคำศัพท์หลักของโปรเจกต์ **"น้องโชคช่วย" (Nong ChokChuay)** และคลังเอกสาร **OpenThai 2.0 Vault** เพื่อให้โค้ด เอกสาร และ AI Agent ใช้ความหมายเดียวกันทั้งหมด

---

## 1. คลังเอกสารและการประมวลผล OCR

### **Vault Document** (เอกสารในคลัง)
- **ความหมาย:** ข้อมูลเอกสาร (รูปภาพหรือ PDF) ที่จัดเก็บในตาราง `documents` ของฐานข้อมูล SQLite พร้อมข้อความที่อ่านได้จาก OCR (`full_text`), พิกัดบรรทัดข้อความ (`lines_json`), สถานะการตรวจทาน (`review_status`), ช่องทางนำเข้า (`ingestion_channel`), และป้ายกำกับ (`tags_json`)
- **คำที่ต้องหลีกเลี่ยง:** Record, Entry, Item

### **Source File** (ไฟล์ต้นฉบับ)
- **ความหมาย:** ไฟล์รูปภาพหรือ PDF ต้นฉบับที่ไม่ถูกดัดแปลง บันทึกไว้ในดิสก์ที่ `data/uploads/` (ในโหมด Local) หรือทั้งในแคชชั่วคราวและใน private Cloudflare R2 bucket ที่ `vault/uploads/<source_storage_key>` (ในโหมด Cloud ตาม ADR 0003) และผูกกับ `Vault Document` ผ่านคอลัมน์ `source_storage_key` และ `source_sha256` โดยดึงกลับมาแคชในเครื่องแบบ lazy เมื่อมีการดาวน์โหลด
- **คำที่ต้องหลีกเลี่ยง:** Attachment, Raw Blob, Upload

### **Vault Manifest & Snapshot Revision** (สารบัญ R2 และรุ่นสแนปช็อต)
- **ความหมาย:** ไฟล์สารบัญกลาง `vault/manifest.json` ใน private Cloudflare R2 bucket ที่ชี้ไปยัง SQLite snapshot รุ่นล่าสุด (`rev-XXXXXXXX-*.db`) พร้อม `revision`, `schema_version`, `snapshot_sha256` และรายการ `retained_snapshots` (ใช้ `If-Match` / `If-None-Match: *` เป็นจุด commit ถาวรเพียงจุดเดียว)
- **คำที่ต้องหลีกเลี่ยง:** Checkpoint File, Temp Backup

### **OCR Line** (บรรทัดข้อความ OCR)
- **ความหมาย:** กล่องข้อความ 1 บรรทัดที่ได้จากโมเดล `LineOCR` (`model/runtime/line_engine.py`) ประกอบด้วยข้อความ (`text`), ค่าความเชื่อมั่น (`confidence`), ลำดับแถว (`row`), เลขหน้า (`page`), และพิกัดสี่เหลี่ยม 4 จุด (`box`)
- **คำที่ต้องหลีกเลี่ยง:** Segment, Token, Snippet

### **Ingestion Channel** (ช่องทางนำเข้าเอกสาร)
- **ความหมาย:** ช่องทางที่ `Source File` เข้าสู่ระบบ มีค่ามาตรฐาน 4 ค่า ได้แก่:
  - `web_upload`: อัปโหลดผ่านหน้าเว็บหรือ LIFF
  - `watch_folder`: ดูดไฟล์อัตโนมัติจากโฟลเดอร์ `data/inbox/`
  - `line_oa`: ส่งผ่านแชท LINE Official Account
  - `api`: เรียกผ่าน REST API โดยตรง
- **คำที่ต้องหลีกเลี่ยง:** Source Type, Input Mode

### **Watch Folder** (โฟลเดอร์รับไฟล์อัตโนมัติ)
- **ความหมาย:** โฟลเดอร์ `data/inbox/` ในเครื่องที่ `watcher.py` คอยตรวจจับไฟล์ใหม่เพื่อนำเข้าคลังอัตโนมัติ (รองรับโฟลเดอร์ย่อย `data/inbox/teacher/` สำหรับติดป้าย `"ครูอนุบาล"` และ `data/inbox/me/` สำหรับติดป้าย `"ส่วนตัว"` ส่วนไฟล์ที่อ่านไม่ได้จะถูกย้ายไป `data/inbox_failed/` พร้อมไฟล์ `.error.txt`)
- **คำที่ต้องหลีกเลี่ยง:** Drop Folder, Hot Folder

### **Review Queue** (ถาดรอตรวจทาน)
- **ความหมาย:** รายการ `Vault Document` ที่มีสถานะ `review_status = 'pending_review'` ซึ่งรอให้คุณครูกดตรวจยืนยัน (1-Click Review บนเว็บหรือผ่านปุ่มใน LINE Flex Card) ก่อนเปลี่ยนสถานะเป็น `'reviewed'`
- **คำที่ต้องหลีกเลี่ยง:** Moderation List, Drafts

---

## 2. การเบิกจ่ายและทะเบียนเด็กอนุบาล

### **Expense Record** (รายการบิลค่าใช้จ่าย)
- **ความหมาย:** ข้อมูลทางการเงินที่สกัดหรือบันทึกไว้ในตาราง `expenses` (`merchant`, `expense_date`, `receipt_number`, `subtotal`, `vat_amount`, `total_amount`, `currency`, `expense_category`, `reimbursement_status`, `budget_source`) โดยผูกแบบหนึ่งต่อหนึ่ง (1:1) กับ `Vault Document`
- **คำที่ต้องหลีกเลี่ยง:** Transaction, Ledger Row, Bill

### **Reimbursement Status** (สถานะการเบิกเงินคืน)
- **ความหมาย:** สถานะของ `Expense Record` ที่ระบุว่าคุณครูสำรองจ่ายไปก่อนหรือเบิกคืนแล้ว มี 3 ค่ามาตรฐาน:
  - `pending_reimbursement`: สำรองจ่าย รอเบิกคืนจากโรงเรียน
  - `reimbursed`: เบิกเงินคืนเรียบร้อยแล้ว
  - `personal`: รายจ่ายส่วนตัว (ไม่เบิกโรงเรียน)
- **คำที่ต้องหลีกเลี่ยง:** Claim Flag, Paid Bool

### **Student Profile** (แฟ้มทะเบียนเด็กอนุบาล)
- **ความหมาย:** ข้อมูลนักเรียนอนุบาลที่จัดเก็บในตาราง `students` (`full_name`, `nickname`, `citizen_id`, `birth_date`, `classroom`, `parent_name`, `parent_phone`, `allergies`, `medical_notes`) ซึ่งผูกกับ `Vault Document` แบบไม่บังคับ (`document_id`, `ON DELETE SET NULL`)
- **คำที่ต้องหลีกเลี่ยง:** Kid Row, Child Entry

### **Extraction Method** (วิธีสกัดข้อมูล)
- **ความหมาย:** กลไกที่ใช้สร้างหรืออัปเดต `Expense Record` หรือ `Student Profile` เพื่อแยกที่มาและความน่าเชื่อถือของข้อมูล มี 3 ค่ามาตรฐาน:
  - `regex`: สกัดด้วยกฎ Offline Thai Regex ในเครื่อง (`extractor.py`)
  - `llm`: สกัดด้วย Text-only LLM adapter (ส่งเฉพาะข้อความ OCR ไม่ส่งรูปภาพ)
  - `manual`: ผู้ใช้กรอกหรือแก้ไขด้วยตนเอง
- **คำที่ต้องหลีกเลี่ยง:** Parser Type, Mode

---

## 3. ช่องทางแชทและการเชื่อมต่อ

### **Teacher Settings** (การตั้งค่าคุณครู)
- **ความหมาย:** ค่าที่ใช้ร่วมกันทั้ง Vault ใน SQLite `teacher_settings` ได้แก่ชื่อที่บอตใช้เรียก (`teacher_name`), ห้องเรียนเริ่มต้น (`classroom`) และสถานะเบิกจ่ายเริ่มต้น (`default_reimbursement_status`: `pending_reimbursement` / `personal`) ใช้กับข้อมูลใหม่ผ่านตรรกะนำเข้ากลาง และ commit R2 ก่อนตอบว่าบันทึกสำเร็จ ไม่ใช่ข้อมูลรายบัญชี และไม่เปลี่ยนข้อมูลเก่าย้อนหลัง

### **LINE Rich Menu** (เมนูหลักใต้แชท LINE)
- **ความหมาย:** ภาพพร้อมพื้นที่กด 8 จุดของน้องโชคช่วย แต่ละจุดส่ง Postback ไปยังตรรกะ LINE เดิม หรือเปิดหน้าเว็บที่มี Google auth นิยามพิกัดและ action ใน `rich_menu.py` ร่วมกับตัวจำลอง ต่างจาก Flex Card และ Quick Reply ที่อยู่ในข้อความตอบกลับ ดู [พฤติกรรมและการติดตั้ง](docs/rich-menu.md)

### **LINE OA Channel** (ช่องทาง LINE Official Account)
- **ความหมาย:** ส่วนรับเหตุการณ์ Webhook (`/api/line/webhook`) และตัวจำลองแชท (`/api/line/simulate`) ใน `line_service.py` ที่ตรวจสอบลายเซ็น `X-Line-Signature`, ตอบกลับฟรีด้วย `replyToken` ในรูปแบบ Flex Card, รองรับปุ่ม Postback สำหรับยืนยันการตรวจหรือเปลี่ยนสถานะเบิกเงิน และรับคำสั่งค้นหาในแชท (`"ยอดรอเบิก"`, `"แพ้อาหาร"`, หรือคำค้นเอกสาร)
- **คำที่ต้องหลีกเลี่ยง:** Bot Pipe, Chat Feed
