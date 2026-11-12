import os
import unittest

from fastapi.testclient import TestClient

from config import AppConfig
from app import create_app
from tests.fakes import FakeLineOCR, make_temp_data_dir


class SmokeTest(unittest.TestCase):
    def test_system_endpoint_uses_isolated_storage_without_touching_prod_db(self) -> None:
        with make_temp_data_dir() as tmpdir:
            cfg = AppConfig.from_dir(tmpdir, inbox_poll_seconds=0)
            fake_ocr = FakeLineOCR()
            test_app = create_app(config=cfg, engine_factory=lambda: fake_ocr)

            with TestClient(test_app) as client:
                resp = client.get("/api/system")
                self.assertEqual(resp.status_code, 200)
                payload = resp.json()
                self.assertTrue(payload["offline"])
                self.assertEqual(payload["database"]["total_documents"], 0)
                self.assertTrue(payload["database"]["path"].startswith(tmpdir))
                self.assertTrue(os.path.isdir(cfg.uploads_dir))
                self.assertTrue(os.path.isdir(cfg.inbox_dir))


if __name__ == "__main__":
    unittest.main()
