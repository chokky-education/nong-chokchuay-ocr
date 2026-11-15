import hashlib
import http.server
import io
import json
import os
import socketserver
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from PIL import Image

import auth_service
import database
import line_service
import storage_service
from app import create_app
from config import AppConfig


class FakeLineOCR:
    def __init__(self, text: str = "ใบเสร็จร้านเครื่องเขียนครูอนุบาล\nรวมทั้งสิ้น 250.00 บาท") -> None:
        self.text = text

    def read(self, _img):
        return {
            "text": self.text,
            "lines": [
                {"text": line, "confidence": 0.98, "row": idx, "box": [0, 0, 10, 10]}
                for idx, line in enumerate(self.text.splitlines())
            ],
            "rotation": 0,
            "text_angle": 0.0,
        }


def _make_png_bytes(color: tuple[int, int, int] = (255, 250, 240)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, format="PNG")
    return buf.getvalue()


class InMemoryR2Gateway:
    """In-memory R2 gateway mock implementing R2ClientProtocol with conditional ETag semantics."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.manifest_bytes: bytes | None = None
        self.manifest_etag: str = ""
        self.snapshots: dict[str, tuple[bytes, str]] = {}
        self.uploads: dict[str, tuple[bytes, str, str]] = {}
        self.get_manifest_override: Exception | None = None
        self.put_snapshot_override: Exception | None = None
        self.put_manifest_override: Exception | None = None
        self.put_upload_override: Exception | None = None
        self.get_upload_calls: list[str] = []
        self.delete_upload_calls: list[str] = []
        self.delete_snapshot_calls: list[str] = []
        self._etag_seq = 0

    def _next_etag(self) -> str:
        self._etag_seq += 1
        return f"etag-{self._etag_seq:04d}"

    def get_manifest(self):
        with self.lock:
            if self.get_manifest_override is not None:
                raise self.get_manifest_override
            if self.manifest_bytes is None:
                if self.snapshots or self.uploads:
                    raise storage_service.StorageUnavailableError(
                        "R2 bucket is non-empty but vault/manifest.json is missing",
                        error_code="manifest_missing_on_non_empty_bucket",
                    )
                return 404, None, ""
            return 200, json.loads(self.manifest_bytes.decode("utf-8")), self.manifest_etag

    def put_manifest(self, manifest, *, if_match=None, if_none_match_star=False):
        with self.lock:
            if self.put_manifest_override is not None:
                raise self.put_manifest_override
            if if_none_match_star and not if_match:
                if self.manifest_bytes is not None:
                    raise storage_service.StorageConflictError(
                        "Manifest already exists",
                        error_code="manifest_etag_conflict",
                    )
            elif if_match:
                if self.manifest_bytes is None or if_match != self.manifest_etag:
                    raise storage_service.StorageConflictError(
                        "Manifest ETag mismatch",
                        error_code="manifest_etag_conflict",
                    )
            else:
                raise storage_service.StorageCommitError(
                    "Missing manifest precondition",
                    error_code="missing_manifest_precondition",
                )
            self.manifest_bytes = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
            self.manifest_etag = self._next_etag()
            return self.manifest_etag

    def get_snapshot(self, snapshot_key, dest_path):
        with self.lock:
            if snapshot_key not in self.snapshots:
                raise storage_service.StorageUnavailableError(
                    f"Missing snapshot {snapshot_key}",
                    error_code="snapshot_not_found",
                )
            data, _sha = self.snapshots[snapshot_key]
        with open(dest_path, "wb") as fh:
            fh.write(data)

    def put_snapshot(self, snapshot_key, snapshot_path, *, sha256, revision, schema_version):
        _ = (revision, schema_version)
        with self.lock:
            if self.put_snapshot_override is not None:
                raise self.put_snapshot_override
        with open(snapshot_path, "rb") as fh:
            data = fh.read()
        if len(data) > storage_service.database.MAX_SNAPSHOT_BYTES:
            raise storage_service.StorageCommitError("Snapshot too large", error_code="snapshot_too_large")
        actual = hashlib.sha256(data).hexdigest()
        if actual != sha256:
            raise storage_service.StorageCommitError("Snapshot SHA-256 mismatch", error_code="sha256_mismatch")
        with self.lock:
            self.snapshots[snapshot_key] = (data, actual)

    def delete_snapshot(self, snapshot_key):
        with self.lock:
            self.delete_snapshot_calls.append(snapshot_key)
            self.snapshots.pop(snapshot_key, None)

    def get_upload(self, storage_key):
        with self.lock:
            self.get_upload_calls.append(storage_key)
            if storage_key not in self.uploads:
                raise FileNotFoundError(f"Upload not found: {storage_key}")
            data, sha256, _ctype = self.uploads[storage_key]
            return data, sha256

    def put_upload(self, storage_key, contents, *, sha256, content_type="application/octet-stream"):
        with self.lock:
            if self.put_upload_override is not None:
                raise self.put_upload_override
        actual = hashlib.sha256(contents).hexdigest()
        if actual != sha256:
            raise storage_service.StorageCommitError("Upload SHA-256 mismatch", error_code="sha256_mismatch")
        with self.lock:
            self.uploads[storage_key] = (bytes(contents), actual, content_type)

    def delete_upload(self, storage_key):
        with self.lock:
            self.delete_upload_calls.append(storage_key)
            self.uploads.pop(storage_key, None)


def _r2_config(
    base_dir: str,
    *,
    bootstrap: bool = True,
    retention: int = 5,
    auth_required: bool = False,
    allowed_emails: tuple[str, ...] = (),
    line_secret: str = "",
    line_token: str = "",
    allowed_line_users: tuple[str, ...] = (),
) -> AppConfig:
    return AppConfig.from_dir(
        base_dir,
        r2_gateway_url="http://r2.internal",
        r2_required=True,
        r2_bootstrap_if_empty=bootstrap,
        internal_sync_token="sync-token-secret",
        container_epoch="epoch-1",
        snapshot_retention_count=retention,
        auth_required=auth_required,
        allowed_emails=allowed_emails,
        google_client_id="cid" if auth_required else "",
        google_client_secret="csec" if auth_required else "",
        auth_session_secret="session-secret-0123456789abcdef" if auth_required else "",
        public_base_url="https://vault.example.com" if auth_required else "",
        line_channel_secret=line_secret,
        line_channel_access_token=line_token,
        allowed_line_user_ids=allowed_line_users,
        inbox_poll_seconds=0.0,
    )


class R2PersistenceTests(unittest.TestCase):
    def setUp(self):
        loading = mock.patch("line_service.start_line_loading", return_value=True)
        loading.start()
        self.addCleanup(loading.stop)
    def test_01_restart_recovery_preserves_db_fts_preview_sessions_and_lazy_files(self):
        r2 = InMemoryR2Gateway()
        png_bytes = _make_png_bytes((220, 235, 255))
        expected_sha = hashlib.sha256(png_bytes).hexdigest()

        with tempfile.TemporaryDirectory() as dir1:
            cfg1 = _r2_config(
                dir1,
                auth_required=True,
                allowed_emails=("teacher@school.th",),
            )
            app1 = create_app(config=cfg1, engine_factory=lambda: FakeLineOCR(), storage_client=r2)
            with TestClient(app1, base_url="https://vault.example.com") as client1:
                resp_obj = client1.get("/api/health")
                self.assertEqual(resp_obj.status_code, 200)
                self.assertEqual(resp_obj.json()["storage_state"], "READY")

                # Login via OAuth callback (mock Google token/userinfo) so auth_sessions row commits to R2
                with (
                    mock.patch.object(auth_service, "exchange_google_code", return_value={"access_token": "tok"}),
                    mock.patch.object(
                        auth_service,
                        "fetch_google_userinfo",
                        return_value={"email": "teacher@school.th", "email_verified": True, "name": "ครูพิ้งกี้"},
                    ),
                ):
                    client1.get("/api/auth/google/login", follow_redirects=False)
                    state = client1.cookies.get(auth_service.OAUTH_STATE_COOKIE_NAME)
                    payload = auth_service.verify_signed_payload(state, cfg1.auth_session_secret)
                    cb = client1.get(
                        "/api/auth/google/callback",
                        params={"code": "code-1", "state": payload["state"]},
                        follow_redirects=False,
                    )
                    self.assertEqual(cb.status_code, 302)

                csrf = client1.cookies.get(auth_service.CSRF_COOKIE_NAME)
                session_cookie = client1.cookies.get(auth_service.SESSION_COOKIE_NAME)
                headers = {"X-CSRF-Token": csrf, "Origin": "https://vault.example.com"}

                up = client1.post(
                    "/api/ocr",
                    files={"file": ("bill_kinder.png", png_bytes, "image/png")},
                    data={"save_to_db": "true", "category": "receipt", "tags": "สีเทียน,ครูอนุบาล", "auto_extract": "true"},
                    headers=headers,
                )
                self.assertEqual(up.status_code, 200, up.text)
                doc_id = up.json()["doc_id"]
                image_preview = up.json()["image_preview"]
                self.assertTrue(image_preview.startswith("data:image/jpeg;base64,"))

                rev = client1.post(
                    f"/api/documents/{doc_id}/review",
                    json={"review_status": "reviewed", "reimbursement_status": "reimbursed"},
                    headers=headers,
                )
                self.assertEqual(rev.status_code, 200)

                stu = client1.post(
                    "/api/students",
                    json={"full_name": "เด็กหญิงมะลิ ใจดี", "nickname": "มะลิ", "allergies": "ถั่วลิสง"},
                    headers=headers,
                )
                self.assertEqual(stu.status_code, 200)
                student_id = stu.json()["student"]["id"]

        # Simulate brand-new empty ephemeral container disk with only R2 intact
        with tempfile.TemporaryDirectory() as dir2:
            cfg2 = _r2_config(
                dir2,
                bootstrap=False,
                auth_required=True,
                allowed_emails=("teacher@school.th",),
            )
            cfg2.container_epoch = "epoch-2"
            app2 = create_app(config=cfg2, engine_factory=lambda: FakeLineOCR(), storage_client=r2)
            with TestClient(app2, base_url="https://vault.example.com") as client2:
                # Fresh container uploads dir must be empty before any download
                self.assertEqual(os.listdir(cfg2.uploads_dir), [])

                # Existing session cookie still authenticates across restart!
                client2.cookies.set(auth_service.SESSION_COOKIE_NAME, session_cookie)
                client2.cookies.set(auth_service.CSRF_COOKIE_NAME, csrf)

                me = client2.get("/api/auth/me")
                self.assertTrue(me.json()["authenticated"])

                # FTS5 Thai search works after restore & has_source_file is True even lazily
                docs = client2.get("/api/documents", params={"query": "ใบเสร็จร้านเครื่องเขียน"})
                self.assertEqual(docs.status_code, 200)
                items = docs.json()["documents"]
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["id"], doc_id)
                self.assertTrue(items[0]["has_source_file"])
                self.assertEqual(items[0]["review_status"], "reviewed")
                self.assertEqual(items[0]["ingestion_channel"], "web_upload")

                detail = client2.get(f"/api/documents/{doc_id}")
                doc_detail = detail.json()["document"]
                self.assertEqual(doc_detail["image_preview"], image_preview)
                self.assertEqual(doc_detail["expense"]["reimbursement_status"], "reimbursed")

                # Lazy file download restores from R2, verifies SHA-256, and caches locally
                dl = client2.get(f"/api/documents/{doc_id}/file")
                self.assertEqual(dl.status_code, 200)
                self.assertEqual(hashlib.sha256(dl.content).hexdigest(), expected_sha)
                self.assertEqual(len(r2.get_upload_calls), 1)
                self.assertEqual(len(os.listdir(cfg2.uploads_dir)), 1)

                # Second download hits local cache without hitting R2 again
                dl2 = client2.get(f"/api/documents/{doc_id}/file")
                self.assertEqual(dl2.status_code, 200)
                self.assertEqual(len(r2.get_upload_calls), 1)

                stus = client2.get("/api/students")
                self.assertEqual(len(stus.json()["students"]), 1)
                self.assertEqual(stus.json()["students"][0]["id"], student_id)
                self.assertEqual(stus.json()["students"][0]["allergies"], "ถั่วลิสง")

                # Logout revokes session server-side & persists revocation across another restart
                out = client2.post("/api/auth/logout", headers={"X-CSRF-Token": csrf, "Origin": "https://vault.example.com"})
                self.assertEqual(out.status_code, 200)

        with tempfile.TemporaryDirectory() as dir3:
            cfg3 = _r2_config(
                dir3,
                bootstrap=False,
                auth_required=True,
                allowed_emails=("teacher@school.th",),
            )
            app3 = create_app(config=cfg3, engine_factory=lambda: FakeLineOCR(), storage_client=r2)
            with TestClient(app3, base_url="https://vault.example.com") as client3:
                client3.cookies.set(auth_service.SESSION_COOKIE_NAME, session_cookie)
                self.assertFalse(client3.get("/api/auth/me").json()["authenticated"])

    def test_02_line_webhook_replies_only_after_commit_is_idempotent_and_honest_on_failure(self):
        r2 = InMemoryR2Gateway()
        png_bytes = _make_png_bytes()
        secret = "line-channel-secret-123"
        sent_replies: list[tuple[str, list]] = []

        def _make_signed_request(body_obj: dict) -> tuple[bytes, dict[str, str]]:
            raw = json.dumps(body_obj).encode("utf-8")
            sig = line_service.hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
            import base64

            return raw, {
                "X-Line-Signature": base64.b64encode(sig).decode("ascii"),
                "Content-Type": "application/json",
            }

        with tempfile.TemporaryDirectory() as d1:
            cfg = _r2_config(
                d1,
                line_secret=secret,
                line_token="line-access-tok",
                allowed_line_users=("Uteacher1",),
            )
            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR(), storage_client=r2)
            manifest_rev_when_replied: list[int] = []

            def _fake_reply(reply_token, messages, _token):
                _, manifest, _ = r2.get_manifest()
                manifest_rev_when_replied.append(manifest["revision"])
                sent_replies.append((reply_token, messages))
                return {"ok": True}

            webhook_body = {
                "events": [
                    {
                        "type": "message",
                        "webhookEventId": "evt-line-img-1",
                        "replyToken": "rt-1",
                        "source": {"type": "user", "userId": "Uteacher1"},
                        "message": {"type": "image", "id": "msg-img-1"},
                    }
                ]
            }
            raw_body, hdrs = _make_signed_request(webhook_body)

            with (
                TestClient(test_app) as client,
                mock.patch.object(line_service, "download_line_content", return_value=(png_bytes, "image/png")),
                mock.patch.object(line_service, "reply_line_messages", side_effect=_fake_reply),
            ):
                # 1) Inject R2 failure during manifest PUT -> HTTP 503, NO doc stored, honest error reply!
                r2.put_manifest_override = storage_service.StorageCommitError("R2 down", error_code="r2_down")
                fail_res = client.post("/api/line/webhook", content=raw_body, headers=hdrs)
                self.assertEqual(fail_res.status_code, 503)
                self.assertEqual(len(sent_replies), 1)
                self.assertIn("ขัดข้อง", sent_replies[0][1][0]["text"])
                self.assertEqual(client.get("/api/documents").json()["total"], 0)

                # 2) Clear failure -> LINE webhook succeeds AFTER R2 manifest committed
                r2.put_manifest_override = None
                sent_replies.clear()
                manifest_rev_when_replied.clear()
                ok_res = client.post("/api/line/webhook", content=raw_body, headers=hdrs)
                self.assertEqual(ok_res.status_code, 200)
                self.assertEqual(len(sent_replies), 1)
                self.assertEqual(manifest_rev_when_replied, [2])
                docs = client.get("/api/documents").json()["documents"]
                self.assertEqual(len(docs), 1)
                doc_id = docs[0]["id"]

                # 3) Duplicate webhook redelivery (same webhookEventId) does not duplicate docs
                dup_res = client.post("/api/line/webhook", content=raw_body, headers=hdrs)
                self.assertEqual(dup_res.status_code, 200)
                self.assertEqual(client.get("/api/documents").json()["total"], 1)

                # 4) Postback mark_reimbursed commits durably before reply
                pb_body, pb_hdrs = _make_signed_request(
                    {
                        "events": [
                            {
                                "type": "postback",
                                "webhookEventId": "evt-pb-1",
                                "replyToken": "rt-2",
                                "source": {"type": "user", "userId": "Uteacher1"},
                                "postback": {"data": f"action=mark_reimbursed&doc_id={doc_id}"},
                            }
                        ]
                    }
                )
                pb_res = client.post("/api/line/webhook", content=pb_body, headers=pb_hdrs)
                self.assertEqual(pb_res.status_code, 200)

                # 4) If storage is in ERROR state, text commands on LINE webhook also fail-closed with 503 (zero leaked data)
                test_app.state.storage.state = "ERROR"
                test_app.state.storage.error_code = "simulated_r2_down"
                txt_body, txt_hdrs = _make_signed_request(
                    {
                        "events": [
                            {
                                "type": "message",
                                "webhookEventId": "evt-txt-err-1",
                                "replyToken": "rt-txt-err",
                                "source": {"type": "user", "userId": "Uteacher1"},
                                "message": {"id": "m-txt-err", "type": "text", "text": "ยอดรอเบิก"},
                            }
                        ]
                    }
                )
                txt_err_res = client.post("/api/line/webhook", content=txt_body, headers=txt_hdrs)
                self.assertEqual(txt_err_res.status_code, 503)
                self.assertEqual(txt_err_res.json()["error"], "simulated_r2_down")
                self.assertNotIn("250.00", txt_err_res.text)
                test_app.state.storage.state = "READY"
                test_app.state.storage.error_code = ""

        with tempfile.TemporaryDirectory() as d2:
            cfg2 = _r2_config(d2, bootstrap=False)
            app2 = create_app(config=cfg2, engine_factory=lambda: FakeLineOCR(), storage_client=r2)
            with TestClient(app2) as client2:
                restored_doc = client2.get(f"/api/documents/{doc_id}").json()["document"]
                self.assertEqual(restored_doc["review_status"], "reviewed")
                self.assertEqual(restored_doc["expense"]["reimbursement_status"], "reimbursed")

    def test_03_watcher_keeps_file_on_r2_failure_and_commits_once_recovered(self):
        r2 = InMemoryR2Gateway()
        png_bytes = _make_png_bytes((200, 220, 240))

        with tempfile.TemporaryDirectory() as d:
            cfg = _r2_config(d)
            inbox_teacher = os.path.join(cfg.inbox_dir, "teacher")
            os.makedirs(inbox_teacher, exist_ok=True)
            inbox_file = os.path.join(inbox_teacher, "scan_bill.png")
            with open(inbox_file, "wb") as fh:
                fh.write(png_bytes)

            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR(), storage_client=r2)
            with TestClient(test_app) as client:
                # 1) Non-ambiguous R2 upload error -> file stays in inbox/, NOT inbox_failed/, DB empty
                r2.put_upload_override = storage_service.StorageCommitError("R2 upload error", error_code="r2_503")
                res1 = client.post("/api/watcher/scan")
                self.assertEqual(res1.status_code, 200)
                self.assertEqual(res1.json()["processed"], 0)
                self.assertEqual(res1.json()["failed"], 0)
                self.assertEqual(res1.json()["skipped"], 1)
                self.assertTrue(os.path.exists(inbox_file))
                self.assertEqual(os.listdir(cfg.inbox_failed_dir), [])
                self.assertEqual(client.get("/api/documents").json()["total"], 0)

                # 2) Ambiguous manifest PUT error AFTER manifest actually succeeded in R2:
                # reconcile resolves on next check and deterministic op_id prevents duplicate docs
                r2.put_upload_override = None
                real_put_manifest = r2.put_manifest

                def _ambiguous_put(manifest, **kwargs):
                    real_put_manifest(manifest, **kwargs)
                    r2.get_manifest_override = storage_service.StorageUnavailableError(
                        "temporary partition", error_code="r2_partition"
                    )
                    raise storage_service.StorageCommitError("timeout", error_code="timeout", ambiguous=True)

                with mock.patch.object(r2, "put_manifest", side_effect=_ambiguous_put):
                    res2 = client.post("/api/watcher/scan")
                self.assertEqual(res2.json()["skipped"], 1)
                self.assertTrue(os.path.exists(inbox_file))

                # R2 recovers -> retry reconciles dirty state and hits committed operation_id without duplicating doc
                r2.get_manifest_override = None
                res3 = client.post("/api/watcher/scan")
                self.assertEqual(res3.json()["processed"], 1)
                self.assertFalse(os.path.exists(inbox_file))
                self.assertEqual(client.get("/api/documents").json()["total"], 1)

    def test_04_mid_mutation_failure_rolls_back_local_db_and_upload(self):
        r2 = InMemoryR2Gateway()
        with tempfile.TemporaryDirectory() as d:
            cfg = _r2_config(d)
            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR(), storage_client=r2)
            with TestClient(test_app) as client:
                r2.put_snapshot_override = storage_service.StorageCommitError("Snapshot put failed", error_code="snap_err")
                res = client.post(
                    "/api/ocr",
                    files={"file": ("abort.png", _make_png_bytes(), "image/png")},
                    data={"save_to_db": "true"},
                )
                self.assertEqual(res.status_code, 503)
                self.assertEqual(res.json()["error"], "snap_err")
                self.assertEqual(os.listdir(cfg.uploads_dir), [])
                self.assertEqual(client.get("/api/documents").json()["total"], 0)

    def test_05_and_06_concurrent_mutations_serialize_and_etag_conflict_enters_error_state(self):
        r2 = InMemoryR2Gateway()
        with tempfile.TemporaryDirectory() as d:
            cfg = _r2_config(d)
            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR(), storage_client=r2)
            with TestClient(test_app) as client:
                errors: list[Exception] = []
                statuses: list[int] = []

                def _upload(idx: int):
                    try:
                        r = client.post(
                            "/api/ocr",
                            files={"file": (f"concurrent_{idx}.png", _make_png_bytes((idx * 30, 100, 120)), "image/png")},
                            data={"save_to_db": "true"},
                        )
                        statuses.append(r.status_code)
                    except Exception as exc:
                        errors.append(exc)

                t1 = threading.Thread(target=_upload, args=(1,))
                t2 = threading.Thread(target=_upload, args=(2,))
                t1.start()
                t2.start()
                t1.join()
                t2.join()
                self.assertEqual(errors, [])
                self.assertEqual(statuses, [200, 200])
                self.assertEqual(client.get("/api/documents").json()["total"], 2)
                self.assertEqual(test_app.state.storage.current_revision, 3)

                # External writer advances manifest ETag -> container hits 412 conflict -> ERROR state
                _, current_manifest, current_etag = r2.get_manifest()
                r2.put_manifest({**current_manifest, "revision": current_manifest["revision"] + 1}, if_match=current_etag)

                conflict_res = client.post(
                    "/api/students",
                    json={"full_name": "เด็กชายชนกัน"},
                )
                self.assertEqual(conflict_res.status_code, 503)
                self.assertEqual(conflict_res.json()["error"], "manifest_etag_conflict")
                self.assertEqual(test_app.state.storage.state, "ERROR")
                health = client.get("/api/health")
                self.assertEqual(health.status_code, 503)
                self.assertEqual(health.json()["storage_state"], "ERROR")
                self.assertNotIn("database", health.json())
                self.assertNotIn("path", health.json())
                # All data routes fail closed in ERROR state
                self.assertEqual(client.get("/api/documents").status_code, 503)

    def test_07_startup_gate_rejects_uninitialized_corrupt_future_schema_and_missing_manifest(self):
        # A) Empty bucket + bootstrap=False -> ERROR
        r2_empty = InMemoryR2Gateway()
        with tempfile.TemporaryDirectory() as d:
            app_empty = create_app(config=_r2_config(d, bootstrap=False), engine_factory=lambda: FakeLineOCR(), storage_client=r2_empty)
            with TestClient(app_empty) as client:
                self.assertEqual(app_empty.state.storage.state, "ERROR")
                self.assertEqual(app_empty.state.storage.error_code, "uninitialized_bucket")
                self.assertFalse(app_empty.state.watcher.get_status()["running"])
                self.assertEqual(client.get("/api/health").status_code, 503)
                self.assertEqual(client.get("/api/documents").status_code, 503)
                self.assertEqual(client.get("/").status_code, 200)

        # B) Corrupt snapshot bytes in R2 -> ERROR
        r2_corrupt = InMemoryR2Gateway()
        bad_bytes = b"not-a-sqlite-file"
        bad_sha = hashlib.sha256(bad_bytes).hexdigest()
        snap_key = "rev-00000001-abcdef012345.db"
        r2_corrupt.snapshots[snap_key] = (bad_bytes, bad_sha)
        r2_corrupt.put_manifest(
            {
                "schema_version": database.SUPPORTED_SCHEMA_VERSION,
                "revision": 1,
                "snapshot_key": snap_key,
                "snapshot_sha256": bad_sha,
                "snapshot_size": len(bad_bytes),
            },
            if_none_match_star=True,
        )
        with tempfile.TemporaryDirectory() as d:
            app_corrupt = create_app(config=_r2_config(d, bootstrap=False), engine_factory=lambda: FakeLineOCR(), storage_client=r2_corrupt)
            with TestClient(app_corrupt) as client:
                self.assertEqual(app_corrupt.state.storage.state, "ERROR")
                self.assertEqual(client.get("/api/health").status_code, 503)

        # C) Future schema version (SQLite user_version=99) -> ERROR
        r2_future = InMemoryR2Gateway()
        with tempfile.TemporaryDirectory() as d_tmp:
            future_db = os.path.join(d_tmp, "future.db")
            conn = sqlite3.connect(future_db)
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
            conn.execute("PRAGMA user_version = 99")
            conn.commit()
            conn.close()
            with open(future_db, "rb") as fh:
                f_bytes = fh.read()
            f_sha = hashlib.sha256(f_bytes).hexdigest()
            f_key = "rev-00000002-0123456789ab.db"
            r2_future.snapshots[f_key] = (f_bytes, f_sha)
            r2_future.put_manifest(
                {
                    "schema_version": database.SUPPORTED_SCHEMA_VERSION,
                    "revision": 2,
                    "snapshot_key": f_key,
                    "snapshot_sha256": f_sha,
                    "snapshot_size": len(f_bytes),
                },
                if_none_match_star=True,
            )
            app_future = create_app(config=_r2_config(d_tmp, bootstrap=False), engine_factory=lambda: FakeLineOCR(), storage_client=r2_future)
            with TestClient(app_future):
                self.assertEqual(app_future.state.storage.state, "ERROR")

        # D) Oversized snapshot metadata (> MAX_SNAPSHOT_BYTES) -> ERROR
        r2_oversized = InMemoryR2Gateway()
        ok_key = "rev-00000003-222222222222.db"
        r2_oversized.snapshots[ok_key] = (b"abc", hashlib.sha256(b"abc").hexdigest())
        r2_oversized.put_manifest(
            {
                "schema_version": database.SUPPORTED_SCHEMA_VERSION,
                "revision": 3,
                "snapshot_key": ok_key,
                "snapshot_sha256": hashlib.sha256(b"abc").hexdigest(),
                "snapshot_size": database.MAX_SNAPSHOT_BYTES + 1,
            },
            if_none_match_star=True,
        )
        with tempfile.TemporaryDirectory() as d:
            app_over = create_app(config=_r2_config(d, bootstrap=False), engine_factory=lambda: FakeLineOCR(), storage_client=r2_oversized)
            with TestClient(app_over):
                self.assertEqual(app_over.state.storage.state, "ERROR")

        # E) Non-empty bucket missing manifest -> ERROR (never treats as empty)
        r2_missing_manifest = InMemoryR2Gateway()
        r2_missing_manifest.snapshots["rev-00000001-111111111111.db"] = (b"123", hashlib.sha256(b"123").hexdigest())
        with tempfile.TemporaryDirectory() as d:
            app_mm = create_app(config=_r2_config(d, bootstrap=True), engine_factory=lambda: FakeLineOCR(), storage_client=r2_missing_manifest)
            with TestClient(app_mm):
                self.assertEqual(app_mm.state.storage.state, "ERROR")
                self.assertEqual(app_mm.state.storage.error_code, "manifest_missing_on_non_empty_bucket")

    def test_09_and_10_security_auth_404_traversal_and_checksum_verification(self):
        r2 = InMemoryR2Gateway()
        with tempfile.TemporaryDirectory() as d:
            cfg = _r2_config(d, auth_required=True, allowed_emails=("teacher@school.th",))
            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR(), storage_client=r2)
            with TestClient(test_app, base_url="https://vault.example.com") as client:
                with (
                    mock.patch.object(auth_service, "exchange_google_code", return_value={"access_token": "tok"}),
                    mock.patch.object(
                        auth_service,
                        "fetch_google_userinfo",
                        return_value={"email": "teacher@school.th", "email_verified": True, "name": "ครูพิ้งกี้"},
                    ),
                ):
                    client.get("/api/auth/google/login", follow_redirects=False)
                    state = auth_service.verify_signed_payload(
                        client.cookies.get(auth_service.OAUTH_STATE_COOKIE_NAME), cfg.auth_session_secret
                    )["state"]
                    client.get("/api/auth/google/callback", params={"code": "c", "state": state}, follow_redirects=False)

                csrf = client.cookies.get(auth_service.CSRF_COOKIE_NAME)
                up = client.post(
                    "/api/ocr",
                    files={"file": ("doc.png", _make_png_bytes(), "image/png")},
                    headers={"X-CSRF-Token": csrf, "Origin": "https://vault.example.com"},
                )
                doc_id = up.json()["doc_id"]

                # Unauthenticated download rejected 401 BEFORE any R2 upload fetch
                unauth = TestClient(test_app, base_url="https://vault.example.com")
                self.assertEqual(unauth.get(f"/api/documents/{doc_id}/file").status_code, 401)
                self.assertEqual(r2.get_upload_calls, [])

                # Authenticated missing doc -> 404 BEFORE any R2 upload fetch
                self.assertEqual(client.get("/api/documents/nonexistent-id/file").status_code, 404)
                self.assertEqual(r2.get_upload_calls, [])

                # Path traversal source_storage_key rejected
                with self.assertRaises(ValueError):
                    storage_service.validate_upload_key("../../etc/passwd")

                # Tampered R2 bytes vs DB SHA-256 detected cleanly on lazy restore
                doc = database.get_document_by_id(doc_id, db_path=cfg.db_path, r2_enabled=True)
                skey = doc["source_storage_key"]
                os.remove(os.path.join(cfg.uploads_dir, skey))
                tampered = b"tampered-bytes"
                r2.uploads[skey] = (tampered, hashlib.sha256(tampered).hexdigest(), "image/png")
                bad_dl = client.get(f"/api/documents/{doc_id}/file")
                self.assertEqual(bad_dl.status_code, 503)
                self.assertEqual(bad_dl.json()["error"], "source_file_checksum_mismatch")

    def test_11_deletion_defers_r2_upload_purge_until_outside_retention_window(self):
        r2 = InMemoryR2Gateway()
        png_bytes = _make_png_bytes((50, 150, 200))

        with tempfile.TemporaryDirectory() as d:
            cfg = _r2_config(d, retention=2)
            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR(), storage_client=r2)
            with TestClient(test_app) as client:
                up = client.post("/api/ocr", files={"file": ("to_delete.png", png_bytes, "image/png")})
                doc_id = up.json()["doc_id"]
                skey = database.get_document_by_id(doc_id, db_path=cfg.db_path, r2_enabled=True)["source_storage_key"]
                _, pre_delete_manifest, _ = r2.get_manifest()
                pre_delete_rev = pre_delete_manifest["revision"]

                # Delete doc in rev N: DB row removed immediately, R2 upload still preserved because pre-delete snapshot is retained!
                del_res = client.delete(f"/api/documents/{doc_id}")
                self.assertTrue(del_res.json()["success"])
                self.assertEqual(client.get("/api/documents").json()["total"], 0)
                self.assertIn(skey, r2.uploads)
                self.assertEqual(r2.delete_upload_calls, [])

                # Create 2 more mutations so pre_delete_rev falls outside retention=2 window
                client.post("/api/students", json={"full_name": "เด็กชายหนึ่ง"})
                client.post("/api/students", json={"full_name": "เด็กชายสอง"})

                _, latest_manifest, _ = r2.get_manifest()
                retained_revs = [item["revision"] for item in latest_manifest["retained_snapshots"]]
                self.assertNotIn(pre_delete_rev, retained_revs)
                self.assertIn(skey, r2.delete_upload_calls)
                self.assertNotIn(skey, r2.uploads)
                self.assertEqual(database.list_pending_r2_deletions(max_revision=999999, db_path=cfg.db_path), [])

    def test_12_local_mode_and_http_r2_gateway_client_wire_roundtrip(self):
        # A) Local mode works with zero R2 config
        with tempfile.TemporaryDirectory() as d_local:
            local_cfg = AppConfig.from_dir(d_local, inbox_poll_seconds=0.0)
            self.assertFalse(local_cfg.is_r2_enabled())
            local_app = create_app(config=local_cfg, engine_factory=lambda: FakeLineOCR())
            with TestClient(local_app) as client:
                self.assertEqual(client.get("/api/health").json()["storage_state"], "READY")
                res = client.post("/api/ocr", files={"file": ("local.png", _make_png_bytes(), "image/png")})
                self.assertEqual(res.status_code, 200)
                self.assertTrue(res.json()["has_source_file"])

        # B) Loopback HTTP server exercising R2GatewayClient HTTP transport headers/ETags
        backend = InMemoryR2Gateway()
        expected_token = "tok-loopback"
        expected_epoch = "epoch-loopback"

        class _GatewayHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def _check_auth(self) -> bool:
                if (
                    self.headers.get("X-Vault-Internal-Token") != expected_token
                    or self.headers.get("X-Vault-Container-Epoch") != expected_epoch
                ):
                    self.send_response(403)
                    self.end_headers()
                    return False
                return True

            def do_GET(self):
                if not self._check_auth():
                    return
                if self.path == "/manifest":
                    status, manifest, etag = backend.get_manifest()
                    if status == 404:
                        self.send_response(404)
                        self.end_headers()
                        return
                    body = json.dumps(manifest).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("X-R2-Etag", etag)
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path.startswith("/snapshots/"):
                    skey = self.path.split("/snapshots/", 1)[1]
                    data, sha = backend.snapshots[skey]
                    self.send_response(200)
                    self.send_header("X-Content-Sha256", sha)
                    self.end_headers()
                    self.wfile.write(data)
                elif self.path.startswith("/uploads/"):
                    ukey = self.path.split("/uploads/", 1)[1]
                    data, sha = backend.get_upload(ukey)
                    self.send_response(200)
                    self.send_header("X-Content-Sha256", sha)
                    self.end_headers()
                    self.wfile.write(data)

            def do_PUT(self):
                if not self._check_auth():
                    return
                length = int(self.headers.get("Content-Length") or "0")
                raw = self.rfile.read(length)
                if self.path == "/manifest":
                    if_match = self.headers.get("If-Match")
                    if_none = self.headers.get("If-None-Match") == "*"
                    try:
                        new_etag = backend.put_manifest(
                            json.loads(raw.decode("utf-8")),
                            if_match=if_match,
                            if_none_match_star=if_none,
                        )
                    except storage_service.StorageConflictError:
                        self.send_response(412)
                        self.end_headers()
                        return
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": True, "etag": new_etag}).encode("utf-8"))
                elif self.path.startswith("/snapshots/"):
                    skey = self.path.split("/snapshots/", 1)[1]
                    sha = self.headers.get("X-Content-Sha256")
                    with backend.lock:
                        backend.snapshots[skey] = (raw, sha)
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"ok":true}')
                elif self.path.startswith("/uploads/"):
                    ukey = self.path.split("/uploads/", 1)[1]
                    backend.put_upload(
                        ukey,
                        raw,
                        sha256=self.headers.get("X-Content-Sha256"),
                        content_type=self.headers.get("Content-Type") or "application/octet-stream",
                    )
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"ok":true}')

            def do_DELETE(self):
                if not self._check_auth():
                    return
                if self.path.startswith("/snapshots/"):
                    backend.delete_snapshot(self.path.split("/snapshots/", 1)[1])
                elif self.path.startswith("/uploads/"):
                    backend.delete_upload(self.path.split("/uploads/", 1)[1])
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

        class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True

        with _Server(("127.0.0.1", 0), _GatewayHandler) as srv:
            thread = threading.Thread(target=srv.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{srv.server_port}"
                with tempfile.TemporaryDirectory() as d_http:
                    cfg_http = AppConfig.from_dir(
                        d_http,
                        r2_gateway_url=base_url,
                        r2_required=True,
                        r2_bootstrap_if_empty=True,
                        internal_sync_token=expected_token,
                        container_epoch=expected_epoch,
                        inbox_poll_seconds=0.0,
                    )
                    app_http = create_app(config=cfg_http, engine_factory=lambda: FakeLineOCR())
                    with TestClient(app_http) as client_http:
                        res = client_http.post(
                            "/api/ocr",
                            files={"file": ("wire.png", _make_png_bytes(), "image/png")},
                        )
                        self.assertEqual(res.status_code, 200)
                        doc_id = res.json()["doc_id"]
                        dl = client_http.get(f"/api/documents/{doc_id}/file")
                        self.assertEqual(dl.status_code, 200)
                        self.assertEqual(dl.content, _make_png_bytes())
            finally:
                srv.shutdown()

    def test_13_s1_reconcile_dirty_state_before_reading_auth_sessions(self):
        r2 = InMemoryR2Gateway()
        with tempfile.TemporaryDirectory() as d:
            cfg = _r2_config(d, auth_required=True, allowed_emails=("teacher@school.th",))
            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR(), storage_client=r2)
            with TestClient(test_app, base_url="https://vault.example.com") as client:
                with (
                    mock.patch.object(auth_service, "exchange_google_code", return_value={"access_token": "tok"}),
                    mock.patch.object(
                        auth_service,
                        "fetch_google_userinfo",
                        return_value={"email": "teacher@school.th", "email_verified": True, "name": "ครูพิ้งกี้"},
                    ),
                ):
                    client.get("/api/auth/google/login", follow_redirects=False)
                    state = auth_service.verify_signed_payload(
                        client.cookies.get(auth_service.OAUTH_STATE_COOKIE_NAME), cfg.auth_session_secret
                    )["state"]
                    client.get("/api/auth/google/callback", params={"code": "c1", "state": state}, follow_redirects=False)

                csrf = client.cookies.get(auth_service.CSRF_COOKIE_NAME)
                headers = {"X-CSRF-Token": csrf, "Origin": "https://vault.example.com"}

                # Ambiguous logout failure + unreachable get_manifest leaves storage READY with dirty uncommitted revocation
                r2.put_manifest_override = storage_service.StorageCommitError(
                    "timeout during logout", error_code="manifest_timeout", ambiguous=True
                )
                r2.get_manifest_override = storage_service.StorageUnavailableError("r2 down during reconcile")
                logout1 = client.post("/api/auth/logout", headers=headers)
                self.assertEqual(logout1.status_code, 503)
                self.assertEqual(test_app.state.storage.state, "READY")
                self.assertTrue(test_app.state.storage._dirty_uncommitted)

                # While R2 is still unreachable, auth endpoints must return 503 (never 401 or authenticated=false)
                me_down = client.get("/api/auth/me")
                self.assertEqual(me_down.status_code, 503)
                docs_down = client.get("/api/documents")
                self.assertEqual(docs_down.status_code, 503)

                # Once R2 recovers (manifest still at login revision with active session), /api/auth/me reconciles first!
                r2.put_manifest_override = None
                r2.get_manifest_override = None
                me_ok = client.get("/api/auth/me")
                self.assertEqual(me_ok.status_code, 200)
                self.assertTrue(me_ok.json()["authenticated"])
                self.assertFalse(test_app.state.storage._dirty_uncommitted)

                # Retrying logout succeeds 200 (never 401)
                logout2 = client.post("/api/auth/logout", headers=headers)
                self.assertEqual(logout2.status_code, 200)
                self.assertTrue(logout2.json()["success"])
                me_after = client.get("/api/auth/me")
                self.assertEqual(me_after.status_code, 200)
                self.assertFalse(me_after.json()["authenticated"])

    def test_14_s2_bootstrap_refuses_to_overwrite_non_empty_local_sqlite(self):
        r2 = InMemoryR2Gateway()
        with tempfile.TemporaryDirectory() as d:
            cfg = _r2_config(d, bootstrap=True)
            database.init_db(cfg.db_path)
            seeded = database.upsert_student(
                full_name="เด็กหญิงต้นข้าว ใจดี",
                nickname="ต้นข้าว",
                allergies="แพ้กุ้ง",
                db_path=cfg.db_path,
            )
            storage = storage_service.VaultStorageCoordinator(cfg, client=r2)
            storage.startup()

            self.assertEqual(storage.state, "ERROR")
            self.assertEqual(storage.error_code, "bootstrap_refused_non_empty_local_db")
            self.assertIsNone(r2.manifest_bytes)
            students_after = database.list_students(db_path=cfg.db_path)["students"]
            self.assertEqual(len(students_after), 1)
            self.assertEqual(students_after[0]["id"], seeded["id"])

    def test_15_f1_never_rollback_local_db_after_put_manifest_commits(self):
        r2 = InMemoryR2Gateway()
        with tempfile.TemporaryDirectory() as d:
            cfg = _r2_config(d, bootstrap=True)
            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR(), storage_client=r2)
            with TestClient(test_app) as client:
                storage = test_app.state.storage
                real_atomic_copy = storage_service._atomic_copy_file

                def _fail_committed_copy_once(src_path, dst_path):
                    if dst_path == storage.committed_db_path:
                        raise OSError("simulated disk error copying committed snapshot")
                    return real_atomic_copy(src_path, dst_path)

                with mock.patch.object(storage_service, "_atomic_copy_file", side_effect=_fail_committed_copy_once):
                    stu1 = client.post(
                        "/api/students",
                        json={"full_name": "เด็กชายสอง ปลอดภัย", "nickname": "สอง"},
                    )
                self.assertEqual(stu1.status_code, 503)
                self.assertEqual(stu1.json()["error"], "post_commit_sync_failed")
                self.assertEqual(storage.state, "READY")
                self.assertTrue(storage._dirty_uncommitted)

                # Next write reconciles cleanly to rev 2 and commits rev 3 without manifest_etag_conflict or ERROR state
                stu2 = client.post(
                    "/api/students",
                    json={"full_name": "เด็กหญิงสาม ครบถ้วน", "nickname": "สาม"},
                )
                self.assertEqual(stu2.status_code, 200, stu2.text)
                self.assertEqual(storage.state, "READY")
                self.assertEqual(storage.current_revision, 3)

                all_students = client.get("/api/students").json()["students"]
                names = {s["full_name"] for s in all_students}
                self.assertEqual(names, {"เด็กชายสอง ปลอดภัย", "เด็กหญิงสาม ครบถ้วน"})

    def test_16_f2_web_ocr_idempotency_key_retry_and_payload_mismatch_409(self):
        r2 = InMemoryR2Gateway()
        png_a = _make_png_bytes((20, 40, 80))
        png_b = _make_png_bytes((200, 30, 30))

        with tempfile.TemporaryDirectory() as d:
            cfg = _r2_config(d, bootstrap=True)
            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR(), storage_client=r2)
            with TestClient(test_app) as client:
                real_put_manifest = r2.put_manifest

                def _commit_then_ambiguous_timeout(manifest, *, if_match=None, if_none_match_star=False):
                    real_put_manifest(manifest, if_match=if_match, if_none_match_star=if_none_match_star)
                    raise storage_service.StorageCommitError(
                        "HTTP timeout after remote commit",
                        error_code="manifest_commit_timeout",
                        ambiguous=True,
                    )

                # 1) Upload commits on R2, then ambiguous timeout occurs & get_manifest fails temporarily
                with mock.patch.object(r2, "put_manifest", side_effect=_commit_then_ambiguous_timeout):
                    r2.get_manifest_override = storage_service.StorageUnavailableError("temporary network glitch")
                    first = client.post(
                        "/api/ocr",
                        files={"file": ("bill_retry.png", png_a, "image/png")},
                        data={"category": "receipt", "auto_extract": "true"},
                        headers={"Idempotency-Key": "web-retry-001"},
                    )
                self.assertEqual(first.status_code, 503)

                # 2) Retry identical upload + Idempotency-Key -> reconciles and returns original doc without duplicating
                r2.get_manifest_override = None
                second = client.post(
                    "/api/ocr",
                    files={"file": ("bill_retry.png", png_a, "image/png")},
                    data={"category": "receipt", "auto_extract": "true"},
                    headers={"Idempotency-Key": "web-retry-001"},
                )
                self.assertEqual(second.status_code, 200, second.text)
                first_doc_id = second.json()["doc_id"]

                # Third replay also returns same doc_id and keeps document count = 1
                third = client.post(
                    "/api/ocr",
                    files={"file": ("bill_retry.png", png_a, "image/png")},
                    data={"category": "receipt", "auto_extract": "true"},
                    headers={"Idempotency-Key": "web-retry-001"},
                )
                self.assertEqual(third.status_code, 200)
                self.assertEqual(third.json()["doc_id"], first_doc_id)
                self.assertEqual(client.get("/api/documents").json()["total"], 1)

                # 3) Reusing same Idempotency-Key with a DIFFERENT file must fail with 409 Conflict
                mismatch = client.post(
                    "/api/ocr",
                    files={"file": ("other_bill.png", png_b, "image/png")},
                    data={"category": "receipt", "auto_extract": "true"},
                    headers={"Idempotency-Key": "web-retry-001"},
                )
                self.assertEqual(mismatch.status_code, 409)
                self.assertEqual(mismatch.json()["error"], "idempotency_payload_mismatch")

    def test_17_f3_watcher_reimport_identical_file_after_document_deletion(self):
        r2 = InMemoryR2Gateway()
        png_bytes = _make_png_bytes((90, 120, 40))

        with tempfile.TemporaryDirectory() as d:
            cfg = _r2_config(d, bootstrap=True)
            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR(), storage_client=r2)
            with TestClient(test_app) as client:
                inbox_file = os.path.join(cfg.inbox_dir, "receipt.png")
                with open(inbox_file, "wb") as fh:
                    fh.write(png_bytes)

                scan1 = test_app.state.watcher.scan_once(require_stability=False)
                self.assertEqual(scan1["processed"], 1)
                doc_id_1 = scan1["doc_ids"][0]
                self.assertFalse(os.path.exists(inbox_file))
                self.assertFalse(os.path.exists(f"{inbox_file}.opid"))

                # User deletes the first document from the Vault
                del_res = client.delete(f"/api/documents/{doc_id_1}")
                self.assertTrue(del_res.json()["success"])
                self.assertEqual(client.get("/api/documents").json()["total"], 0)

                # User intentionally drops the identical file into inbox/ again -> must create a new document!
                with open(inbox_file, "wb") as fh:
                    fh.write(png_bytes)

                scan2 = test_app.state.watcher.scan_once(require_stability=False)
                self.assertEqual(scan2["processed"], 1)
                doc_id_2 = scan2["doc_ids"][0]
                self.assertNotEqual(doc_id_2, doc_id_1)
                self.assertFalse(os.path.exists(inbox_file))
                self.assertFalse(os.path.exists(f"{inbox_file}.opid"))
                docs = client.get("/api/documents").json()["documents"]
                self.assertEqual(len(docs), 1)
                self.assertEqual(docs[0]["id"], doc_id_2)

    def test_18_s1r_concurrent_session_read_waits_for_in_flight_aborted_logout_without_false_401(self):
        import time
        from unittest import mock
        import auth_service

        r2 = InMemoryR2Gateway()
        with tempfile.TemporaryDirectory() as d:
            cfg = _r2_config(d)
            cfg.auth_required = True
            cfg.auth_session_secret = "test-secret-for-s1r"
            cfg.allowed_emails = ("teacher@school.ac.th",)
            cfg.public_base_url = "http://127.0.0.1:8765"
            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR(), storage_client=r2)
            with TestClient(test_app) as client:
                sid = "sess-s1r-concurrent"
                exp_ts = int(time.time()) + 3600
                test_app.state.storage.execute_mutation(
                    f"test:login:{sid}",
                    "create_auth_session",
                    lambda _rev: database.create_auth_session(
                        session_id=sid,
                        email="teacher@school.ac.th",
                        name="Teacher",
                        picture="",
                        csrf_token="csrf-s1r",
                        expires_at="2099-01-01T00:00:00Z",
                        db_path=cfg.db_path,
                    ),
                )
                cookie_val = auth_service.sign_payload(
                    {"sid": sid, "email": "teacher@school.ac.th", "exp": exp_ts},
                    cfg.auth_session_secret,
                )

                storage = test_app.state.storage
                orig_put_manifest = r2.put_manifest
                orig_require_ready = storage.require_ready
                orig_get_session = database.get_active_auth_session

                def _run_readiness_before_logout_race(
                    endpoint_path: str,
                    *,
                    wait_for_put_timeout: float = 0.15,
                ) -> dict:
                    reader_worker_tid = {"id": None}
                    reader_passed_readiness = threading.Event()
                    logout_dispatched = threading.Event()
                    logout_in_manifest_put = threading.Event()
                    allow_logout_abort = threading.Event()
                    saw_logout_in_manifest_during_read = {"value": False}
                    logout_intercepted = {"done": False}
                    results: dict[str, object] = {}
                    errors: list[Exception] = []

                    def _hooked_require_ready():
                        res = orig_require_ready()
                        # Capture the first worker entering readiness (always the reader before logout starts).
                        if reader_worker_tid["id"] is None:
                            reader_worker_tid["id"] = threading.get_ident()
                            reader_passed_readiness.set()
                        return res

                    def _hooked_get_active_auth_session(session_id: str, db_path: str = ""):
                        if threading.get_ident() == reader_worker_tid["id"]:
                            self.assertTrue(logout_dispatched.wait(timeout=5.0))
                            # Give an unlocked concurrent logout time to mutate SQLite and reach R2 put_manifest.
                            saw_logout_in_manifest_during_read["value"] = logout_in_manifest_put.wait(
                                timeout=wait_for_put_timeout
                            )
                        return orig_get_session(session_id, db_path=db_path)

                    def _stalling_failed_put_manifest(manifest, *, if_match=None, if_none_match_star=False):
                        if not logout_intercepted["done"]:
                            logout_intercepted["done"] = True
                            logout_in_manifest_put.set()
                            allow_logout_abort.wait(timeout=5.0)
                            raise storage_service.StorageCommitError(
                                "Simulated transient R2 manifest failure",
                                error_code="manifest_network_err",
                            )
                        return orig_put_manifest(
                            manifest,
                            if_match=if_match,
                            if_none_match_star=if_none_match_star,
                        )

                    def _run_reader():
                        try:
                            results["reader"] = client.get(
                                endpoint_path,
                                headers={"cookie": f"{auth_service.SESSION_COOKIE_NAME}={cookie_val}"},
                            )
                        except Exception as exc:
                            errors.append(exc)

                    def _run_logout():
                        try:
                            logout_dispatched.set()
                            results["logout"] = client.post(
                                "/api/auth/logout",
                                headers={
                                    "cookie": f"{auth_service.SESSION_COOKIE_NAME}={cookie_val}",
                                    "origin": "http://127.0.0.1:8765",
                                    "x-csrf-token": "csrf-s1r",
                                },
                            )
                        except Exception as exc:
                            errors.append(exc)

                    r2.put_manifest = _stalling_failed_put_manifest
                    t_reader = threading.Thread(target=_run_reader)
                    t_logout = threading.Thread(target=_run_logout)
                    try:
                        with mock.patch.object(storage, "require_ready", side_effect=_hooked_require_ready), \
                             mock.patch("auth_service.database.get_active_auth_session", side_effect=_hooked_get_active_auth_session):
                            t_reader.start()
                            self.assertTrue(reader_passed_readiness.wait(timeout=5.0))
                            t_logout.start()
                            t_reader.join(timeout=5.0)
                            self.assertTrue(logout_in_manifest_put.wait(timeout=5.0))
                            allow_logout_abort.set()
                            t_logout.join(timeout=5.0)
                    finally:
                        allow_logout_abort.set()
                        r2.put_manifest = orig_put_manifest

                    self.assertEqual(errors, [])
                    return {
                        "reader": results["reader"],
                        "logout": results["logout"],
                        "logout_reached_put_during_read": saw_logout_in_manifest_during_read["value"],
                    }

                # 1) Production execute_read (locked): logout cannot interleave after readiness on either route.
                docs_race = _run_readiness_before_logout_race("/api/documents")
                self.assertFalse(docs_race["logout_reached_put_during_read"])
                self.assertEqual(docs_race["reader"].status_code, 200)
                self.assertEqual(docs_race["logout"].status_code, 503)

                me_race = _run_readiness_before_logout_race("/api/auth/me")
                self.assertFalse(me_race["logout_reached_put_during_read"])
                self.assertEqual(me_race["reader"].status_code, 200)
                self.assertTrue(me_race["reader"].json()["authenticated"])
                self.assertEqual(me_race["logout"].status_code, 503)

                # 2) Negative control: without _mutation_lock around execute_read, this exact sequence triggers the old race!
                def _unlocked_execute_read(read_fn):
                    storage.require_ready()
                    return read_fn()

                with mock.patch.object(storage, "execute_read", side_effect=_unlocked_execute_read):
                    unlocked_docs_race = _run_readiness_before_logout_race(
                        "/api/documents", wait_for_put_timeout=2.0
                    )
                    self.assertTrue(unlocked_docs_race["logout_reached_put_during_read"])
                    self.assertEqual(unlocked_docs_race["reader"].status_code, 401)
                    self.assertEqual(unlocked_docs_race["logout"].status_code, 503)

                    unlocked_me_race = _run_readiness_before_logout_race(
                        "/api/auth/me", wait_for_put_timeout=2.0
                    )
                    self.assertTrue(unlocked_me_race["logout_reached_put_during_read"])
                    self.assertEqual(unlocked_me_race["reader"].status_code, 200)
                    self.assertFalse(unlocked_me_race["reader"].json()["authenticated"])
                    self.assertEqual(unlocked_me_race["logout"].status_code, 503)

    def test_19_f3r_watcher_defers_when_opid_sidecar_write_or_read_fails_and_never_duplicates(self):
        import builtins
        from unittest import mock
        import ocr_service

        r2 = InMemoryR2Gateway()
        with tempfile.TemporaryDirectory() as d:
            cfg = _r2_config(d)
            png_bytes = _make_png_bytes((22, 44, 66))
            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR(), storage_client=r2)
            with TestClient(test_app) as client:
                spaced_dir = os.path.join(cfg.inbox_dir, "ครู อนุบาล 1")
                os.makedirs(spaced_dir, exist_ok=True)
                inbox_file = os.path.join(spaced_dir, "บิล สีเทียน f3r.png")
                with open(inbox_file, "wb") as fh:
                    fh.write(png_bytes)

                with mock.patch("watcher.os.replace", side_effect=OSError("disk full")), \
                     mock.patch("watcher.ocr_service.ingest_document", wraps=ocr_service.ingest_document) as ingest_spy:
                    scan_write_err = test_app.state.watcher.scan_once(require_stability=False)

                self.assertEqual(scan_write_err["processed"], 0)
                self.assertEqual(scan_write_err["failed"], 0)
                self.assertEqual(scan_write_err["skipped"], 1)
                self.assertEqual(ingest_spy.call_count, 0)
                self.assertTrue(os.path.exists(inbox_file))
                self.assertEqual(client.get("/api/documents").json()["total"], 0)

                # Once sidecar persists cleanly, simulate ambiguous R2 response (committed in R2, error returned).
                orig_put_manifest = r2.put_manifest
                orig_get_manifest = r2.get_manifest

                def _run_ambiguous_scan_once() -> dict:
                    ambiguous_calls = {"put": 0, "get": 0}

                    def _commit_then_drop_ack(manifest, *, if_match=None, if_none_match_star=False):
                        res = orig_put_manifest(
                            manifest,
                            if_match=if_match,
                            if_none_match_star=if_none_match_star,
                        )
                        if ambiguous_calls["put"] == 0:
                            ambiguous_calls["put"] += 1
                            raise storage_service.StorageCommitError(
                                "Response lost after R2 commit",
                                error_code="manifest_ack_lost",
                                ambiguous=True,
                            )
                        return res

                    def _fail_reconcile_get_once():
                        if ambiguous_calls["put"] == 1 and ambiguous_calls["get"] == 0:
                            ambiguous_calls["get"] += 1
                            raise storage_service.StorageUnavailableError(
                                "Transient R2 outage during reconcile",
                                error_code="r2_temp_down",
                            )
                        return orig_get_manifest()

                    r2.put_manifest = _commit_then_drop_ack
                    r2.get_manifest = _fail_reconcile_get_once
                    try:
                        return test_app.state.watcher.scan_once(require_stability=False)
                    finally:
                        r2.put_manifest = orig_put_manifest
                        r2.get_manifest = orig_get_manifest

                scan_ambiguous = _run_ambiguous_scan_once()
                self.assertEqual(scan_ambiguous["processed"], 0)
                self.assertEqual(scan_ambiguous["skipped"], 1)
                self.assertTrue(os.path.exists(inbox_file))
                self.assertTrue(os.path.exists(f"{inbox_file}.opid"))
                with open(f"{inbox_file}.opid", "r", encoding="utf-8") as fh:
                    persisted_sidecar = fh.read().strip()
                self.assertFalse(any(ch.isspace() for ch in persisted_sidecar))

                # Reading existing .opid fails -> watcher must defer before ingesting instead of making a new ID.
                orig_open = builtins.open

                def _fail_opid_read(file, mode="r", *args, **kwargs):
                    if isinstance(file, str) and file.endswith(".opid") and "r" in mode:
                        raise PermissionError("opid unreadable")
                    return orig_open(file, mode, *args, **kwargs)

                with mock.patch("builtins.open", side_effect=_fail_opid_read), \
                     mock.patch("watcher.ocr_service.ingest_document", wraps=ocr_service.ingest_document) as ingest_spy_2:
                    scan_read_err = test_app.state.watcher.scan_once(require_stability=False)

                self.assertEqual(scan_read_err["processed"], 0)
                self.assertEqual(scan_read_err["failed"], 0)
                self.assertEqual(scan_read_err["skipped"], 1)
                self.assertEqual(ingest_spy_2.call_count, 0)
                self.assertTrue(os.path.exists(inbox_file))

                # Retry after I/O recovers -> reconciles + reuses existing .opid (even for spaced Thai path) -> 1 doc!
                scan_recover = test_app.state.watcher.scan_once(require_stability=False)
                self.assertEqual(scan_recover["processed"], 1)
                self.assertEqual(len(scan_recover["doc_ids"]), 1)
                self.assertFalse(os.path.exists(inbox_file))
                self.assertFalse(os.path.exists(f"{inbox_file}.opid"))
                self.assertEqual(client.get("/api/documents").json()["total"], 1)

                # Legacy .opid sidecar containing spaces in rel_path must still be accepted on retry without corruption.
                legacy_png = _make_png_bytes((55, 66, 77))
                legacy_size = len(legacy_png)
                legacy_sha = hashlib.sha256(legacy_png).hexdigest()
                legacy_rel = "ครู อนุบาล 1/บิล เก่า f3r.png"
                legacy_file = os.path.join(spaced_dir, "บิล เก่า f3r.png")
                with open(legacy_file, "wb") as fh:
                    fh.write(legacy_png)
                legacy_op_id = f"watcher:{legacy_rel}:{legacy_size}:{legacy_sha}:deadbeef1234"
                with open(f"{legacy_file}.opid", "w", encoding="utf-8") as fh:
                    fh.write(f"{legacy_size}:{legacy_sha}:{legacy_op_id}\n")

                scan_legacy_ambiguous = _run_ambiguous_scan_once()
                self.assertEqual(scan_legacy_ambiguous["processed"], 0)
                self.assertEqual(scan_legacy_ambiguous["skipped"], 1)

                scan_legacy_recover = test_app.state.watcher.scan_once(require_stability=False)
                self.assertEqual(scan_legacy_recover["processed"], 1)
                self.assertFalse(os.path.exists(legacy_file))
                self.assertFalse(os.path.exists(f"{legacy_file}.opid"))
                self.assertEqual(client.get("/api/documents").json()["total"], 2)

                # Corrupted .opid sidecar on disk must also defer before ingestion without quarantining.
                corrupt_file = os.path.join(cfg.inbox_dir, "corrupt f3r.png")
                with open(corrupt_file, "wb") as fh:
                    fh.write(_make_png_bytes((99, 88, 77)))
                with open(f"{corrupt_file}.opid", "w", encoding="utf-8") as fh:
                    fh.write("corrupt-partial-content\n")

                with mock.patch("watcher.ocr_service.ingest_document", wraps=ocr_service.ingest_document) as ingest_spy_3:
                    scan_corrupt = test_app.state.watcher.scan_once(require_stability=False)

                self.assertEqual(scan_corrupt["processed"], 0)
                self.assertEqual(scan_corrupt["failed"], 0)
                self.assertEqual(scan_corrupt["skipped"], 1)
                self.assertEqual(ingest_spy_3.call_count, 0)
                self.assertTrue(os.path.exists(corrupt_file))

                os.remove(f"{corrupt_file}.opid")
                scan_corrupt_recovered = test_app.state.watcher.scan_once(require_stability=False)
                self.assertEqual(scan_corrupt_recovered["processed"], 1)
                self.assertEqual(client.get("/api/documents").json()["total"], 3)


if __name__ == "__main__":
    unittest.main()
