# ทำงานกับ AI Agent ในโปรเจกต์น้องโชคช่วย

## เริ่มงาน

อ่าน [AGENTS.md](../AGENTS.md) → [สถานะล่าสุด](STATUS.md) → [แผนที่โค้ด](../INDEX.md) แล้วเช็ก branch และไฟล์ที่มีการแก้ค้าง สรุปให้ได้หนึ่งประโยคว่าแก้ปัญหาอะไรให้ใคร และจะตรวจจากผลอะไร

| งาน | เตรียมและตรวจ |
| --- | --- |
| ฟีเจอร์หรือการเปลี่ยนหลายช่องทาง | ระบุผู้ใช้ เกณฑ์ผ่าน และช่องทางที่ได้รับผล ถ้าแตะการนำเข้าเอกสาร ให้ตรวจจากช่องทางที่เปลี่ยนและใช้ตรรกะกลางตาม AGENTS.md |
| หน้าจอหรือ flow ใหม่ | บันทึก flow และสถานะปกติ/ว่าง/โหลด/ผิดพลาด ทำ HTML ที่กดลองได้เมื่อยังต้องตัดสินใจเรื่องประสบการณ์ใช้งาน แล้วตรวจด้วยเบราว์เซอร์จริงในจอเล็ก กลาง และใหญ่ เช่น 375×812, 768×1024 และ 1440×900 เก็บภาพหน้าที่เปลี่ยนและเช็ก Console/Network ของ flow ที่ทดสอบ |
| บั๊ก | เก็บขั้นตอนและผลที่เห็นก่อนแก้ ใช้ชุดทดสอบหรือเอกสารตัวอย่างที่ทำให้เกิดอาการซ้ำ ตรวจกรณีนั้นอีกครั้งหลังแก้ |
| เอกสารหรือการตั้งค่า | ตรวจลิงก์ คำสั่ง และเงื่อนไขกับไฟล์จริง ระบุผลตรวจ CI และจุดที่ต้องให้เจ้าของอนุมัติหากเกี่ยวกับ release |

## รันและตรวจในเครื่อง

ใช้ Python 3.11 และ Node.js 22 ให้ตรงกับ CI จากโฟลเดอร์ repo ถ้ายังไม่มี `.venv` ให้สร้างและติดตั้งแพ็กเกจ

```bash
python3.11 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

ถ้า `.venv` เดิมสร้างด้วย `uv` ให้ใช้ `uv pip install --python .venv/bin/python -r requirements.txt` เมื่อต้องติดตั้งแพ็กเกจเพิ่ม

รันเว็บและ API ด้วย `./.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8765` หรือใช้ `./start.sh` บน macOS เพื่อเปิดเบราว์เซอร์ด้วย เมื่อจะตั้งค่าการเชื่อมต่อในเครื่อง ให้ใช้ [`.env.example`](../.env.example) ดูชื่อตัวแปร แล้วตั้งใน `.env` เฉพาะเครื่อง เก็บค่าที่มีอยู่หากตั้งไว้แล้ว

ถ้าต้องการกดลองงานด้วยเอกสารตัวอย่างโดยยังไม่มี Google OAuth ให้เปิด instance ทดสอบที่ `http://127.0.0.1:8770` คำสั่งนี้ใช้ฐานข้อมูลชั่วคราวกับค่าทดสอบแยกจาก `.env` ของแอปหลัก กด Ctrl+C เพื่อปิดและล้างข้อมูลทดสอบ

```bash
./.venv/bin/python - <<'PY'
from tempfile import TemporaryDirectory

import uvicorn

from app import create_app
from config import AppConfig

with TemporaryDirectory(prefix="nong-chokchuay-dev-") as data_dir:
    config = AppConfig.from_dir(data_dir, inbox_poll_seconds=0, auto_extract_on_ingest=True)
    uvicorn.run(create_app(config=config), host="127.0.0.1", port=8770)
PY
```

ใช้ชุดคำสั่งก่อนเปิด PR ใน [การตรวจงานในเครื่อง](release-workflow.md#3-การตรวจงานในเครื่องก่อนส่ง-pr-local-verification) ชุด Python มี [การทดสอบ OCR โมเดลจริง](../tests/test_real_ocr_smoke.py) กับ [เอกสารตัวอย่าง](../samples/sample_budget_table.png) ในพื้นที่ชั่วคราว

## ส่งต่องาน

อัปเดต [STATUS.md](STATUS.md) ให้เห็นผลล่าสุดและสิ่งที่ค้าง เก็บหลักฐานการตรวจที่ต้องใช้ต่อใน `docs/qa/` หรือ `docs/releases/` แล้วส่ง PR ตาม [ขั้นตอน release](release-workflow.md) เจ้าของอนุมัติ merge และ deploy แยกกัน

เมื่องานอาศัยบทความหรือเอกสารภายนอก ให้ค้นจาก `~/second-brain/wiki/nong-chokchuay-ocr/index.md` หากเข้าถึงได้ และบันทึกบทเรียนที่นำกลับมาใช้ได้ตามกติกาใน Wiki ไฟล์ต้นทางจากภายนอกอยู่ใน Wiki ส่วนรายละเอียดโค้ดและสถานะล่าสุดอ่านจาก repo
