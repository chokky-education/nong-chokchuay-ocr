import os
import unittest

from fastapi.testclient import TestClient

from app import create_app
from config import AppConfig
from tests.fakes import FakeLineOCR, make_png_bytes, make_temp_data_dir


class WatcherTest(unittest.TestCase):
    def test_manual_scan_ingests_valid_files_and_quarantines_corrupt_files(self) -> None:
        with make_temp_data_dir() as tmpdir:
            cfg = AppConfig.from_dir(tmpdir, inbox_poll_seconds=0)
            cfg.ensure_dirs()

            valid_path = os.path.join(cfg.inbox_dir, "receipt_01.png")
            with open(valid_path, "wb") as fh:
                fh.write(make_png_bytes(500, 300))

            corrupt_path = os.path.join(cfg.inbox_dir, "broken.png")
            with open(corrupt_path, "wb") as fh:
                fh.write(b"not-a-real-png")

            hidden_path = os.path.join(cfg.inbox_dir, ".DS_Store")
            with open(hidden_path, "wb") as fh:
                fh.write(b"ignored")

            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR())
            with TestClient(test_app) as client:
                scan_resp = client.post("/api/watcher/scan")
                self.assertEqual(scan_resp.status_code, 200)
                scan_data = scan_resp.json()
                self.assertEqual(scan_data["processed"], 1)
                self.assertEqual(scan_data["failed"], 1)

                self.assertFalse(os.path.exists(valid_path))
                self.assertFalse(os.path.exists(corrupt_path))
                self.assertTrue(os.path.exists(hidden_path))

                self.assertTrue(os.path.exists(os.path.join(cfg.inbox_failed_dir, "broken.png")))
                self.assertTrue(
                    os.path.exists(os.path.join(cfg.inbox_failed_dir, "broken.png.error.txt"))
                )

                docs_resp = client.get("/api/documents")
                docs = docs_resp.json()["documents"]
                self.assertEqual(len(docs), 1)
                self.assertEqual(docs[0]["filename"], "receipt_01.png")
                self.assertEqual(docs[0]["ingestion_channel"], "watch_folder")
                self.assertTrue(docs[0]["has_source_file"])

                status_resp = client.get("/api/watcher/status")
                self.assertEqual(status_resp.status_code, 200)
                status = status_resp.json()
                self.assertEqual(status["pending_count"], 0)
                self.assertEqual(status["failed_count"], 1)

    def test_background_stability_guard_requires_two_consecutive_matching_polls(self) -> None:
        from watcher import InboxWatcher

        with make_temp_data_dir() as tmpdir:
            cfg = AppConfig.from_dir(tmpdir, inbox_poll_seconds=0)
            cfg.ensure_dirs()
            target = os.path.join(cfg.inbox_dir, "copying.png")
            with open(target, "wb") as fh:
                fh.write(make_png_bytes(300, 200))

            fake_ocr = FakeLineOCR()
            import database

            database.init_db(cfg.db_path)
            watcher = InboxWatcher(config=cfg, engine_getter=lambda: fake_ocr)

            first = watcher.scan_once(require_stability=True)
            self.assertEqual(first["processed"], 0)
            self.assertTrue(os.path.exists(target))

            second = watcher.scan_once(require_stability=True)
            self.assertEqual(second["processed"], 1)
            self.assertFalse(os.path.exists(target))

    def test_subfolder_inbox_auto_tags_teacher_and_me_while_preserving_folders(self) -> None:
        receipt_text = (
            "ใบเสร็จรับเงิน\n"
            "ร้านเครื่องเขียนอนุบาล\n"
            "วันที่ 14/11/2569\n"
            "สีเทียนและดินน้ำมัน สื่อการสอน 250.00 บาท\n"
            "ยอดสุทธิ 250.00 บาท"
        )
        personal_text = "บันทึกส่วนตัว\nรายการของใช้ในบ้าน"

        with make_temp_data_dir() as tmpdir:
            cfg = AppConfig.from_dir(tmpdir, inbox_poll_seconds=0, auto_extract_on_ingest=True)
            cfg.ensure_dirs()

            teacher_dir = os.path.join(cfg.inbox_dir, "teacher")
            me_dir = os.path.join(cfg.inbox_dir, "me")
            self.assertTrue(os.path.isdir(teacher_dir))
            self.assertTrue(os.path.isdir(me_dir))

            teacher_file = os.path.join(teacher_dir, "crayons.png")
            me_file = os.path.join(me_dir, "personal_note.png")
            with open(teacher_file, "wb") as fh:
                fh.write(make_png_bytes(400, 300))
            with open(me_file, "wb") as fh:
                fh.write(make_png_bytes(400, 300))

            def ocr_by_order(_img):
                # First file alphabetically in inbox is me/personal_note.png, second is teacher/crayons.png
                return {
                    "text": personal_text if ocr.calls == 1 else receipt_text,
                    "lines": [],
                    "rotation": 0,
                    "text_angle": 0.0,
                }

            ocr = FakeLineOCR(responder=ocr_by_order)
            test_app = create_app(config=cfg, engine_factory=lambda: ocr)
            with TestClient(test_app) as client:
                status_before = client.get("/api/watcher/status").json()
                self.assertEqual(status_before["pending_count"], 2)

                scan_resp = client.post("/api/watcher/scan")
                self.assertEqual(scan_resp.status_code, 200)
                self.assertEqual(scan_resp.json()["processed"], 2)

                self.assertFalse(os.path.exists(teacher_file))
                self.assertFalse(os.path.exists(me_file))
                self.assertTrue(os.path.isdir(teacher_dir))
                self.assertTrue(os.path.isdir(me_dir))

                teacher_docs = client.get("/api/documents", params={"tag": "ครูอนุบาล"}).json()["documents"]
                self.assertEqual(len(teacher_docs), 1)
                self.assertEqual(teacher_docs[0]["filename"], "crayons.png")
                self.assertIn("ครูอนุบาล", teacher_docs[0]["tags"])
                self.assertIn("สื่อการสอน", teacher_docs[0]["tags"])

                me_docs = client.get("/api/documents", params={"tag": "ส่วนตัว"}).json()["documents"]
                self.assertEqual(len(me_docs), 1)
                self.assertEqual(me_docs[0]["filename"], "personal_note.png")
                self.assertEqual(me_docs[0]["tags"], ["ส่วนตัว"])

                teacher_expenses = client.get("/api/expenses", params={"tag": "ครูอนุบาล"}).json()["expenses"]
                self.assertEqual(len(teacher_expenses), 1)
                self.assertEqual(teacher_expenses[0]["expense_category"], "classroom")
                self.assertAlmostEqual(teacher_expenses[0]["total_amount"], 250.0, places=2)


if __name__ == "__main__":
    unittest.main()
