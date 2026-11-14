# Release & Verification Workflow (`docs/release-workflow.md`)

เอกสารนี้กำหนดขั้นตอนมาตรฐานสำหรับการเตรียมงาน การตรวจงาน และการส่งมอบงานขึ้นระบบตามหมวดที่ 5 และหมวดที่ 6 ของ [AGENTS.md](../AGENTS.md)

---

## 1. ลำดับการทำงานหลัก

ทุกการเปลี่ยนแปลงที่มีผลต่อรีโพซิทอรีหรือระบบจริง ใช้ลำดับ:

`PR → CI → merge → deploy → ตรวจระบบจริง`

> **ขอบเขตสิทธิ์ของ Agent (กำหนดโดยเจ้าของโปรเจกต์):**
> - **Agent ทำเองได้:** สร้างสาขา `codex/…`, แก้ไขโค้ดและเอกสาร, รันชุดทดสอบและตรวจ syntax ในเครื่อง, push สาขางาน และสร้าง/อัปเดต PR
> - **ต้องขออนุมัติจากเจ้าของโปรเจกต์ก่อนเสมอ:** การ **merge PR เข้า `main`**, การ **deploy ขึ้นระบบจริง**, การแก้ไขค่าลับ (secrets) หรือข้อมูลจริง และการเปลี่ยนสถาปัตยกรรมที่ต่างจาก `docs/adr/`

---

## 2. ขั้นตอนก่อนเริ่มแก้ไขไฟล์ (Pre-flight Check)

1. ตรวจสถานะ Git และสาขาปัจจุบัน:
   ```bash
   git status
   git branch --show-current
   ```
2. หากมีงานค้างของผู้ใช้ใน working tree ให้รักษาไว้ตามเดิม ห้ามใช้ `git reset --hard` หรือลบไฟล์โดยไม่ถาม
3. สร้างหรือสลับไปยังสาขางาน:
   ```bash
   git checkout -b codex/<short-task-name>
   ```

---

## 3. การตรวจงานในเครื่องก่อนส่ง PR (Local Verification)

ก่อน commit และ push ทุกครั้ง ต้องรันคำสั่งตรวจต่อไปนี้ให้ผ่านครบ:

1. **ตรวจไวยากรณ์โค้ด Python (Syntax Check):**
   ```bash
   ./.venv/bin/python -m py_compile app.py config.py database.py extractor.py line_service.py ocr_service.py watcher.py
   ```
2. **รันชุดทดสอบอัตโนมัติทั้งหมด (Unit & Integration Tests):**
   ```bash
   ./.venv/bin/python -m unittest discover -s tests -v
   ```
3. **ตรวจความเรียบร้อยของเอกสารและไฟล์ที่เกี่ยวข้อง:**
   - อัปเดต `docs/STATUS.md` ให้ตรงกับสถานะจริง
   - อัปเดต `INDEX.md` หากมีการเพิ่ม ย้าย หรือลบไฟล์/โมดูล
   - อัปเดต `GLOSSARY.md` (และ `CONTEXT.md`) หากมีคำศัพท์โดเมนใหม่
4. **ตรวจทาน Diff ตาม `CODING_STANDARDS.md`:**
   ```bash
   git status
   git diff
   ```
   ตรวจสอบว่าไม่มีไฟล์ `.env`, `*.db`, หรือค่าลับใด ๆ หลุดเข้า commit

---

## 4. การสร้าง PR และรอผล CI

1. Push เฉพาะสาขางานขึ้น GitHub:
   ```bash
   git push -u origin codex/<short-task-name>
   ```
2. สร้างหรืออัปเดต Pull Request เข้าสู่ `main`:
   ```bash
   gh pr create --base main --head codex/<short-task-name> --title "<หัวข้องาน>" --body "<สรุปการเปลี่ยนแปลงและผลตรวจ>"
   ```
3. ตรวจสอบสถานะ GitHub Actions CI บน revision ล่าสุด:
   ```bash
   gh pr checks
   ```
4. หาก `main` มีการเปลี่ยนแปลงระหว่างทำงาน ให้นำการเปลี่ยนแปลงล่าสุดมาอัปเดตเข้าสาขางานและรันชุดทดสอบซ้ำก่อนขออนุมัติ merge

---

## 5. การ Merge และการ Deploy (หลังได้รับอนุมัติจากเจ้าของโปรเจกต์)

1. **เมื่อเจ้าของโปรเจกต์อนุมัติให้ merge:**
   - ทำการ merge PR เข้า `main` และรอให้ workflow `.github/workflows/deploy-cloudflare.yml` บน `main` รันผ่าน
2. **เมื่อเจ้าของโปรเจกต์อนุมัติให้ deploy (กรณีสั่ง deploy จากเครื่อง):**
   - สลับไปยัง `main` ที่ดึง commit ล่าสุดแล้ว (`git checkout main && git pull --ff-only`)
   - รันคำสั่ง deploy ผ่าน Wrangler:
     ```bash
     npx wrangler deploy
     ```
3. **การจัดการค่าลับ (Wrangler Secrets):**
   - ต้องมีแผนใน PR ที่ระบุชื่อตัวแปร เป้าหมาย ผลที่คาด วิธีตรวจ และวิธีย้อนกลับ โดยไม่เปิดเผยค่าลับจริง
   - ตั้งค่าผ่าน CLI เฉพาะเมื่อได้รับอนุมัติ:
     ```bash
     npx wrangler secret put LINE_CHANNEL_SECRET
     npx wrangler secret put LINE_CHANNEL_ACCESS_TOKEN
     npx wrangler secret put LLM_API_KEY
     ```

---

## 6. การตรวจระบบจริงหลัง Deploy (Post-Deploy Verification)

ห้ามใช้เพียง `/api/health` แล้วสรุปว่างานเสร็จ ต้องตรวจทั้งสถานะพื้นฐานและพฤติกรรมของฟีเจอร์ที่แก้ไข:

1. **ตรวจสถานะพื้นฐานและการตั้งค่าระบบ:**
   - `GET /api/health` (ตรวจสถานะเซิร์ฟเวอร์ โมเดล OCR และสถานะตัวแปรตั้งต้น)
   - `GET /api/stats` (ตรวจการเชื่อมต่อฐานข้อมูล SQLite และยอดสรุป)
2. **ตรวจ Flow ของฟีเจอร์ที่ได้รับผลกระทบ:**
   - ช่องทางเว็บ: ทดสอบอัปโหลดภาพหรือกดเอกสารตัวอย่าง (`/api/ocr/sample/...`), ตรวจถาดรอตรวจ (`/api/documents/review-queue`), สมุดเบิกจ่าย (`/api/expenses`) และแฟ้มเด็กอนุบาล (`/api/students`)
   - ช่องทาง LINE OA: ทดสอบผ่าน `/api/line/simulate` หรือส่งข้อความ/รูปภาพผ่าน LINE Webhook จริงหากการแก้ไขเกี่ยวข้องกับ `line_service.py`
3. **การย้อนกลับเมื่อพบปัญหา (Rollback):**
   - สำหรับ Cloudflare Worker / Container: ใช้ `npx wrangler rollback` เพื่อย้อนกลับไปยัง deployment ก่อนหน้าทันที
   - สำหรับโค้ดใน Git: สร้าง revert PR จาก `main` และแจ้งผลพร้อมสาเหตุให้เจ้าของโปรเจกต์ทราบ
