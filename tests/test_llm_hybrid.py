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
                llm_model="gemini-4-hydrogen",
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

    def test_from_env_defaults_auto_extract_true_and_supports_gemini_key_fallback(self) -> None:
        import os
        import config

        with make_temp_data_dir() as tmpdir:
            env_path = os.path.join(tmpdir, ".env")
            with open(env_path, "w", encoding="utf-8") as fh:
                fh.write("LLM_API_KEY=\n")
                fh.write("LLM_MODEL=   \n")
                fh.write("GEMINI_API_KEY=from-dotenv-gemini-key\n")
                fh.write("AUTH_REQUIRED=false\n")

            with patch.object(config, "BASE_DIR", tmpdir):
                with patch.dict(os.environ, {"OPENTHAI_DATA_DIR": tmpdir}, clear=True):
                    cfg = AppConfig.from_env()
                    self.assertTrue(cfg.auto_extract_on_ingest)
                    self.assertEqual(cfg.llm_api_key, "from-dotenv-gemini-key")
                    self.assertEqual(cfg.llm_model, "gemini-4-hydrogen")

            # Also ensure a later non-empty LLM_API_KEY overrides an earlier empty LLM_API_KEY= line
            with open(env_path, "a", encoding="utf-8") as fh:
                fh.write("LLM_API_KEY=later-nonempty-key\n")

            with patch.object(config, "BASE_DIR", tmpdir):
                with patch.dict(os.environ, {"OPENTHAI_DATA_DIR": tmpdir}, clear=True):
                    cfg2 = AppConfig.from_env()
                    self.assertEqual(cfg2.llm_api_key, "later-nonempty-key")
                    self.assertEqual(cfg2.llm_model, "gemini-4-hydrogen")

    def test_gemini_4_hydrogen_skips_thought_parts_and_uses_default_model(self) -> None:
        llm_json = {
            "is_expense": True,
            "doc_category": "receipt",
            "tags": ["ครูอนุบาล", "เบิกโรงเรียน"],
            "merchant": "ร้านสมใจเครื่องเขียน",
            "expense_date": "2026-11-15",
            "subtotal": 320.0,
            "vat_amount": 0.0,
            "total_amount": 320.0,
            "currency": "THB",
            "expense_category": "classroom",
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"thought": True, "text": "วิเคราะห์ใบเสร็จเครื่องเขียน..."},
                            {"text": json.dumps(llm_json, ensure_ascii=False)},
                        ]
                    }
                }
            ]
        }

        with make_temp_data_dir() as tmpdir:
            cfg = AppConfig.from_dir(
                tmpdir,
                inbox_poll_seconds=0,
                auto_extract_on_ingest=True,
                llm_api_key="fake-gemini-key",
            )
            self.assertEqual(cfg.llm_model, "gemini-4-hydrogen")
            test_app = create_app(
                config=cfg,
                engine_factory=lambda: FakeLineOCR(text="ร้านสมใจเครื่องเขียน 320.00 บาท"),
            )
            with patch("extractor.requests.post", return_value=mock_resp) as mock_post:
                with TestClient(test_app) as client:
                    up = client.post(
                        "/api/ocr",
                        files={"file": ("stationery.png", make_png_bytes(300, 300), "image/png")},
                    )
                    self.assertEqual(up.status_code, 200)
                    self.assertEqual(up.json()["expense"]["extraction_method"], "llm")
                    self.assertEqual(up.json()["expense"]["merchant"], "ร้านสมใจเครื่องเขียน")

                self.assertIn("models/gemini-4-hydrogen:generateContent", mock_post.call_args.args[0])
                self.assertEqual(mock_post.call_args.kwargs["timeout"], 20)
                gen_cfg = mock_post.call_args.kwargs["json"]["generationConfig"]
                self.assertEqual(gen_cfg["maxOutputTokens"], 4096)

    def test_provider_whitespace_and_openai_blank_model_resolution(self) -> None:
        import os
        import config

        with make_temp_data_dir() as tmpdir:
            env_path = os.path.join(tmpdir, ".env")
            with open(env_path, "w", encoding="utf-8") as fh:
                fh.write("LLM_PROVIDER=   \n")
                fh.write("LLM_MODEL=   \n")
                fh.write("LLM_API_KEY=fake-key\n")
                fh.write("AUTH_REQUIRED=false\n")

            with patch.object(config, "BASE_DIR", tmpdir):
                with patch.dict(os.environ, {"OPENTHAI_DATA_DIR": tmpdir}, clear=True):
                    cfg_ws = AppConfig.from_env()
                    self.assertEqual(cfg_ws.llm_provider, "gemini")
                    self.assertEqual(cfg_ws.llm_model, "gemini-4-hydrogen")

            with open(env_path, "w", encoding="utf-8") as fh:
                fh.write("LLM_PROVIDER=openai\n")
                fh.write("LLM_MODEL=   \n")
                fh.write("LLM_API_KEY=fake-openai-key\n")
                fh.write("AUTH_REQUIRED=false\n")

            with patch.object(config, "BASE_DIR", tmpdir):
                with patch.dict(os.environ, {"OPENTHAI_DATA_DIR": tmpdir}, clear=True):
                    cfg_oa = AppConfig.from_env()
                    self.assertEqual(cfg_oa.llm_provider, "openai")
                    self.assertEqual(cfg_oa.llm_model, "gpt-4o-mini")


if __name__ == "__main__":
    unittest.main()
