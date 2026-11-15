# Release PR #6 — คำถามธรรมชาติ → Mascot Messaging Cards

วันที่: 15 พฤศจิกายน 2026 (Asia/Bangkok)

## ขอบเขตและ revision

- เจ้าของอนุมัติให้ merge PR #6, deploy บน Cloudflare และตรวจระบบจริง
- PR: https://github.com/chokky-education/nong-chokchuay-ocr/pull/6
- Merged SHA: `3c3b98be4952cb8ac0d1bc968af46337b5e16e1d`
- ระบบจริง: https://nong-chokchuay-ocr.entrace-space.workers.dev/
- คำถามที่รองรับเกี่ยวกับยอดเบิก/เด็กแพ้อาหาร/เอกสารรอตรวจ เรียกการ์ดเดิมจาก SQLite ก่อนเรียก LLM คำสั่งค้นหา คำขอให้ AI ช่วยเขียน และประโยคนอกกฎยังใช้เส้นทางเดิม

## CI และ deploy

| ขั้นตอน | หลักฐาน/ผล |
| --- | --- |
| CI ของ PR | [Run 43686786397](https://github.com/chokky-education/nong-chokchuay-ocr/actions/runs/43686786397) ผ่าน |
| CI ของ merged commit | [Run 43686981201](https://github.com/chokky-education/nong-chokchuay-ocr/actions/runs/43686981201) ผ่าน |
| การตรวจโค้ด | Python syntax + 59 tests; JavaScript syntax + 13 Worker/Frontend tests ผ่าน |
| Deploy job ใน Actions | ข้ามการ deploy จริง เพราะไม่มี `CLOUDFLARE_API_TOKEN` (job สีเขียวไม่ได้แปลว่า deploy แล้ว) |
| Deploy จากเครื่อง | Wrangler 4.114.0, Docker พร้อมใช้งาน, dry run ผ่าน จาก checkout ของ merged SHA; `wrangler deploy` จบด้วย exit 0 |
| Worker Version | `5c29a541-b0bd-4d95-aa6c-3a447c4ab42d` ถูกใช้งาน 100% |
| Worker Deployment | `e959783a-a60e-4dca-a410-23af6caf7e67`, สร้าง 13:48:26 น.; version message ระบุ merged SHA ครบ |
| Container Application | `a037c057-4910-485a-b6b8-d1532ae621db`, version 7 |
| Container image | `sha256:c50c13490ade6c2299fcd7367d8a81da6aad5fc6a7319a8c1e76fd8e686a1282` ตรวจตรงกันระหว่าง deploy output และ Cloudflare API ของ application/instance |

ไม่มีการเพิ่ม/เปลี่ยน secrets, เปลี่ยน auth config, bootstrap R2 ใหม่ หรือแก้ข้อมูลเอกสาร/นักเรียนจริงในการตรวจครั้งนี้

## ตรวจพฤติกรรมบนระบบจริง

ใช้แท็บ Chrome ของระบบจริงที่ล็อกอินอยู่แล้ว เปิด LINE Simulator และพิมพ์คำถามผ่าน UI จริง:

| คำถาม | ผลที่เห็นบนหน้าจอ |
| --- | --- |
| มียอดรอเบิกเท่าไหร่ | การ์ดมาสคอต “ยอดรอเบิก / สรุปงบ” แสดงยอดรอเบิก 0 บาท/0 ใบ และเบิกคืนแล้ว 0 บาท/0 ใบ ตรงกับข้อมูลตั้งต้น |
| ใครแพ้อาหารบ้าง | การ์ดมาสคอต “แพ้อาหารหน้าห้องเรียน” แสดงรายการนักเรียน 1 คนพร้อมข้อความแจ้งเตือนจากข้อมูลเดิม |
| มีเอกสารรอตรวจไหม | การ์ดมาสคอต “รอตรวจ (ค้างหลายใบ)” พร้อมข้อความว่าตรวจเอกสารครบทุกใบแล้ว ตรงกับข้อมูลตั้งต้นที่มีเอกสาร 0 ฉบับ |

- หลังเปลี่ยน Container ยังใช้ session เดิมได้ และข้อมูลนักเรียนเดิม 1 คนยังอยู่
- `/api/health` หลัง deploy: `status=ok`, `storage_state=READY`, `storage_revision=3`, `busy=false`, `auth_required=true`; OAuth/LINE/allowlist แสดงว่าตั้งค่าแล้ว
- R2 revision เท่ากับ 3 ทั้งก่อนและหลัง deploy/การอ่านข้อมูลครั้งนี้
- `static/app.js`, `static/styles.css` และมาสคอต PNG ทั้ง 9 ไฟล์ตอบ HTTP 200 และ byte ตรงกับ merged checkout
- Simulator เมื่อไม่มี session ตอบ HTTP 401 ตามที่คาด

## เหตุการณ์ระหว่างตรวจและข้อจำกัด

- แท็บเดิมเก็บ JavaScript/CSS รุ่นเก่าไว้ ทำให้ครั้งแรกเห็นการ์ดแบบเดิม หลัง hard reload ที่ Chrome จึงเห็น UI การ์ดมาสคอตและ Quick Replies รุ่นล่าสุด
- พบ HTTP 503 หนึ่งครั้งระหว่าง Container เปลี่ยนรุ่น/ตื่นขึ้นมา จากนั้น health กลับเป็น READY และลองคำถามใหม่ผ่านครบทั้ง 3 แบบ ไม่ได้แก้กลไก cold start ใน release นี้
- การอ่าน CSS ผ่านสคริปต์ตรวจไฟล์ครั้งแรก timeout หลัง 30 วินาที เมื่อลองใหม่ด้วย connection แยก อ่าน CSS และ PNG ครบทุกไฟล์สำเร็จ ไม่มีการแก้ไฟล์หรือ config เพื่อให้ผ่านการตรวจ
- ตรวจพฤติกรรมผ่าน Simulator บน production ไม่ได้ส่งข้อความจากแอป LINE จริง จึงยังไม่ยืนยันการแสดงผลในแอป LINE/การส่งผ่าน Reply API จริง
- ไม่เพิ่มเอกสารหรือแก้ข้อมูลจริง จึงไม่ได้ทำ OCR upload/Watch Folder/การกดยืนยันบิลบน production ในรอบนี้ ชุดทดสอบของช่องทางเหล่านั้นผ่านใน CI
- ใช้ session เดิมในการตรวจ ไม่ได้ทดสอบ Google OAuth sign-in ใหม่ และในเครื่องยังขาด Google OAuth ตามข้อจำกัดเดิม

## ย้อนกลับ

หากพบปัญหาจาก release นี้ ให้ใช้ขั้นตอน rollback ใน `docs/release-workflow.md` โดยตรวจทั้ง Worker และ Container image เพราะการ deploy ของสองส่วนไม่ได้เป็นธุรกรรมเดียวกัน รุ่น Worker ก่อนหน้า: `734d07ad-7dc7-444a-8157-5e060256f354`; Container image ก่อนหน้า: `sha256:c38a6782021ba767fa9a7af3a5c8fdce119e9faf6a2cdd622aa8c808784b211e`

การย้อนโค้ดไม่ได้ย้อนข้อมูลใน R2 และไม่มีการเปลี่ยน schema ใน PR #6
