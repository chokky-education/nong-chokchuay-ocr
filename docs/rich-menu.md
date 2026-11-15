# Rich Menu 8 ปุ่มและการตั้งค่าคุณครู

สถานะ: เตรียมใน PR เท่านั้น ยังไม่ merge/deploy หรือติดตั้งบน LINE OA จริง

## ปุ่มและพฤติกรรม

| ปุ่ม | LINE OA | ตัวจำลองบนเว็บ |
| --- | --- | --- |
| ส่งบิล / เอกสาร | Postback → คำแนะนำ + Quick Reply ถ่ายรูป/เลือกอัลบั้ม; PDF ส่งผ่านไฟล์แนบ | คำแนะนำเดียวกัน + เลือกรูปจากอุปกรณ์; ปุ่มแนบไฟล์ยังรับ PDF |
| สรุปยอดเบิก | Postback → การ์ดยอดรอเบิก/เบิกคืนแล้วจาก SQLite | การ์ดเดียวกัน |
| รายการบิล | เปิดเว็บภายนอก `/?view=expenses&openExternalBrowser=1` | เปิดสมุดเบิกจ่ายในหน้าเดิม พร้อมแก้สถานะ/ส่งออก CSV |
| แฟ้มเด็ก | เปิดเว็บภายนอก `/?view=students&openExternalBrowser=1` | เปิดทะเบียนเด็ก พร้อมข้อมูลผู้ปกครอง/ตัวกรองแพ้อาหาร/ส่งออก CSV |
| เอกสารรอตรวจ | Postback → การ์ดรอตรวจและปุ่มยืนยันเดิม | การ์ดเดียวกัน |
| ค้นหาเอกสาร | Postback → คำแนะนำ เปิดคีย์บอร์ดพร้อม `ค้นหา ` | เติม `ค้นหา ` และโฟกัสช่องพิมพ์ |
| ตั้งค่า | เปิดเว็บภายนอก `/?view=settings&openExternalBrowser=1` | เปิดหน้าตั้งค่า |
| วิธีใช้ | Postback → อธิบายการใช้งานทั้ง 8 ปุ่ม | คำอธิบายเดียวกัน |

เว็บยังใช้ Google session/allowlist เดิม และการเขียนต้องผ่าน Origin + CSRF เหมือนเดิม ลิงก์ `view` ที่รู้จักจะถูกเก็บใน `sessionStorage` ของแท็บเพื่อกลับมาหน้าที่เลือกหลัง Google OAuth เปลี่ยนกลับ `/` (ถ้าเบราว์เซอร์ปิด storage ให้เปิดลิงก์เดิมอีกครั้งหลังล็อกอิน) ไม่มีการเชื่อมบัญชี LINE เข้ากับ Google เพิ่มเติม

`rich_menu.py` เป็นนิยามกลางของ action/พิกัดทั้ง LINE และตัวจำลอง พื้นที่โลโก้ท้ายภาพไม่ใช่ปุ่ม รูปที่เจ้าของเลือกอยู่ที่ `static/rich_menu/menu.jpg` (1526 × 1031 px, JPEG ต่ำกว่า 1 MB) แปลงจากภาพร่างที่อนุมัติโดยรักษาองค์ประกอบเดิม

## การตั้งค่าที่ใช้ร่วมกันทั้งคลัง

- ชื่อที่บอตใช้เรียก: ข้อความบรรทัดเดียว 1–60 ตัวอักษร
- ห้องเรียนเริ่มต้น: ข้อความบรรทัดเดียว 1–40 ตัวอักษร ใช้เมื่อเพิ่มเด็กใหม่โดยไม่ระบุห้อง หรือ OCR ได้เพียงค่าเริ่มต้น “อนุบาล”; ห้องที่อ่านได้ชัด เช่น “อนุบาล 2/1” มีสิทธิ์ก่อน
- สถานะเบิกจ่ายเริ่มต้น: `pending_reimbursement` หรือ `personal` เท่านั้น ไม่ตั้ง `reimbursed` อัตโนมัติ

เป็นค่ากลางของ Vault ที่ทั้งบัญชี Google ที่อนุญาตและ LINE ที่อนุญาตใช้ร่วมกัน ไม่ใช่ค่ารายคน ก่อนบันทึกครั้งแรกยังใช้ `TEACHER_NAME` และกฎสกัดเดิม เมื่อบันทึกแล้วค่าทั้งสามใช้กับข้อมูลใหม่ทุกช่องทาง (`web_upload`, `watch_folder`, `line_oa`, `api`) โดยไม่แก้รายการเก่า ป้าย “ส่วนตัว” ยังใช้กฎเดิม (แยกบิลทั่วไปออกจากรายการห้องเรียน/อาหารเด็ก) การสกัดซ้ำรักษาสถานะ/งบที่เคยตรวจแก้ไว้ และค่าห้องเรียนเดิมเมื่อเอกสารไม่ได้บอกห้องใหม่

`GET /api/settings` อ่านค่า และ `PUT /api/settings` ตรวจ input ก่อนบันทึกตาราง `teacher_settings` (`created_at`, `updated_at`, `updated_by`) ผ่าน `storage.execute_mutation` ในโหมด Cloud ต้อง commit R2 สำเร็จก่อนตอบว่าบันทึกแล้ว ถ้า commit ล้มเหลวจะย้อนค่ากลับและฟอร์มแจ้งว่ายังไม่สำเร็จ

Schema เป็นรุ่น 4: เพิ่มตารางด้วย `CREATE TABLE IF NOT EXISTS` ไม่แก้ข้อมูลเดิม รองรับการกู้ snapshot รุ่น 3 แล้ว migrate เป็น 4 ในเครื่อง ก่อน snapshot รุ่น 4 จะถูก commit เมื่อมี mutation ถัดไป

## เตรียมตรวจและเปิดใช้งานหลังอนุมัติ

PR นี้ไม่มีการเรียก LINE API จริง ไม่เปลี่ยน default menu, per-user menu, secrets หรือ production data

1. ตรวจ PR/CI และขออนุมัติ merge/deploy ตาม `release-workflow.md`
2. Deploy merged revision และตรวจเว็บ/Google sign-in/ลิงก์เข้ารายการบิล แฟ้มเด็ก และตั้งค่า โดยทดสอบใน Safari/Chrome
3. สร้าง JSON แบบ offline จาก merged checkout เพื่อตรวจ URL และพิกัด:

   ```bash
   ./.venv/bin/python scripts/line_rich_menu.py build \
     --base-url https://nong-chokchuay-ocr.entrace-space.workers.dev
   ```

4. เมื่อเจ้าของอนุมัติการเปิดใช้ Rich Menu บน OA จริง ให้ผู้ปฏิบัติงานที่มี `LINE_CHANNEL_ACCESS_TOKEN` ใน environment รัน (ห้ามพิมพ์ token ลงคำสั่ง/PR/log และไม่ต้องเพิ่ม secret ใหม่):

   ```bash
   ./.venv/bin/python scripts/line_rich_menu.py install \
     --base-url https://nong-chokchuay-ocr.entrace-space.workers.dev \
     --state-file /path/outside-repo/rich-menu-rollback.json
   ```

   คำสั่งจะอ่าน default เดิม → จด rollback record (ห้ามทับไฟล์เดิม) → validate JSON → สร้าง menu → บันทึก ID ใหม่ → upload ภาพ → เปลี่ยน default เป็นขั้นสุดท้าย → อ่านกลับตรวจ ID ไม่ลบเมนูเดิมและไม่แก้ per-user links ถ้าอัปโหลดภาพล้มเหลวจะไม่เปลี่ยน default แต่ menu ใหม่อาจค้างแบบไม่ถูกใช้งาน ให้ตรวจจาก ID ใน rollback record ก่อนล้างภายหลัง

5. ปิด/เปิดห้องแชท LINE บนมือถือ ตรวจภาพและกดครบ 8 ปุ่ม รวมถ่ายรูป/อัลบั้ม, ส่งไฟล์, ค้นหา, การ์ดรอตรวจ, เว็บล็อกอิน, แก้บิล, แพ้อาหาร, CSV และบันทึกตั้งค่า ทดสอบเอกสารใหม่ว่าค่าเริ่มต้นมีผล และยืนยันข้อมูลยังอยู่หลัง Container หลับ/เริ่มใหม่ การตรวจ API อย่างเดียวไม่แทนการกดจากแอป LINE จริง

ข้อควรทราบ: per-user menu มีสิทธิ์ก่อน API default และ API default มีสิทธิ์ก่อนเมนูจาก OA Manager จึงต้องตรวจว่าบัญชีที่ใช้ทดสอบไม่มี per-user menu บังอยู่ `openKeyboard`/`fillInText` ต้องใช้ LINE รุ่นที่รองรับ ส่วนเบราว์เซอร์บนเดสก์ท็อปไม่ใช่หลักฐานว่า native camera action ทำงาน

## ย้อนกลับ

หลังอนุมัติการเปลี่ยน production ใช้ rollback record เดิม:

```bash
./.venv/bin/python scripts/line_rich_menu.py restore \
  --state-file /path/outside-repo/rich-menu-rollback.json
```

คำสั่งจะคืน API default เดิม หรือ clear API default ถ้าเดิมไม่มี โดยไม่ลบเมนู/ไม่แตะ per-user links และปฏิเสธการคืนค่าถ้ามี release อื่นเปลี่ยน default ไปแล้ว ตรวจ default และแอป LINE ซ้ำหลัง restore

**การย้อนโค้ดหลังบันทึก snapshot รุ่น 4:** โค้ดเดิมที่รองรับถึง schema 3 จะปฏิเสธ snapshot รุ่น 4 อย่า rollback image เก่าอย่างเดียว ให้ใช้ revert PR ที่คง migration/การอ่าน schema 4 ไว้ หรือเลือก snapshot รุ่น 3 ที่ตรวจแล้วภายใต้แผนกู้ข้อมูลที่เจ้าของอนุมัติ (การกู้ snapshot เก่าจะสูญการแก้ไขหลัง revision นั้น)

## อ้างอิง LINE ที่ตรวจเมื่อ 2026-11-15

- [Rich menu object / image requirements / default menu](https://developers.line.biz/en/reference/messaging-api/nojs/#rich-menu-object)
- [Postback actions และการเปิดคีย์บอร์ด](https://developers.line.biz/en/reference/messaging-api/nojs/#postback-action)
- [Camera / Camera roll ใช้ได้ใน Quick Reply เท่านั้น](https://developers.line.biz/en/docs/messaging-api/actions/)
