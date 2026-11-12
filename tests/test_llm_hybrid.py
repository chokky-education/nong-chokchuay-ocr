import json
import unittest
from unittest.mock import MagicMock, patch

import requests
from fastapi.testclient import TestClient

from app import create_app
from config import AppConfig
from tests.fakes import FakeLineOCR, make_png_bytes, make_temp_data_dir


class LlmHybridAdapterTest(unittest.TestCase):
    def test_llm_adapter_sends_only_text_and_applies_tags_and_expense(self) -> None:
        ocr_text = "STARBUCKS COFFEE\nวันที่ 12/11/2569\nIced Latte 165.00\nรวมทั้งสิ้น 165.00 บาท"
        llm_json = {
            "is_expense": True,
            "doc_category": "receipt",
            "tags": ["กาแฟ", "ทำงาน"],
            "merchant": "Starbucks Coffee",
            "expense_date": "2026-11-12",
            "subtotal": 154.21,
            "vat_amount": 10.79,
            "total_amount": 165.00,
            "currency": "THB",
            "expense_category": "food",
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": json.dumps(llm_json, ensure_ascii=False)}]}}
            ]
        }

        with make_temp_data_dir() as tmpdir:
            cfg = AppConfig.from_dir(
                tmpdir,
                inbox_poll_seconds=0,
                auto_extract_on_ingest=True,
                llm_provider="gemini",
                llm_api_key="fake-gemini-key",
                llm_model="gemini-2.5-flash",
            )
            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR(text=ocr_text))

            with patch("extractor.requests.post", return_value=mock_resp) as mock_post:
                with TestClient(test_app) as client:
                    sys_resp = client.get("/api/system")
                    self.assertTrue(sys_resp.json()["llm_configured"])

                    up = client.post(
                        "/api/ocr",
                        files={"file": ("sbux.png", make_png_bytes(350, 500), "image/png")},
                    )
                    self.assertEqual(up.status_code, 200)
                    body = up.json()
                    self.assertEqual(body["category"], "receipt")
                    self.assertEqual(body["tags"], ["กาแฟ", "ทำงาน"])
                    self.assertIsNotNone(body["expense"])
                    self.assertEqual(body["expense"]["extraction_method"], "llm")
                    self.assertEqual(body["expense"]["merchant"], "Starbucks Coffee")
                    self.assertAlmostEqual(body["expense"]["total_amount"], 165.00, places=2)

                self.assertEqual(mock_post.call_count, 1)
                sent_payload = json.dumps(mock_post.call_args.kwargs.get("json", {}))
                self.assertIn("STARBUCKS COFFEE", sent_payload)
                self.assertNotIn("data:image", sent_payload)
                self.assertNotIn("inlineData", sent_payload)
                self.assertNotIn("image_url", sent_payload)

    def test_llm_timeout_falls_back_cleanly_to_offline_regex(self) -> None:
        ocr_text = (
            "ใบเสร็จรับเงิน\n"
            "ร้านหนังสือทดสอบ\n"
            "วันที่ 12/11/2569\n"
            "ยอดสุทธิ 250.00 บาท"
        )
        with make_temp_data_dir() as tmpdir:
            cfg = AppConfig.from_dir(
                tmpdir,
                inbox_poll_seconds=0,
                llm_provider="openai",
                llm_api_key="fake-openai-key",
                llm_model="gpt-4o-mini",
            )
            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR(text=ocr_text))
            with patch("extractor.requests.post", side_effect=requests.Timeout("timed out")):
                with TestClient(test_app) as client:
                    up = client.post(
                        "/api/ocr",
                        files={"file": ("book.png", make_png_bytes(300, 400), "image/png")},
                    )
                    doc_id = up.json()["doc_id"]

                    ext = client.post(f"/api/documents/{doc_id}/extract")
                    self.assertEqual(ext.status_code, 200)
                    expense = ext.json()["expense"]
                    self.assertEqual(expense["extraction_method"], "regex")
                    self.assertAlmostEqual(expense["total_amount"], 250.00, places=2)
                    self.assertEqual(expense["expense_date"], "2026-11-12")

    def test_auto_extract_skips_non_expense_document(self) -> None:
        memo_text = "บันทึกข้อความ\nส่วนราชการ สำนักงานทดสอบ\nเรื่อง ขอเชิญประชุมประจำปี"
        with make_temp_data_dir() as tmpdir:
            cfg = AppConfig.from_dir(
                tmpdir,
                inbox_poll_seconds=0,
                auto_extract_on_ingest=True,
            )
            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR(text=memo_text))
            with TestClient(test_app) as client:
                up = client.post(
                    "/api/ocr",
                    files={"file": ("memo.png", make_png_bytes(400, 600), "image/png")},
                )
                self.assertEqual(up.status_code, 200)
                self.assertIsNone(up.json().get("expense"))
                expenses = client.get("/api/expenses").json()["expenses"]
                self.assertEqual(expenses, [])

    def test_keyless_local_ollama_base_url_works_without_api_key(self) -> None:
        llm_json = {
            "is_expense": True,
            "doc_category": "receipt",
            "tags": ["ollama"],
            "merchant": "Local Cafe",
            "expense_date": "2026-11-12",
            "subtotal": 50.0,
            "vat_amount": 0.0,
            "total_amount": 50.0,
            "currency": "THB",
            "expense_category": "food",
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": json.dumps(llm_json)}}]
        }

        with make_temp_data_dir() as tmpdir:
            cfg = AppConfig.from_dir(
                tmpdir,
                inbox_poll_seconds=0,
                llm_provider="openai",
                llm_api_key="",
                llm_model="qwen2.5:7b",
                llm_base_url="http://127.0.0.1:11434/v1",
            )
            test_app = create_app(
                config=cfg,
                engine_factory=lambda: FakeLineOCR(text="Local Cafe\n50.00 บาท"),
            )
            with patch("extractor.requests.post", return_value=mock_resp) as mock_post:
                with TestClient(test_app) as client:
                    sys_resp = client.get("/api/system")
                    self.assertTrue(sys_resp.json()["llm_configured"])
                    up = client.post(
                        "/api/ocr",
                        files={"file": ("cafe.png", make_png_bytes(300, 300), "image/png")},
                        data={"auto_extract": "true"},
                    )
                    self.assertEqual(up.status_code, 200)
                    self.assertEqual(up.json()["expense"]["extraction_method"], "llm")
                    self.assertEqual(up.json()["expense"]["merchant"], "Local Cafe")
                self.assertEqual(mock_post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
