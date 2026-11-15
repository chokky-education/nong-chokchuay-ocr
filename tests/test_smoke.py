import os
import hashlib
import re
from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from config import AppConfig
from app import create_app
from tests.fakes import FakeLineOCR, make_temp_data_dir


class SmokeTest(unittest.TestCase):
    def test_index_versions_assets_by_content_and_revalidates_the_html(self) -> None:
        with make_temp_data_dir() as tmpdir:
            static = Path(tmpdir) / "static"
            static.mkdir()
            (static / "index.html").write_text('<link href="/static/styles.css"><script src="/static/app.js"></script>')
            (static / "styles.css").write_text('body {color: blue;}')
            (static / "app.js").write_text('const version = 1;')
            cfg = AppConfig.from_dir(str(Path(tmpdir) / "data"), auth_required=True, inbox_poll_seconds=0)
            with TestClient(create_app(config=cfg, engine_factory=FakeLineOCR, static_dir=str(static))) as client:
                first = client.get("/")
                self.assertEqual(first.headers["cache-control"], "no-cache")
                for asset in ("styles.css", "app.js"):
                    digest = hashlib.sha256((static / asset).read_bytes()).hexdigest()[:16]
                    self.assertIn(f'/static/{asset}?v={digest}', first.text)
                    self.assertEqual(client.get(f"/static/{asset}?v={digest}").status_code, 200)
                old_url = re.search(r'src="([^"]+)"', first.text).group(1)
                (static / "app.js").write_text('const version = 2;')
                second = client.get("/", headers={"If-None-Match": "old-index"})
                self.assertEqual(second.status_code, 200)
                self.assertNotIn(old_url, second.text)

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
