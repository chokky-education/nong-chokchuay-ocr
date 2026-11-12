import os
import unittest

from fastapi.testclient import TestClient

from app import create_app
from config import AppConfig
from tests.fakes import make_temp_data_dir

SAMPLE_BUDGET = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "samples",
    "sample_budget_table.png",
)


class RealOcrSmokeTest(unittest.TestCase):
    def test_real_onnx_engine_reads_thai_sample_into_isolated_vault(self) -> None:
        self.assertTrue(os.path.exists(SAMPLE_BUDGET))
        with make_temp_data_dir() as tmpdir:
            cfg = AppConfig.from_dir(tmpdir, inbox_poll_seconds=0)
            test_app = create_app(config=cfg)  # Real _default_engine_factory (LineOCR)
            with open(SAMPLE_BUDGET, "rb") as fh:
                sample_bytes = fh.read()

            with TestClient(test_app) as client:
                resp = client.post(
                    "/api/ocr",
                    files={"file": ("sample_budget_table.png", sample_bytes, "image/png")},
                    data={"category": "official", "tags": "งบประมาณ"},
                )
                self.assertEqual(resp.status_code, 200)
                body = resp.json()
                self.assertTrue(body["success"])
                self.assertTrue(body["doc_id"])
                self.assertGreater(len(body["lines"]), 5)
                self.assertGreater(body["image_width"], 0)
                self.assertGreater(body["image_height"], 0)
                self.assertIn("งบประมาณ", body["text"])

                # Verify FTS5 finds the real OCR output and Source File is downloadable
                search_resp = client.get("/api/documents", params={"query": "งบประมาณ"})
                self.assertEqual(len(search_resp.json()["documents"]), 1)
                dl_resp = client.get(f"/api/documents/{body['doc_id']}/file")
                self.assertEqual(dl_resp.status_code, 200)
                self.assertEqual(dl_resp.content, sample_bytes)


if __name__ == "__main__":
    unittest.main()
