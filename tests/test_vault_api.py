import os
import sqlite3
import unittest

from fastapi.testclient import TestClient

import database
from app import create_app
from config import AppConfig
from tests.fakes import FakeLineOCR, make_png_bytes, make_temp_data_dir


class VaultApiTest(unittest.TestCase):
    def test_legacy_db_migration_preserves_existing_rows_and_fts_search(self) -> None:
        with make_temp_data_dir() as tmpdir:
            cfg = AppConfig.from_dir(tmpdir, inbox_poll_seconds=0)
            os.makedirs(tmpdir, exist_ok=True)
            conn = sqlite3.connect(cfg.db_path)
            conn.execute("""
                CREATE TABLE documents (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    file_size INTEGER DEFAULT 0,
                    total_pages INTEGER DEFAULT 1,
                    text_content TEXT,
                    lines_json TEXT,
                    avg_confidence REAL DEFAULT 0.0,
                    duration_ms REAL DEFAULT 0.0,
                    image_preview TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE VIRTUAL TABLE documents_fts USING fts5(
                    id UNINDEXED,
                    filename,
                    text_content,
                    tokenize="trigram"
                )
            """)
            conn.execute(
                """
                INSERT INTO documents (
                    id, filename, file_type, file_size, total_pages,
                    text_content, lines_json, avg_confidence, duration_ms,
                    image_preview, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-doc-1",
                    "old_budget.png",
                    "image",
                    1234,
                    1,
                    "เอกสารงบประมาณประจำปีเดิม",
                    "[]",
                    0.95,
                    250.0,
                    "",
                    "2026-11-12 09:00:00",
                    "2026-11-12 09:00:00",
                ),
            )
            conn.execute(
                "INSERT INTO documents_fts (id, filename, text_content) VALUES (?, ?, ?)",
                ("legacy-doc-1", "old_budget.png", "เอกสารงบประมาณประจำปีเดิม"),
            )
            conn.commit()
            conn.close()

            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR())
            with TestClient(test_app) as client:
                resp = client.get("/api/documents", params={"query": "งบประมาณ"})
                self.assertEqual(resp.status_code, 200)
                docs = resp.json()["documents"]
                self.assertEqual(len(docs), 1)
                self.assertEqual(docs[0]["id"], "legacy-doc-1")
                self.assertEqual(docs[0]["category"], "general")
                self.assertEqual(docs[0]["tags"], [])

                detail = client.get("/api/documents/legacy-doc-1")
                self.assertEqual(detail.status_code, 200)
                self.assertEqual(detail.json()["document"]["text"], "เอกสารงบประมาณประจำปีเดิม")

    def test_upload_saves_source_file_dimensions_and_supports_download_patch_filter_delete(self) -> None:
        with make_temp_data_dir() as tmpdir:
            cfg = AppConfig.from_dir(tmpdir, inbox_poll_seconds=0)
            png_bytes = make_png_bytes(width=640, height=480)
            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR())

            with TestClient(test_app) as client:
                upload_resp = client.post(
                    "/api/ocr",
                    files={"file": ("coffee_slip.png", png_bytes, "image/png")},
                    data={"category": "receipt", "tags": "กาแฟ, ภาษี"},
                )
                self.assertEqual(upload_resp.status_code, 200)
                body = upload_resp.json()
                doc_id = body["doc_id"]
                self.assertTrue(doc_id)
                self.assertEqual(body["image_width"], 640)
                self.assertEqual(body["image_height"], 480)
                self.assertEqual(body["category"], "receipt")
                self.assertEqual(body["tags"], ["กาแฟ", "ภาษี"])
                self.assertTrue(body["has_source_file"])

                # Download original unmodified Source File
                file_resp = client.get(f"/api/documents/{doc_id}/file")
                self.assertEqual(file_resp.status_code, 200)
                self.assertEqual(file_resp.content, png_bytes)

                # Reopen doc detail retains real dimensions & metadata
                detail_resp = client.get(f"/api/documents/{doc_id}")
                self.assertEqual(detail_resp.status_code, 200)
                doc = detail_resp.json()["document"]
                self.assertEqual(doc["image_width"], 640)
                self.assertEqual(doc["image_height"], 480)
                self.assertEqual(doc["ingestion_channel"], "web_upload")
                self.assertTrue(doc["has_source_file"])

                # Patch text, category, tags, doc_date -> re-indexes FTS5
                patch_resp = client.patch(
                    f"/api/documents/{doc_id}",
                    json={
                        "text": "แก้คำผิด ใบกำกับภาษีเต็มรูป",
                        "category": "tax",
                        "tags": ["บัญชี", "ภาษี"],
                        "doc_date": "2026-11-12",
                    },
                )
                self.assertEqual(patch_resp.status_code, 200)
                self.assertEqual(patch_resp.json()["document"]["category"], "tax")
                self.assertEqual(patch_resp.json()["document"]["tags"], ["บัญชี", "ภาษี"])

                search_new = client.get("/api/documents", params={"query": "ใบกำกับภาษีเต็มรูป"})
                self.assertEqual(len(search_new.json()["documents"]), 1)

                by_cat = client.get("/api/documents", params={"category": "tax"})
                self.assertEqual(len(by_cat.json()["documents"]), 1)
                wrong_cat = client.get("/api/documents", params={"category": "official"})
                self.assertEqual(len(wrong_cat.json()["documents"]), 0)

                by_tag = client.get("/api/documents", params={"tag": "บัญชี"})
                self.assertEqual(len(by_tag.json()["documents"]), 1)

                # Deleting document removes Source File from disk too
                saved_files = os.listdir(cfg.uploads_dir)
                self.assertEqual(len(saved_files), 1)
                del_resp = client.delete(f"/api/documents/{doc_id}")
                self.assertEqual(del_resp.status_code, 200)
                self.assertEqual(os.listdir(cfg.uploads_dir), [])
                self.assertEqual(client.get(f"/api/documents/{doc_id}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
