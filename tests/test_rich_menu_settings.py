import base64
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from PIL import Image

from app import create_app
from config import AppConfig
import database
import line_service
import ocr_service
import rich_menu
import storage_service
from scripts import line_rich_menu
from tests.fakes import FakeLineOCR, make_png_bytes
from tests.test_r2_persistence import InMemoryR2Gateway, _r2_config


VALUES = {"teacher_name": "ครูทดสอบ", "classroom": "อนุบาล 3/2", "default_reimbursement_status": "personal"}


class RichMenuSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = AppConfig.from_dir(self.tmp.name, auto_extract_on_ingest=True, public_base_url="https://school.example",
                                      line_channel_secret="test-secret", line_channel_access_token="test-token")
        self.engine = FakeLineOCR()
        self.app = create_app(self.cfg, engine_factory=lambda: self.engine)
        self.client = self.enterContext(TestClient(self.app))

    def save(self, **changes):
        result = self.client.put("/api/settings", json={**VALUES, **changes})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()["settings"]

    def test_all_eight_actions_and_asset_coordinates(self):
        payload = self.client.get("/api/line/rich-menu").json()
        menu = payload["menu"]
        self.assertEqual(len(menu["areas"]), 8)
        self.assertLessEqual(len(menu["chatBarText"]), 14)
        self.assertTrue(all(len(area["action"]["label"]) <= 20 for area in menu["areas"]))
        self.assertEqual(len({item["key"] for item in payload["items"]}), 8)
        asset = self.client.get(payload["image_path"])
        self.assertEqual(asset.status_code, 200)
        image_file = Path(__file__).resolve().parents[1] / rich_menu.IMAGE_PATH.lstrip("/")
        with Image.open(image_file) as im:
            self.assertEqual(im.size, (menu["size"]["width"], menu["size"]["height"]))
        self.assertLess(image_file.stat().st_size, 1024 * 1024)
        boxes = []
        for item in payload["items"]:
            b, action = item["bounds"], item["action"]
            self.assertLessEqual(b["x"] + b["width"], rich_menu.WIDTH)
            self.assertLessEqual(b["y"] + b["height"], rich_menu.HEIGHT)
            for prev in boxes:
                overlap = b["x"] < prev["x"] + prev["width"] and prev["x"] < b["x"] + b["width"] and b["y"] < prev["y"] + prev["height"] and prev["y"] < b["y"] + b["height"]
                self.assertFalse(overlap)
            boxes.append(b)
            if action["type"] == "uri":
                parts = urlsplit(action["uri"])
                self.assertEqual(parts.netloc, "school.example")
                params = parse_qs(parts.query)
                self.assertIn(params["view"][0], {"expenses", "students", "settings"})
                self.assertEqual(params["openExternalBrowser"], ["1"])
                self.assertEqual(self.client.get(parts.path + "?" + parts.query).status_code, 200)
            else:
                with patch("line_service.generate_nong_chok_ai_reply") as ai:
                    result = self.client.post("/api/line/simulate", data={"postback_data": action["data"]})
                    self.assertEqual(result.status_code, 200)
                    self.assertTrue(result.json()["line_messages"])
                    ai.assert_not_called()
                    if item["key"] == "upload":
                        types = [i["action"]["type"] for i in result.json()["line_messages"][0]["quickReply"]["items"]]
                        self.assertEqual(types, ["camera", "cameraRoll"])
                    if item["key"] == "search":
                        self.assertEqual(action["fillInText"], "ค้นหา ")

    def test_settings_validate_persist_and_do_not_rewrite_records(self):
        self.assertFalse(self.client.get("/api/settings").json()["settings"]["configured"])
        first = self.save()
        old = self.client.post("/api/students", json={"full_name": "เด็กหญิงหนึ่ง"}).json()["student"]
        self.assertEqual(old["classroom"], VALUES["classroom"])
        self.save(teacher_name=" ครูใหม่ ", classroom="อนุบาล 1/1")
        self.assertEqual(self.client.get("/api/system").json()["teacher_name"], "ครูใหม่")
        self.assertEqual(self.client.get("/api/students").json()["students"][0]["classroom"], old["classroom"])
        self.assertEqual(database.get_teacher_settings(self.cfg.db_path)["created_at"], first["created_at"])
        for invalid in ({"teacher_name": "   "}, {"teacher_name": "x" * 61}, {"classroom": ""}, {"classroom": "A\nB"}, {"default_reimbursement_status": "reimbursed"}, {"api_key": "never-accepted"}):
            res = self.client.put("/api/settings", json={**VALUES, **invalid})
            self.assertEqual(res.status_code, 422, res.text)
        database.init_db(self.cfg.db_path)
        self.assertEqual(database.get_teacher_settings(self.cfg.db_path)["teacher_name"], "ครูใหม่")

    def test_preferences_apply_to_all_ingestion_channels_and_explicit_class_wins(self):
        self.save()
        for channel in ("web_upload", "watch_folder", "line_oa", "api"):
            result = ocr_service.ingest_document(make_png_bytes(), "bill.png", self.engine, self.cfg,
                ingestion_channel=channel, storage=self.app.state.storage)
            self.assertEqual(result["expense"]["reimbursement_status"], "personal")
            self.assertEqual(result["expense"]["budget_source"], "personal")
        self.engine.default_text = "ใบสมัครเข้าเรียน\nชื่อ-นามสกุล: เด็กหญิงมะลิ ใจดี\nชื่อเล่น: มะลิ\nแพ้อาหาร: แพ้นมวัว"
        for classroom_line, expected in (("", "อนุบาล 3/2"), ("\nชั้นเรียน: อนุบาล 2/1", "อนุบาล 2/1")):
            engine = FakeLineOCR(self.engine.default_text + classroom_line)
            result = ocr_service.ingest_document(make_png_bytes(), "child.png", engine, self.cfg, storage=self.app.state.storage)
            self.assertEqual(result["student"]["classroom"], expected)
            self.assertEqual(result["review_status"], "pending_review")

    def test_line_webhook_uses_saved_name_and_search_bypasses_llm(self):
        self.save()
        event = {"type": "postback", "replyToken": "reply", "source": {"type": "user", "userId": "Utest"},
                 "postback": {"data": "action=run_cmd&cmd=" + "ส่งเอกสาร"}}
        raw = json.dumps({"events": [event]}).encode()
        signature = base64.b64encode(hmac.new(b"test-secret", raw, hashlib.sha256).digest()).decode()
        with patch("line_service.start_line_loading"), patch("line_service.reply_line_messages") as reply:
            res = self.client.post("/api/line/webhook", content=raw, headers={"X-Line-Signature": signature})
            self.assertEqual(res.status_code, 200)
            self.assertIn(VALUES["teacher_name"], reply.call_args.args[1][0]["text"])
        with patch("line_service.generate_nong_chok_ai_reply") as ai:
            for text in ("ค้นหาเอกสาร", "ค้นหา ", "วิธีใช้", "ส่งเอกสาร"):
                self.assertEqual(self.client.post("/api/line/simulate", data={"text": text}).status_code, 200)
            ai.assert_not_called()

    def test_protected_settings_require_auth_and_csrf(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = AppConfig.from_dir(d, auth_required=True, auth_session_secret="test-only-session-secret",
                                     allowed_emails=("teacher@example.com",), public_base_url="https://school.example")
            with TestClient(create_app(cfg, engine_factory=FakeLineOCR)) as client:
                self.assertEqual(client.get("/api/settings").status_code, 401)
                self.assertEqual(client.get("/api/line/rich-menu").status_code, 401)
                self.assertEqual(client.put("/api/settings", json=VALUES).status_code, 401)
                session = {"email": "teacher@example.com", "csrf_token": "test-csrf"}
                with patch("auth_service.authenticate_request", return_value=session):
                    self.assertEqual(client.put("/api/settings", json=VALUES).status_code, 403)
                    res = client.put("/api/settings", json=VALUES, headers={"Origin": "https://school.example", "X-CSRF-Token": "test-csrf"})
                    self.assertEqual(res.status_code, 200)
                    self.assertEqual(res.json()["settings"]["updated_by"], session["email"])

    def test_r2_failure_rolls_back_settings_and_restart_restores_success(self):
        r2 = InMemoryR2Gateway()
        with tempfile.TemporaryDirectory() as d:
            cfg = _r2_config(d, bootstrap=True)
            app = create_app(cfg, engine_factory=FakeLineOCR, storage_client=r2)
            with TestClient(app) as client:
                self.assertEqual(client.put("/api/settings", json=VALUES).status_code, 200)
                revision = app.state.storage.current_revision
                r2.put_manifest_override = storage_service.StorageCommitError("down", error_code="test_down")
                res = client.put("/api/settings", json={**VALUES, "teacher_name": "ไม่ควรถูกบันทึก"})
                self.assertEqual(res.status_code, 503)
                self.assertEqual(client.get("/api/settings").json()["settings"]["teacher_name"], VALUES["teacher_name"])
                self.assertEqual(app.state.storage.current_revision, revision)
            r2.put_manifest_override = None
        with tempfile.TemporaryDirectory() as d:
            with TestClient(create_app(_r2_config(d, bootstrap=False), engine_factory=FakeLineOCR, storage_client=r2)) as client:
                self.assertEqual(client.get("/api/settings").json()["settings"]["classroom"], VALUES["classroom"])

    def test_migration_from_v3_preserves_data_and_preferences_idempotently(self):
        student = database.upsert_student(full_name="เด็กเดิม", db_path=self.cfg.db_path)
        with database.get_db_connection(self.cfg.db_path) as conn:
            conn.execute("DROP TABLE teacher_settings")
            conn.execute("PRAGMA user_version=3")
        database.init_db(self.cfg.db_path)
        self.save()
        database.init_db(self.cfg.db_path)
        self.assertEqual(database.get_student_by_id(student["id"], self.cfg.db_path)["full_name"], "เด็กเดิม")
        self.assertEqual(database.get_teacher_settings(self.cfg.db_path)["classroom"], VALUES["classroom"])

    def test_settings_idempotency_and_reextraction_preserve_reviewed_status(self):
        self.save()
        headers = {"Idempotency-Key": "settings-one"}
        first = self.client.put("/api/settings", json=VALUES, headers=headers)
        self.assertEqual(self.client.put("/api/settings", json=VALUES, headers=headers).json(), first.json())
        self.assertEqual(self.client.put("/api/settings", json={**VALUES, "teacher_name": "คนละคำขอ"}, headers=headers).status_code, 409)
        result = ocr_service.ingest_document(make_png_bytes(), "bill.png", self.engine, self.cfg, storage=self.app.state.storage)
        doc_id = result["doc_id"]
        self.client.post(f"/api/documents/{doc_id}/review", json={"review_status": "reviewed", "reimbursement_status": "reimbursed"})
        self.assertEqual(self.client.post(f"/api/documents/{doc_id}/extract").status_code, 200)
        self.assertEqual(self.client.get(f"/api/documents/{doc_id}").json()["document"]["expense"]["reimbursement_status"], "reimbursed")

    def test_manual_api_expense_creation_uses_defaults_without_overwriting_explicit_or_existing_status(self):
        self.save()
        def new_document():
            response = self.client.post("/api/ocr", data={"auto_extract": "false"},
                files={"file": ("new-bill.png", make_png_bytes(), "image/png")})
            self.assertEqual(response.status_code, 200)
            return response.json()["doc_id"]

        payload = {"merchant": "ร้านทดสอบ", "total_amount": 100, "expense_category": "classroom"}
        doc_id = new_document()
        created = self.client.put(f"/api/expenses/{doc_id}", json=payload)
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["expense"]["reimbursement_status"], "personal")
        self.assertEqual(created.json()["expense"]["budget_source"], "personal")

        explicit_id = new_document()
        explicit = self.client.put(f"/api/expenses/{explicit_id}",
            json={**payload, "reimbursement_status": "pending_reimbursement"})
        self.assertEqual(explicit.json()["expense"]["reimbursement_status"], "pending_reimbursement")
        self.assertEqual(explicit.json()["expense"]["budget_source"], "classroom")

        self.save(default_reimbursement_status="pending_reimbursement")
        updated = self.client.put(f"/api/expenses/{doc_id}", json={**payload, "total_amount": 200})
        self.assertEqual(updated.json()["expense"]["reimbursement_status"], "personal")
        self.assertEqual(updated.json()["expense"]["total_amount"], 200)

    def test_v4_snapshot_missing_settings_table_is_rejected(self):
        with database.get_db_connection(self.cfg.db_path) as conn:
            conn.execute("DROP TABLE teacher_settings")
        with self.assertRaisesRegex(ValueError, "teacher_settings"):
            database.validate_sqlite_snapshot_file(self.cfg.db_path)

    def test_restores_real_v3_r2_snapshot_then_commits_v4(self):
        r2 = InMemoryR2Gateway()
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "legacy.db")
            database.init_db(path)
            database.upsert_student(full_name="เด็กจากรุ่นเดิม", db_path=path)
            with database.get_db_connection(path) as conn:
                conn.execute("DROP TABLE teacher_settings")
                conn.execute("PRAGMA user_version=3")
            snap = str(Path(d) / "snapshot.db")
            meta = database.export_db_snapshot(path, snap)
            key = "rev-00000001-abcdef123456.db"
            r2.put_snapshot(key, snap, sha256=meta["sha256"], revision=1, schema_version=3)
            r2.put_manifest({"revision": 1, "schema_version": 3, "snapshot_key": key,
                "snapshot_sha256": meta["sha256"], "snapshot_size": meta["size"], "retained_snapshots": []}, if_none_match_star=True)
            with tempfile.TemporaryDirectory() as fresh:
                with TestClient(create_app(_r2_config(fresh, bootstrap=False), engine_factory=FakeLineOCR, storage_client=r2)) as client:
                    self.assertEqual(client.get("/api/health").json()["storage_state"], "READY")
                    self.assertEqual(client.get("/api/students").json()["students"][0]["full_name"], "เด็กจากรุ่นเดิม")
                    self.assertEqual(client.put("/api/settings", json=VALUES).status_code, 200)
                    self.assertEqual(r2.get_manifest()[1]["schema_version"], 4)

    def test_install_records_rollback_and_activates_only_after_image_upload(self):
        with tempfile.TemporaryDirectory() as d, patch.object(line_rich_menu, "line_request") as request:
            request.side_effect = [{"richMenuId": "old"}, {}, {"richMenuId": "new"}, {}, {}, {"richMenuId": "new"}]
            state_path = Path(d) / "rollback.json"
            line_rich_menu.install("https://school.example", "test-token", state_path)
            self.assertEqual(json.loads(state_path.read_text())["previous_default"], "old")
            self.assertEqual([c.args[1] for c in request.call_args_list], ["/user/all/richmenu", "/richmenu/validate", "/richmenu", "/richmenu/new/content", "/user/all/richmenu/new", "/user/all/richmenu"])
            request.reset_mock()
            request.side_effect = [{"richMenuId": "new"}, {}]
            line_rich_menu.restore("test-token", state_path)
            self.assertEqual(request.call_args.args[:2], ("POST", "/user/all/richmenu/old"))
            request.side_effect = [{"richMenuId": "some-newer-release"}]
            with self.assertRaises(RuntimeError):
                line_rich_menu.restore("test-token", state_path)

    def test_install_failure_never_activates_half_uploaded_menu(self):
        with tempfile.TemporaryDirectory() as d, patch.object(line_rich_menu, "line_request") as request:
            request.side_effect = [None, {}, {"richMenuId": "new"}, RuntimeError("upload failed")]
            state_path = Path(d) / "rollback.json"
            with self.assertRaises(RuntimeError):
                line_rich_menu.install("https://school.example", "test-token", state_path)
            self.assertNotIn("/user/all/richmenu/new", [c.args[1] for c in request.call_args_list])
            self.assertEqual(json.loads(state_path.read_text())["new_menu_id"], "new")
