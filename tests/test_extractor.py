import csv
import io
import unittest

from fastapi.testclient import TestClient

from app import create_app
from config import AppConfig
from tests.fakes import FakeLineOCR, make_png_bytes, make_temp_data_dir


class ExtractorAndExpenseDashboardTest(unittest.TestCase):
    def test_offline_regex_parses_thai_receipt_be_date_vat_and_totals(self) -> None:
        from extractor import extract_expense_regex

        receipt_text = (
            "ใบเสร็จรับเงิน/ใบกำกับภาษีอย่างย่อ\n"
            "7-Eleven สาขาลาดพร้าว\n"
            "เลขประจำตัวผู้เสียภาษี 0107542000011\n"
            "วันที่ 12/11/2569 14:30\n"
            "กาแฟเย็น 100.00\n"
            "VAT 7% 7.00\n"
            "ยอดสุทธิ 107.00 บาท"
        )
        res = extract_expense_regex(receipt_text)
        self.assertEqual(res["expense_date"], "2026-11-12")
        self.assertAlmostEqual(res["total_amount"], 107.0, places=2)
        self.assertAlmostEqual(res["vat_amount"], 7.0, places=2)
        self.assertAlmostEqual(res["subtotal"], 100.0, places=2)
        self.assertIn("7-Eleven", res["merchant"])
        self.assertEqual(res["extraction_method"], "regex")

        slip_text = (
            "โอนเงินสำเร็จ\n"
            "12 พ.ย. 69 09:15\n"
            "ไปยัง ร้านข้าวมันไก่เจ๊นก\n"
            "จำนวนเงิน 85.50 บาท\n"
            "ค่าธรรมเนียม 0.00 บาท"
        )
        slip_res = extract_expense_regex(slip_text)
        self.assertEqual(slip_res["expense_date"], "2026-11-12")
        self.assertAlmostEqual(slip_res["total_amount"], 85.5, places=2)
        self.assertIn("ร้านข้าวมันไก่เจ๊นก", slip_res["merchant"])

        before_vat_text = (
            "ใบเสร็จรับเงิน\n"
            "ร้านทดสอบ\n"
            "อ้างอิง 99-99-2569 วันที่ 12/11/2569\n"
            "Before VAT 100.00\n"
            "VAT 7% 7.00\n"
            "Total 107.00"
        )
        bv_res = extract_expense_regex(before_vat_text)
        self.assertEqual(bv_res["expense_date"], "2026-11-12")
        self.assertAlmostEqual(bv_res["subtotal"], 100.0, places=2)
        self.assertAlmostEqual(bv_res["vat_amount"], 7.0, places=2)
        self.assertAlmostEqual(bv_res["total_amount"], 107.0, places=2)

        no_vat_amt_text = (
            "ใบเสร็จรับเงิน\n"
            "ร้านกาแฟทดสอบ\n"
            "Total 2 items\n"
            "VAT 7% included\n"
            "ยอดสุทธิ 150.00 บาท"
        )
        nv_res = extract_expense_regex(no_vat_amt_text)
        self.assertAlmostEqual(nv_res["vat_amount"], 0.0, places=2)
        self.assertAlmostEqual(nv_res["total_amount"], 150.0, places=2)

    def test_expense_endpoints_crud_monthly_summary_csv_and_cascade_delete(self) -> None:
        receipt_ocr_text = (
            "ใบเสร็จรับเงิน\n"
            "Cafe Amazon สาขาอารีย์\n"
            "วันที่ 12/11/2569\n"
            "รวมเป็นเงิน 100.00\n"
            "ภาษีมูลค่าเพิ่ม 7% 7.00\n"
            "รวมทั้งสิ้น 107.00 บาท"
        )
        with make_temp_data_dir() as tmpdir:
            cfg = AppConfig.from_dir(tmpdir, inbox_poll_seconds=0)
            test_app = create_app(
                config=cfg,
                engine_factory=lambda: FakeLineOCR(text=receipt_ocr_text),
            )
            with TestClient(test_app) as client:
                up = client.post(
                    "/api/ocr",
                    files={"file": ("amazon.png", make_png_bytes(400, 500), "image/png")},
                )
                self.assertEqual(up.status_code, 200)
                doc_id = up.json()["doc_id"]

                ext_resp = client.post(f"/api/documents/{doc_id}/extract")
                self.assertEqual(ext_resp.status_code, 200)
                expense = ext_resp.json()["expense"]
                self.assertEqual(expense["document_id"], doc_id)
                self.assertEqual(expense["expense_date"], "2026-11-12")
                self.assertAlmostEqual(expense["total_amount"], 107.0, places=2)
                self.assertAlmostEqual(expense["vat_amount"], 7.0, places=2)
                self.assertEqual(expense["extraction_method"], "regex")

                list_resp = client.get("/api/expenses", params={"month": "2026-11"})
                self.assertEqual(list_resp.status_code, 200)
                list_body = list_resp.json()
                self.assertEqual(len(list_body["expenses"]), 1)
                self.assertAlmostEqual(list_body["summary"]["total_amount"], 107.0, places=2)

                upd_resp = client.put(
                    f"/api/expenses/{doc_id}",
                    json={
                        "merchant": "Cafe Amazon Updated",
                        "expense_date": "2026-11-12",
                        "subtotal": 112.15,
                        "vat_amount": 7.85,
                        "total_amount": 120.0,
                        "expense_category": "food",
                    },
                )
                self.assertEqual(upd_resp.status_code, 200)
                self.assertEqual(upd_resp.json()["expense"]["extraction_method"], "manual")
                self.assertAlmostEqual(upd_resp.json()["expense"]["total_amount"], 120.0, places=2)

                csv_resp = client.get("/api/expenses/export.csv", params={"month": "2026-11"})
                self.assertEqual(csv_resp.status_code, 200)
                csv_text = csv_resp.content.decode("utf-8")
                self.assertTrue(csv_text.startswith("\ufeff"))
                rows = list(csv.reader(io.StringIO(csv_text.lstrip("\ufeff"))))
                self.assertEqual(len(rows), 2)
                self.assertIn("Cafe Amazon Updated", rows[1])
                self.assertIn("120.00", rows[1])

                # Deleting Vault Document cascades to Expense Record
                del_doc = client.delete(f"/api/documents/{doc_id}")
                self.assertEqual(del_doc.status_code, 200)
                after = client.get("/api/expenses", params={"month": "2026-11"}).json()
                self.assertEqual(len(after["expenses"]), 0)

    def test_kindergarten_categories_auto_tags_and_tag_filtered_csv_export(self) -> None:
        from extractor import extract_document_intelligence, extract_expense_regex

        cfg = AppConfig.from_dir("/tmp/unused-kindergarten-test", inbox_poll_seconds=0)

        classroom_receipt = (
            "ใบเสร็จรับเงิน\n"
            "ร้านเครื่องเขียนสุขใจ\n"
            "วันที่ 14/11/2569\n"
            "สีเทียน ดินน้ำมัน กระดาษโปสเตอร์\n"
            "เบิกค่าสื่อการสอนโรงเรียนอนุบาล\n"
            "ยอดสุทธิ 320.00 บาท"
        )
        c_res = extract_expense_regex(classroom_receipt)
        self.assertEqual(c_res["expense_category"], "classroom")
        self.assertAlmostEqual(c_res["total_amount"], 320.0, places=2)

        snack_receipt = (
            "ใบเสร็จรับเงิน\n"
            "สหกรณ์ร้านค้า\n"
            "วันที่ 14/11/2569\n"
            "นมโรงเรียน นมกล่อง และขนมเด็ก\n"
            "ยอดสุทธิ 450.00 บาท"
        )
        s_res = extract_expense_regex(snack_receipt)
        self.assertEqual(s_res["expense_category"], "kids_snack")

        school_notice = "หนังสือแจ้งผู้ปกครอง\nโรงเรียนอนุบาลแสนสนุก\nกำหนดการทัศนศึกษาและประเมินพัฒนาการ"
        school_intel = extract_document_intelligence(school_notice, cfg, require_expense_hint=True)
        self.assertFalse(school_intel["is_expense"])
        self.assertEqual(school_intel["doc_category"], "school")
        self.assertIn("ครูอนุบาล", school_intel["tags"])
        self.assertIn("แจ้งผู้ปกครอง", school_intel["tags"])

        teaching_plan = "แผนการจัดประสบการณ์\nกิจกรรมสร้างสรรค์และเกมการศึกษาสำหรับเด็กอนุบาล"
        teaching_intel = extract_document_intelligence(teaching_plan, cfg, require_expense_hint=True)
        self.assertFalse(teaching_intel["is_expense"])
        self.assertEqual(teaching_intel["doc_category"], "teaching")
        self.assertIn("ครูอนุบาล", teaching_intel["tags"])

        with make_temp_data_dir() as tmpdir:
            app_cfg = AppConfig.from_dir(tmpdir, inbox_poll_seconds=0, auto_extract_on_ingest=True)
            personal_receipt = "ใบเสร็จรับเงิน\nร้านกาแฟส่วนตัว\nวันที่ 14/11/2569\nยอดสุทธิ 80.00 บาท"

            def multi_ocr(_img):
                return {
                    "text": classroom_receipt if ocr.calls == 1 else personal_receipt,
                    "lines": [],
                    "rotation": 0,
                    "text_angle": 0.0,
                }

            ocr = FakeLineOCR(responder=multi_ocr)
            test_app = create_app(config=app_cfg, engine_factory=lambda: ocr)
            with TestClient(test_app) as client:
                up1 = client.post(
                    "/api/ocr",
                    files={"file": ("teacher_bill.png", make_png_bytes(400, 500), "image/png")},
                )
                self.assertEqual(up1.status_code, 200)
                self.assertEqual(up1.json()["expense"]["expense_category"], "classroom")
                self.assertIn("ครูอนุบาล", up1.json()["tags"])
                self.assertIn("เบิกโรงเรียน", up1.json()["tags"])

                up2 = client.post(
                    "/api/ocr",
                    files={"file": ("my_coffee.png", make_png_bytes(400, 500), "image/png")},
                    data={"tags": "ส่วนตัว"},
                )
                self.assertEqual(up2.status_code, 200)
                self.assertIn("ส่วนตัว", up2.json()["tags"])

                teacher_list = client.get("/api/expenses", params={"tag": "ครูอนุบาล"}).json()
                self.assertEqual(len(teacher_list["expenses"]), 1)
                self.assertAlmostEqual(teacher_list["summary"]["total_amount"], 320.0, places=2)

                csv_resp = client.get("/api/expenses/export.csv", params={"tag": "ครูอนุบาล"})
                self.assertEqual(csv_resp.status_code, 200)
                csv_text = csv_resp.content.decode("utf-8")
                self.assertTrue(csv_text.startswith("\ufeff"))
                rows = list(csv.reader(io.StringIO(csv_text.lstrip("\ufeff"))))
                self.assertEqual(rows[0][-1], "tags")
                self.assertEqual(len(rows), 2)
                self.assertIn("ครูอนุบาล", rows[1][-1])


if __name__ == "__main__":
    unittest.main()
