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


if __name__ == "__main__":
    unittest.main()
