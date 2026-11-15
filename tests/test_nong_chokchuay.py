import base64
import csv
import hashlib
import hmac
import io
import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module  # noqa: E402
import extractor  # noqa: E402
import line_service  # noqa: E402
from config import AppConfig  # noqa: E402
from tests.fakes import FakeLineOCR, make_png_bytes, make_temp_data_dir  # noqa: E402


def _sign_line_body(body_bytes: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


class NongChokChuayTests(unittest.TestCase):
    def setUp(self):
        self.start_loading_original = line_service.start_line_loading
        loading = patch("line_service.start_line_loading", return_value=True)
        loading.start()
        self.addCleanup(loading.stop)
        self._tmp = make_temp_data_dir()
        self.cfg = AppConfig.from_dir(
            self._tmp.name,
            auto_extract_on_ingest=True,
            llm_api_key="",
            line_channel_secret="chok-secret-123",
            line_channel_access_token="chok-token-abc",
            public_base_url="https://nongchok.example.com",
        )
        self.fake_ocr = FakeLineOCR()
        self.test_app = app_module.create_app(config=self.cfg, engine_factory=lambda: self.fake_ocr)
        self.client_cm = TestClient(self.test_app)
        self.client = self.client_cm.__enter__()

    def test_loading_api_and_webhook_fail_open(self):
        start_line_loading = self.start_loading_original
        with patch("line_service.requests.post") as post:
            post.return_value.status_code = 202
            self.assertTrue(start_line_loading("Uteacher", "token"))
            self.assertEqual(post.call_args.kwargs["json"], {"chatId": "Uteacher", "loadingSeconds": 60})
            self.assertEqual(post.call_args.kwargs["timeout"], (1, 1))
            for status in (400, 401, 429, 500):
                post.return_value.status_code = status
                self.assertFalse(start_line_loading("Uteacher", "token"))
            post.side_effect = line_service.requests.Timeout("secret must not be logged")
            self.assertFalse(start_line_loading("Uteacher", "token"))

        for kind in ("text", "image", "file", "postback"):
            event = {"type": "postback" if kind == "postback" else "message", "replyToken": "rt", "source": {"type": "user", "userId": "Uteacher"}, "message": {"type": kind, "id": "m", "text": "เมนู"}, "postback": {"data": "unknown"}}
            body = json.dumps({"events": [event]}).encode()
            with self.subTest(kind=kind), patch("line_service.start_line_loading", return_value=False) as loading, patch("line_service.download_line_content", return_value=(make_png_bytes(), "image/png")), patch("line_service.reply_line_messages", return_value=True):
                response = self.client.post("/api/line/webhook", content=body, headers={"X-Line-Signature": _sign_line_body(body, "chok-secret-123")})
                self.assertEqual(response.status_code, 200)
                loading.assert_called_once_with("Uteacher", "chok-token-abc")

        for source, kind in (({"type": "group", "userId": "Uteacher"}, "text"), ({"type": "user", "userId": "Uteacher"}, "sticker"), ({"type": "user"}, "text")):
            event = {"type": "message", "replyToken": "rt", "source": source, "message": {"type": kind, "text": "เมนู"}}
            body = json.dumps({"events": [event]}).encode()
            with patch("line_service.start_line_loading") as loading, patch("line_service.reply_line_messages", return_value=True):
                self.client.post("/api/line/webhook", content=body, headers={"X-Line-Signature": _sign_line_body(body, "chok-secret-123")})
                loading.assert_not_called()

    def tearDown(self):
        self.client_cm.__exit__(None, None, None)
        self._tmp.cleanup()

    def test_extract_student_profile_regex_and_ocr_noise(self):
        sample_text = (
            "ใบสมัครเข้าเรียนและประวัติเด็กอนุบาล\n"
            "ชื่อ-นามสกุล: เด็กหญิงมะลิ ใจดี\n"
            "ชื่อเล่น: น้องมะลิ\n"
            "เลขบัตรประชาชน: 1-1009-O1234-56-7\n"
            "วันเกิด: 15/06/2565\n"
            "ชั้นเรียน: อนุบาล 2/1\n"
            "ผู้ปกครอง: นางสาวสมฤดี ใจดี\n"
            "เบอร์โทรฉุกเฉิน: 0812345678\n"
            "แพ้อาหาร: แพ้นมวัว และถั่วลิสง\n"
            "โรคประจำตัว: หอบหืดเล็กน้อย\n"
        )
        self.assertTrue(extractor.is_likely_student_document(sample_text))
        stu = extractor.extract_student_profile_regex(sample_text)
        self.assertEqual(stu["full_name"], "เด็กหญิงมะลิ ใจดี")
        self.assertEqual(stu["nickname"], "น้องมะลิ")
        self.assertEqual(stu["citizen_id"], "1100901234567")
        self.assertEqual(stu["birth_date"], "2022-06-15")
        self.assertEqual(stu["classroom"], "อนุบาล 2/1")
        self.assertEqual(stu["parent_name"], "นางสาวสมฤดี ใจดี")
        self.assertEqual(stu["parent_phone"], "081-234-5678")
        self.assertIn("นมวัว", stu["allergies"])
        self.assertIn("หอบหืด", stu["medical_notes"])

        # Parent circular/notice must NOT falsely trigger student extraction
        notice_text = (
            "หนังสือแจ้งผู้ปกครอง\n"
            "โรงเรียนอนุบาลสุขใจ\n"
            "เรื่อง แจ้งกำหนดการวันหยุดและทัศนศึกษา\n"
            "ขอความร่วมมือผู้ปกครองเตรียมชุดพละให้นักเรียน\n"
        )
        self.assertFalse(extractor.is_likely_student_document(notice_text))

    def test_ocr_auto_extracts_student_crud_and_csv(self):
        self.fake_ocr.default_text = (
            "ทะเบียนประวัตินักเรียนอนุบาล\n"
            "ชื่อ-นามสกุล: เด็กหญิงมะลิ ใจดี\n"
            "ชื่อเล่น: น้องมะลิ\n"
            "เลขประจำตัวประชาชน: 1-1009-01234-56-7\n"
            "วันเกิด: 15/06/2565\n"
            "ห้องเรียน: อนุบาล 2/1\n"
            "ชื่อผู้ปกครอง: คุณแม่สมฤดี ใจดี\n"
            "เบอร์โทร: 081-234-5678\n"
            "อาหารที่แพ้: แพ้นมวัว\n"
        )
        res = self.client.post(
            "/api/ocr",
            files={"file": ("mali_form.png", make_png_bytes(), "image/png")},
        )
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertEqual(payload["category"], "student")
        self.assertEqual(payload["review_status"], "pending_review")
        self.assertIsNotNone(payload["student"])
        self.assertEqual(payload["student"]["nickname"], "น้องมะลิ")
        doc_id = payload["doc_id"]

        # Allergy filter returns Mali
        list_res = self.client.get("/api/students", params={"has_allergy": "true"})
        self.assertEqual(list_res.status_code, 200)
        students = list_res.json()["students"]
        self.assertEqual(len(students), 1)
        self.assertEqual(students[0]["document_id"], doc_id)

        # Manual Create + Update + Delete
        create_res = self.client.post(
            "/api/students",
            json={
                "full_name": "เด็กชายต้นกล้า รักเรียน",
                "nickname": "น้องต้นกล้า",
                "classroom": "อนุบาล 1/2",
                "parent_phone": "089-999-8888",
                "allergies": "",
            },
        )
        self.assertEqual(create_res.status_code, 200)
        created_id = create_res.json()["student"]["id"]

        upd_res = self.client.put(
            f"/api/students/{created_id}",
            json={
                "full_name": "เด็กชายต้นกล้า รักเรียน",
                "nickname": "น้องต้นกล้า",
                "classroom": "อนุบาล 1/2",
                "parent_phone": "089-999-8888",
                "allergies": "แพ้อาหารทะเล",
            },
        )
        self.assertEqual(upd_res.status_code, 200)
        self.assertEqual(upd_res.json()["student"]["allergies"], "แพ้อาหารทะเล")

        csv_res = self.client.get("/api/students/export.csv")
        self.assertEqual(csv_res.status_code, 200)
        csv_text = csv_res.content.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(csv_text)))
        self.assertEqual(rows[0][0], "full_name")
        self.assertEqual(rows[0][-1], "document_id")
        self.assertEqual(len(rows), 3)

        del_res = self.client.delete(f"/api/students/{created_id}")
        self.assertEqual(del_res.status_code, 200)
        self.assertEqual(self.client.delete(f"/api/students/{created_id}").status_code, 404)

    def test_reimbursement_workflow_review_queue_and_csv_contract(self):
        # 1. Kindergarten classroom receipt
        self.fake_ocr.default_text = (
            "ใบเสร็จรับเงิน\n"
            "ร้านสมศึกษาเครื่องเขียน\n"
            "วันที่ 10/11/2569\n"
            "ค่าสีเทียนและกระดาษวาดเขียน ห้องอนุบาล\n"
            "ยอดสุทธิ 150.00 บาท\n"
        )
        r1 = self.client.post(
            "/api/ocr",
            files={"file": ("crayons.png", make_png_bytes(), "image/png")},
        )
        self.assertEqual(r1.status_code, 200)
        d1 = r1.json()
        self.assertEqual(d1["expense"]["expense_category"], "classroom")
        self.assertEqual(d1["expense"]["reimbursement_status"], "pending_reimbursement")
        self.assertAlmostEqual(d1["expense"]["total_amount"], 150.0)

        # System stats show 1 pending_review and 150.00 pending reimbursement
        sys1 = self.client.get("/api/system").json()
        self.assertEqual(sys1["database"]["pending_review_count"], 1)
        self.assertAlmostEqual(sys1["database"]["pending_reimbursement_total"], 150.0)

        # Confirm review + mark reimbursed
        rev = self.client.post(
            f"/api/documents/{d1['doc_id']}/review",
            json={"review_status": "reviewed", "reimbursement_status": "reimbursed"},
        )
        self.assertEqual(rev.status_code, 200)
        self.assertEqual(rev.json()["document"]["review_status"], "reviewed")
        self.assertEqual(rev.json()["document"]["expense"]["reimbursement_status"], "reimbursed")

        # 2. Personal receipt tagged ส่วนตัว
        self.fake_ocr.default_text = "ใบเสร็จรับเงิน\nร้านกาแฟอเมซอน\nยอดสุทธิ 65.00 บาท"
        r2 = self.client.post(
            "/api/ocr",
            data={"tags": "ส่วนตัว"},
            files={"file": ("coffee.png", make_png_bytes(), "image/png")},
        )
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["expense"]["reimbursement_status"], "personal")

        # Filter expenses & check export CSV preserves trailing tags column
        exp_reimb = self.client.get("/api/expenses", params={"reimbursement_status": "reimbursed"}).json()
        self.assertEqual(exp_reimb["summary"]["count"], 1)
        self.assertAlmostEqual(exp_reimb["summary"]["reimbursed_total"], 150.0)

        csv_res = self.client.get("/api/expenses/export.csv")
        self.assertEqual(csv_res.status_code, 200)
        self.assertTrue(csv_res.content.startswith(b"\xef\xbb\xbf"))
        rows = list(csv.reader(io.StringIO(csv_res.content.decode("utf-8-sig"))))
        self.assertEqual(rows[0][-1], "tags")
        self.assertIn("reimbursement_status", rows[0])

    def test_line_webhook_signature_image_commands_and_postbacks(self):
        # Missing/invalid signature returns 401
        bad_body = json.dumps({"events": []}).encode("utf-8")
        unauth = self.client.post(
            "/api/line/webhook",
            content=bad_body,
            headers={"X-Line-Signature": "invalid-sig", "Content-Type": "application/json"},
        )
        self.assertEqual(unauth.status_code, 401)

        # Valid LINE image webhook -> downloads image, runs OCR, saves doc/expense, replies with Flex
        self.fake_ocr.default_text = (
            "ใบเสร็จรับเงิน\n"
            "ร้านสหกรณ์โรงเรียน\n"
            "สีเทียนและนมกล่องเด็กอนุบาล\n"
            "ยอดสุทธิ 220.00 บาท\n"
        )
        img_event_body = json.dumps(
            {
                "events": [
                    {
                        "type": "message",
                        "replyToken": "reply-tok-1",
                        "source": {"userId": "Uteacher001"},
                        "message": {"id": "msg-img-1", "type": "image"},
                    }
                ]
            }
        ).encode("utf-8")

        with patch(
            "line_service.download_line_content",
            return_value=(make_png_bytes(), "image/png"),
        ) as mock_dl, patch(
            "line_service.reply_line_messages",
            return_value=True,
        ) as mock_reply:
            ok_img = self.client.post(
                "/api/line/webhook",
                content=img_event_body,
                headers={
                    "X-Line-Signature": _sign_line_body(img_event_body, "chok-secret-123"),
                    "Content-Type": "application/json",
                },
            )

        self.assertEqual(ok_img.status_code, 200)
        mock_dl.assert_called_once_with("msg-img-1", "chok-token-abc")
        mock_reply.assert_called_once()
        reply_payload = ok_img.json()["replies"][0]
        self.assertEqual(reply_payload["flex_message"]["type"], "flex")
        doc_id = reply_payload["actions"][0]["doc_id"]

        # Text command: "ยอดรอเบิก"
        txt_body = json.dumps(
            {
                "events": [
                    {
                        "type": "message",
                        "replyToken": "reply-tok-2",
                        "source": {"userId": "Uteacher001"},
                        "message": {"id": "msg-txt-1", "type": "text", "text": "ยอดรอเบิก"},
                    }
                ]
            }
        ).encode("utf-8")
        with patch("line_service.reply_line_messages", return_value=True):
            res_cmd = self.client.post(
                "/api/line/webhook",
                content=txt_body,
                headers={
                    "X-Line-Signature": _sign_line_body(txt_body, "chok-secret-123"),
                    "Content-Type": "application/json",
                },
            )
        self.assertEqual(res_cmd.status_code, 200)
        self.assertIn("220.00", res_cmd.json()["replies"][0]["reply_text"])

        # Search fallback via text command
        search_body = json.dumps(
            {
                "events": [
                    {
                        "type": "message",
                        "replyToken": "reply-tok-3",
                        "source": {"userId": "Uteacher001"},
                        "message": {"id": "msg-txt-2", "type": "text", "text": "ค้นหา สีเทียน"},
                    }
                ]
            }
        ).encode("utf-8")
        with patch("line_service.reply_line_messages", return_value=True):
            res_search = self.client.post(
                "/api/line/webhook",
                content=search_body,
                headers={
                    "X-Line-Signature": _sign_line_body(search_body, "chok-secret-123"),
                    "Content-Type": "application/json",
                },
            )
        self.assertEqual(res_search.status_code, 200)
        self.assertEqual(res_search.json()["replies"][0]["intent"], "search")

        # Postback confirm_review + mark_reimbursed
        pb_body = json.dumps(
            {
                "events": [
                    {
                        "type": "postback",
                        "replyToken": "reply-tok-4",
                        "source": {"userId": "Uteacher001"},
                        "postback": {"data": f"action=confirm_review&doc_id={doc_id}"},
                    },
                    {
                        "type": "postback",
                        "replyToken": "reply-tok-5",
                        "source": {"userId": "Uteacher001"},
                        "postback": {"data": f"action=mark_reimbursed&doc_id={doc_id}"},
                    },
                ]
            }
        ).encode("utf-8")
        with patch("line_service.reply_line_messages", return_value=True):
            res_pb = self.client.post(
                "/api/line/webhook",
                content=pb_body,
                headers={
                    "X-Line-Signature": _sign_line_body(pb_body, "chok-secret-123"),
                    "Content-Type": "application/json",
                },
            )
        self.assertEqual(res_pb.status_code, 200)
        self.assertEqual(res_pb.json()["processed_events"], 2)

        doc_after = self.client.get(f"/api/documents/{doc_id}").json()["document"]
        self.assertEqual(doc_after["review_status"], "reviewed")
        self.assertEqual(doc_after["expense"]["reimbursement_status"], "reimbursed")

    def test_line_simulator_presets_and_static_ui(self):
        # Preset endpoint with FakeLineOCR
        self.fake_ocr.default_text = (
            "ใบสมัครเด็กอนุบาล\n"
            "ชื่อ: เด็กชายปั้น พากเพียร (ชื่อเล่น: น้องปั้น)\n"
            "ชั้นเรียน: อนุบาล 3/1\n"
            "เบอร์ผู้ปกครอง: 0851112222\n"
            "แพ้อาหาร: แพ้ไข่ไก่\n"
        )
        preset_res = self.client.post("/api/samples/kindergarten/student_profile")
        self.assertEqual(preset_res.status_code, 200)
        self.assertEqual(preset_res.json()["student"]["nickname"], "น้องปั้น")

        # Simulator text command for allergies
        sim_txt = self.client.post("/api/line/simulate", data={"text": "แพ้อาหาร"})
        self.assertEqual(sim_txt.status_code, 200)
        self.assertIn("น้องปั้น", sim_txt.json()["reply_text"])
        self.assertIn("แพ้ไข่ไก่", sim_txt.json()["reply_text"])

        # Simulator image upload
        self.fake_ocr.default_text = "ใบเสร็จรับเงิน\nร้านหนังสืออนุบาล\nยอดสุทธิ 95.00 บาท"
        sim_file = self.client.post(
            "/api/line/simulate",
            files={"file": ("line_bill.png", make_png_bytes(), "image/png")},
        )
        self.assertEqual(sim_file.status_code, 200)
        sim_payload = sim_file.json()
        self.assertEqual(sim_payload["mode"], "file")
        self.assertEqual(sim_payload["flex_message"]["type"], "flex")

        # Web UI HTML & Mascot asset
        index_res = self.client.get("/")
        self.assertEqual(index_res.status_code, 200)
        self.assertIn("น้องโชคช่วย", index_res.text)
        self.assertIn("/static/mascot.jpg", index_res.text)

        mascot_res = self.client.get("/static/mascot.jpg")
        self.assertEqual(mascot_res.status_code, 200)
        self.assertTrue(len(mascot_res.content) > 1000)

    def test_natural_data_questions_return_cards_with_and_without_llm(self):
        self.fake_ocr.default_text = "ใบเสร็จรับเงิน\nร้านเครื่องเขียน\nยอดสุทธิ 125.50 บาท"
        receipt = self.client.post(
            "/api/ocr", files={"file": ("bill.png", make_png_bytes(), "image/png")}
        )
        self.assertEqual(receipt.status_code, 200)
        student = self.client.post("/api/students", json={
            "full_name": "เด็กหญิงมะลิ ใจดี", "nickname": "มะลิ", "allergies": "แพ้นมวัว",
        })
        self.assertEqual(student.status_code, 200)
        cases = [
            ("มียอดรอเบิกเท่าไหร่", "reimbursement_summary", "expense_summary"),
            ("ตอนนี้มีเงินรอเบิกคืนกี่บาทครับ?", "reimbursement_summary", "expense_summary"),
            ("ช่วยสรุปยอดรอเบิกให้หน่อยค่ะ", "reimbursement_summary", "expense_summary"),
            ("ขอดูค่าใช้จ่ายหน่อย", "reimbursement_summary", "expense_summary"),
            ("ใครแพ้อาหารบ้าง", "student_allergy_summary", "allergy_watchlist"),
            ("มีเด็กแพ้อาหารกี่คนคะ", "student_allergy_summary", "allergy_watchlist"),
            ("ขอดูรายชื่อเด็กที่แพ้อาหารหน่อยครับ", "student_allergy_summary", "allergy_watchlist"),
            ("  ใคร แพ้อาหาร บ้าง คะ？  ", "student_allergy_summary", "allergy_watchlist"),
            ("ตอนนี้มีเอกสารรอตรวจกี่ใบ", "pending_review_list", "pending_carousel"),
            ("ขอดูรายการรอตรวจหน่อยครับ", "pending_review_list", "pending_carousel"),
            ("มีอะไรค้างตรวจบ้าง", "pending_review_list", "pending_carousel"),
            ("มีเอกสารที่ยังรอตรวจทานอยู่ไหมคะ", "pending_review_list", "pending_carousel"),
        ]
        with patch("line_service.requests.post") as external_post:
            for api_key in ("", "fake-gemini-key"):
                self.cfg.llm_api_key = api_key
                for text, intent, kind in cases:
                    with self.subTest(llm_enabled=bool(api_key), text=text):
                        response = self.client.post("/api/line/simulate", data={"text": text})
                        self.assertEqual(response.status_code, 200)
                        payload = response.json()
                        self.assertEqual(payload["intent"], intent)
                        self.assertEqual(payload["card_view"]["kind"], kind)
                        self._assert_valid_line_messages(payload["line_messages"])
                        self.assertEqual(payload["line_messages"][0]["type"], "flex")
                        if kind == "expense_summary":
                            self.assertEqual(payload["card_view"]["pending_total"], 125.50)
                        elif kind == "allergy_watchlist":
                            self.assertIn("มะลิ", payload["reply_text"])
                            self.assertIn("แพ้นมวัว", payload["reply_text"])
                        else:
                            self.assertEqual(len(payload["documents"]), 1)
            external_post.assert_not_called()

    def test_natural_questions_use_shared_cards_and_read_lock_on_webhook(self):
        self.cfg.llm_api_key = "fake-gemini-key"
        cases = [
            ("มียอดรอเบิกเท่าไหร่", "expense_summary"),
            ("ใครแพ้อาหารบ้าง", "allergy_watchlist"),
            ("มีเอกสารรอตรวจไหม", "pending_carousel"),
        ]
        storage = self.test_app.state.storage
        with patch("line_service.requests.post") as external_post, patch(
            "line_service.reply_line_messages", return_value=True
        ) as reply, patch.object(storage, "execute_read", wraps=storage.execute_read) as read:
            for text, kind in cases:
                with self.subTest(text=text):
                    body = json.dumps({"events": [{
                        "type": "message", "replyToken": "test-token",
                        "source": {"type": "user", "userId": "Uteacher001"},
                        "message": {"type": "text", "text": text},
                    }]}).encode("utf-8")
                    response = self.client.post("/api/line/webhook", content=body, headers={
                        "X-Line-Signature": _sign_line_body(body, "chok-secret-123"),
                        "Content-Type": "application/json",
                    })
                    self.assertEqual(response.status_code, 200)
                    payload = response.json()["replies"][0]
                    self.assertEqual(payload["card_view"]["kind"], kind)
                    self.assertEqual(reply.call_args.args[1][0]["type"], "flex")
                    self._assert_valid_line_messages(reply.call_args.args[1])
            # Each event reads shared preferences and card data under the coordinator lock.
            self.assertEqual(read.call_count, 2 * len(cases))
            external_post.assert_not_called()

    def test_natural_card_routing_preserves_explicit_search_and_ai_writing(self):
        self.cfg.llm_api_key = "fake-gemini-key"
        with patch("line_service.generate_nong_chok_ai_reply", return_value="ร่างข้อความตัวอย่าง") as ai:
            for text in ("ค้นหา ใครแพ้อาหารบ้าง", "หา ยอดรอเบิก", "search มีเอกสารรอตรวจไหม"):
                with self.subTest(text=text):
                    response = self.client.post("/api/line/simulate", data={"text": text})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["intent"], "search")
            ai.assert_not_called()
            for text in (
                "ช่วยร่างข้อความแจ้งผู้ปกครองเรื่องแพ้อาหาร",
                "ช่วยคิดวิธีติดตามยอดรอเบิก",
                "เอกสารรอตรวจควรจัดเก็บอย่างไร",
                "ใครแพ้อาหารบ้าง ช่วยแปลประโยคนี้เป็นอังกฤษ",
                "มียอดรอเบิกเท่าไหร่ แล้วใครแพ้อาหารบ้าง",
            ):
                with self.subTest(text=text):
                    ai.reset_mock()
                    response = self.client.post("/api/line/simulate", data={"text": text})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["intent"], "ai_chat")
                    ai.assert_called_once()

    def test_gemini_flash_system_instruction_and_pinky_teacher_persona(self):
        sys_inst = line_service.build_nong_chok_system_instruction()
        self.assertIn("ครูพิ้งกี้", sys_inst)

        # Offline summary, text commands, and postback replies all address ครูพิ้งกี้
        offline_help = line_service.process_line_text_command("สวัสดี", db_path=self.cfg.db_path, config=self.cfg)
        self.assertIn("ครูพิ้งกี้", offline_help["reply_text"])

        r_up = self.client.post(
            "/api/ocr",
            files={"file": ("pinky_bill.png", make_png_bytes(), "image/png")},
        )
        doc = r_up.json()
        summary = line_service.build_nong_chok_reply_summary(
            doc,
            expense=doc.get("expense"),
            config=self.cfg,
        )
        self.assertIn("ครูพิ้งกี้", summary["header_title"])
        self.assertIn("ครูพิ้งกี้", summary["reply_text"])

        pb = line_service.handle_line_postback(
            f"action=confirm_review&doc_id={doc['doc_id']}",
            db_path=self.cfg.db_path,
            config=self.cfg,
        )
        self.assertIn("ครูพิ้งกี้", pb["reply_text"])

        # Default Gemini 4 Hydrogen sends natural Thai kindergarten sidekick systemInstruction
        self.assertIn("น้องโชค", sys_inst)
        self.assertIn("อนุบาล", sys_inst)
        self.assertIn("ผู้ปกครอง", sys_inst)
        self.assertIn("แพ้อาหาร", sys_inst)
        self.assertIn('ห้ามขึ้นต้นด้วย "สวัสดีครับครูพิ้งกี้" ซ้ำๆ', sys_inst)

        ai_cfg = AppConfig.from_dir(
            self._tmp.name,
            llm_provider="gemini",
            llm_api_key="fake-gemini-key",
            teacher_name="ครูพิ้งกี้",
        )
        self.assertEqual(ai_cfg.llm_model, "gemini-4-hydrogen")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"thought": True, "text": "คิดหาคำพูดเป็นกันเอง..."},
                            {"text": "ได้เลยครับ เดี๋ยวน้องโชคร่างข้อความแจ้งผู้ปกครองให้ทันทีนะครับ"},
                        ]
                    }
                }
            ]
        }

        with patch("line_service.requests.post", return_value=mock_resp) as mock_post:
            res_ai = line_service.process_line_text_command(
                "ช่วยคิดข้อความแจ้งผู้ปกครองในไลน์กลุ่มหน่อยสิ",
                db_path=self.cfg.db_path,
                config=ai_cfg,
            )

        self.assertEqual(res_ai["intent"], "ai_chat")
        self.assertEqual(res_ai["model"], "gemini-4-hydrogen")
        self.assertEqual(res_ai["reply_text"], "ได้เลยครับ เดี๋ยวน้องโชคร่างข้อความแจ้งผู้ปกครองให้ทันทีนะครับ")
        self.assertNotIn("คิดหาคำพูด", res_ai["reply_text"])
        self.assertFalse(res_ai["reply_text"].startswith("สวัสดีครับครูพิ้งกี้!"))
        mock_post.assert_called_once()
        called_url = mock_post.call_args.args[0]
        called_json = mock_post.call_args.kwargs["json"]
        self.assertIn("models/gemini-4-hydrogen:generateContent", called_url)
        self.assertEqual(mock_post.call_args.kwargs["timeout"], 20)
        self.assertEqual(called_json["generationConfig"]["temperature"], 0.65)
        self.assertEqual(called_json["generationConfig"]["maxOutputTokens"], 2048)
        self.assertIn("ครูพิ้งกี้", called_json["systemInstruction"]["parts"][0]["text"])

        # Truncated MAX_TOKENS candidate without parts returns None cleanly for fallback
        truncated_resp = MagicMock()
        truncated_resp.status_code = 200
        truncated_resp.raise_for_status.return_value = None
        truncated_resp.json.return_value = {
            "candidates": [{"content": {"role": "model"}, "finishReason": "MAX_TOKENS"}]
        }
        with patch("line_service.requests.post", return_value=truncated_resp):
            self.assertIsNone(
                line_service.generate_nong_chok_ai_reply(
                    "สรุปงาน",
                    db_context="ไม่มี",
                    config=ai_cfg,
                )
            )

        # OpenAI provider with blank llm_model defaults to gpt-4o-mini consistently in request and response metadata
        oa_cfg = AppConfig.from_dir(
            self._tmp.name,
            llm_provider="openai",
            llm_api_key="fake-oa-key",
            llm_model="   ",
        )
        oa_resp = MagicMock()
        oa_resp.status_code = 200
        oa_resp.raise_for_status.return_value = None
        oa_resp.json.return_value = {"choices": [{"message": {"content": "รับทราบครับครูพิ้งกี้"}}]}
        with patch("line_service.requests.post", return_value=oa_resp) as mock_oa_post:
            res_oa = line_service.process_line_text_command(
                "ช่วยเช็กงานหน่อย",
                db_path=self.cfg.db_path,
                config=oa_cfg,
            )
        self.assertEqual(res_oa["model"], "gpt-4o-mini")
        self.assertEqual(mock_oa_post.call_args.kwargs["json"]["model"], "gpt-4o-mini")

    def _assert_valid_line_messages(self, messages):
        self.assertIsInstance(messages, list)
        self.assertGreaterEqual(len(messages), 1)
        self.assertLessEqual(len(messages), 5)

        for msg in messages:
            mtype = msg.get("type")
            self.assertIn(mtype, {"text", "flex"})

            if "quickReply" in msg:
                qr_items = msg["quickReply"].get("items") or []
                self.assertGreaterEqual(len(qr_items), 1)
                self.assertLessEqual(len(qr_items), 13)
                for item in qr_items:
                    self.assertEqual(item.get("type"), "action")
                    act = item.get("action") or {}
                    self.assertEqual(act.get("type"), "message")
                    lbl = act.get("label") or ""
                    txt = act.get("text") or ""
                    self.assertTrue(1 <= len(lbl) <= 20, f"Invalid quickReply label length: {lbl!r}")
                    self.assertFalse(lbl.endswith("("), f"Dangling '(' in quickReply label: {lbl!r}")
                    self.assertEqual(lbl.count("("), lbl.count(")"), f"Unbalanced parens in quickReply label: {lbl!r}")
                    self.assertTrue(1 <= len(txt) <= 300, f"Invalid quickReply text length: {txt!r}")

            if mtype == "text":
                self.assertTrue(1 <= len(msg.get("text") or "") <= 5000)
                continue

            alt_text = msg.get("altText") or ""
            self.assertTrue(1 <= len(alt_text) <= 400, f"Invalid Flex altText length: {len(alt_text)}")
            contents = msg.get("contents") or {}
            ctype = contents.get("type")
            self.assertIn(ctype, {"bubble", "carousel"})
            bubbles = contents.get("contents") if ctype == "carousel" else [contents]
            self.assertIsInstance(bubbles, list)
            self.assertTrue(1 <= len(bubbles) <= 12, f"Invalid carousel bubble count: {len(bubbles)}")

            for bubble in bubbles:
                self.assertEqual(bubble.get("type"), "bubble")
                body = bubble.get("body") or {}
                self.assertTrue(len(body.get("contents") or []) >= 1)

                if "hero" in bubble:
                    hero = bubble["hero"]
                    self.assertEqual(hero.get("type"), "image")
                    self.assertTrue(
                        (hero.get("url") or "").startswith("https://"),
                        f"LINE Flex hero.url must be https://, got {hero.get('url')!r}",
                    )
                    self.assertEqual(hero.get("aspectMode"), "fit")
                    self.assertTrue(bool(hero.get("backgroundColor")))

                if "footer" in bubble:
                    f_btns = bubble["footer"].get("contents") or []
                    self.assertTrue(1 <= len(f_btns) <= 5, f"Invalid footer button count: {len(f_btns)}")
                    for btn in f_btns:
                        self.assertEqual(btn.get("type"), "button")
                        act = btn.get("action") or {}
                        lbl = act.get("label") or ""
                        self.assertTrue(1 <= len(lbl) <= 20, f"Invalid Flex button label ({len(lbl)}): {lbl!r}")
                        self.assertFalse(lbl.endswith("("), f"Dangling '(' in Flex button label: {lbl!r}")
                        self.assertEqual(lbl.count("("), lbl.count(")"), f"Unbalanced parens in Flex button label: {lbl!r}")
                        atype = act.get("type")
                        self.assertIn(atype, {"postback", "uri", "message"})
                        if atype == "postback":
                            data = act.get("data") or ""
                            self.assertTrue(1 <= len(data) <= 300, f"Invalid postback data: {data!r}")
                        elif atype == "uri":
                            uri = act.get("uri") or ""
                            self.assertTrue(1 <= len(uri) <= 1000, f"Invalid URI length: {uri!r}")
                            if uri.startswith("tel:"):
                                tel_body = uri[4:]
                                digits = "".join(ch for ch in tel_body if ch.isdigit())
                                self.assertGreaterEqual(len(digits), 3, f"Invalid tel URI without digits: {uri!r}")
                                self.assertNotIn("openExternalBrowser", uri)
                            else:
                                self.assertTrue(
                                    uri.startswith(("https://", "http://")),
                                    f"Unexpected URI scheme: {uri!r}",
                                )

    def test_line_mascot_cards_all_seven_scenarios_and_assets(self):
        # 0. All 9 mascot card PNG assets exist and are served over HTTP
        asset_paths = [
            "/static/mascot_cards/avatar.png",
            "/static/mascot_cards/hero_receipt.png",
            "/static/mascot_cards/hero_confirmed.png",
            "/static/mascot_cards/hero_student.png",
            "/static/mascot_cards/hero_pending.png",
            "/static/mascot_cards/hero_summary.png",
            "/static/mascot_cards/hero_allergy.png",
            "/static/mascot_cards/hero_search.png",
            "/static/mascot_cards/hero_menu.png",
        ]
        for path in asset_paths:
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, f"Missing mascot asset: {path}")
            self.assertTrue(len(resp.content) > 1000, f"Empty mascot asset: {path}")

        base_url = "https://nongchok.school.example.com"

        # 1. Scenario 1: Receipt Card (with portrait-safe hero aspectMode=fit + openExternalBrowser=1)
        self.fake_ocr.default_text = (
            "ใบเสร็จรับเงิน\nร้านสมใจเครื่องเขียน\nวันที่ 15/11/2026\n"
            "สีเทียนเด็ก 180.00\nยอดสุทธิ 180.00 บาท"
        )
        r_rec = self.client.post(
            "/api/line/simulate",
            files={"file": ("receipt_card.png", make_png_bytes(), "image/png")},
        )
        self.assertEqual(r_rec.status_code, 200)
        rec_data = r_rec.json()
        self.assertEqual(rec_data["card_view"]["kind"], "receipt")
        self.assertEqual(rec_data["card_view"]["mascot_image"], "/static/mascot_cards/hero_receipt.png")
        self.assertTrue(len(rec_data["quick_replies"]) >= 2)
        self.assertIn("quickReply", rec_data["line_messages"][-1])

        rec_summary_with_url = line_service.build_nong_chok_reply_summary(
            rec_data["ocr"],
            expense=rec_data["ocr"].get("expense"),
            public_base_url=base_url,
            config=self.cfg,
        )
        rec_hero = rec_summary_with_url["flex_message"]["contents"]["hero"]
        self.assertEqual(rec_hero["url"], f"{base_url}/static/mascot_cards/hero_receipt.png")
        self.assertNotIn("openExternalBrowser", rec_hero["url"])
        self.assertEqual(rec_hero["aspectMode"], "fit")
        self.assertEqual(rec_hero["backgroundColor"], "#DDF5EB")
        open_doc_actions = [a for a in rec_summary_with_url["actions"] if a["action"] == "open_doc"]
        self.assertEqual(len(open_doc_actions), 1)
        self.assertIn("openExternalBrowser=1", open_doc_actions[0]["url"])

        # 2. Scenario 2: Student Registration Card (tel: URI must not have openExternalBrowser=1)
        self.fake_ocr.default_text = (
            "ใบสมัครเด็กอนุบาล\n"
            "ชื่อ: เด็กหญิงใบบัว รักเรียน (ชื่อเล่น: น้องใบบัว)\n"
            "ชั้นเรียน: อนุบาล 1/2\n"
            "เบอร์ผู้ปกครอง: 089-777-8888\n"
            "แพ้อาหาร: แพ้ถั่วลิสง\n"
        )
        r_stu = self.client.post(
            "/api/line/simulate",
            files={"file": ("student_card.png", make_png_bytes(), "image/png")},
        )
        self.assertEqual(r_stu.status_code, 200)
        stu_data = r_stu.json()
        self.assertEqual(stu_data["card_view"]["kind"], "student")
        self.assertTrue(stu_data["card_view"]["has_allergy"])
        self.assertEqual(stu_data["card_view"]["mascot_image"], "/static/mascot_cards/hero_student.png")
        call_actions = [a for a in stu_data["actions"] if a["action"] == "call_parent"]
        self.assertEqual(len(call_actions), 1)
        self.assertEqual(call_actions[0]["url"], "tel:0897778888")
        self.assertNotIn("openExternalBrowser", call_actions[0]["url"])

        # 3. Scenario 3: Pending Review Carousel ("รอตรวจ")
        r_pend = line_service.process_line_text_command(
            "รอตรวจ",
            db_path=self.cfg.db_path,
            public_base_url=base_url,
            config=self.cfg,
        )
        self.assertEqual(r_pend["intent"], "pending_review_list")
        self.assertEqual(r_pend["card_view"]["kind"], "pending_carousel")
        self.assertGreaterEqual(len(r_pend["card_view"]["items"]), 2)
        self.assertEqual(r_pend["flex_message"]["contents"]["type"], "carousel")
        self.assertEqual(
            r_pend["flex_message"]["contents"]["contents"][0]["hero"]["url"],
            f"{base_url}/static/mascot_cards/hero_pending.png",
        )
        self.assertEqual(r_pend["flex_message"]["contents"]["contents"][0]["hero"]["aspectMode"], "fit")

        # 4. Scenario 4: Expense Summary ("ยอดรอเบิก")
        r_sum = line_service.process_line_text_command(
            "ยอดรอเบิก",
            db_path=self.cfg.db_path,
            public_base_url=base_url,
            config=self.cfg,
        )
        self.assertEqual(r_sum["intent"], "reimbursement_summary")
        self.assertEqual(r_sum["card_view"]["kind"], "expense_summary")
        self.assertEqual(r_sum["card_view"]["mascot_image"], "/static/mascot_cards/hero_summary.png")
        self.assertAlmostEqual(r_sum["card_view"]["pending_total"], 180.0, places=2)
        open_web_actions = [a for a in r_sum["actions"] if a["action"] == "open_web"]
        self.assertEqual(len(open_web_actions), 1)
        self.assertIn("openExternalBrowser=1", open_web_actions[0]["url"])

        # 5. Scenario 5: Allergy Watchlist ("แพ้อาหาร")
        r_alg = line_service.process_line_text_command(
            "แพ้อาหาร",
            db_path=self.cfg.db_path,
            public_base_url=base_url,
            config=self.cfg,
        )
        self.assertEqual(r_alg["intent"], "student_allergy_summary")
        self.assertEqual(r_alg["card_view"]["kind"], "allergy_watchlist")
        self.assertEqual(r_alg["card_view"]["mascot_image"], "/static/mascot_cards/hero_allergy.png")
        self.assertGreaterEqual(r_alg["card_view"]["allergy_count"], 1)

        # 6. Scenario 6: Search Carousel ("ค้นหา สีเทียน")
        r_srch = line_service.process_line_text_command(
            "ค้นหา สีเทียน",
            db_path=self.cfg.db_path,
            public_base_url=base_url,
            config=self.cfg,
        )
        self.assertEqual(r_srch["intent"], "search")
        self.assertEqual(r_srch["card_view"]["kind"], "search_carousel")
        self.assertEqual(r_srch["card_view"]["mascot_image"], "/static/mascot_cards/hero_search.png")
        self.assertGreaterEqual(len(r_srch["card_view"]["items"]), 1)
        srch_bubble = r_srch["flex_message"]["contents"]
        if srch_bubble.get("type") == "carousel":
            srch_bubble = srch_bubble["contents"][0]
        self.assertIn("openExternalBrowser=1", srch_bubble["footer"]["contents"][0]["action"]["uri"])

        # 7. Scenario 7: Shortcut Menu ("เมนู") & Confirmed Postback Card + run_cmd Postback
        r_menu = self.client.post("/api/line/simulate", data={"text": "เมนู"}).json()
        self.assertEqual(r_menu["intent"], "help")
        self.assertEqual(r_menu["card_view"]["kind"], "menu")
        self.assertEqual(r_menu["card_view"]["mascot_image"], "/static/mascot_cards/hero_menu.png")
        self.assertEqual(len(r_menu["card_view"]["shortcuts"]), 3)

        doc_id = rec_data["ocr"]["doc_id"]
        r_conf = self.client.post(
            "/api/line/simulate",
            data={"postback_data": f"action=confirm_review&doc_id={doc_id}"},
        ).json()
        self.assertTrue(r_conf["success"])
        self.assertEqual(r_conf["doc_id"], doc_id)
        self.assertEqual(r_conf["card_view"]["mascot_image"], "/static/mascot_cards/hero_confirmed.png")

        r_runcmd = self.client.post(
            "/api/line/simulate",
            data={"postback_data": "action=run_cmd&cmd=ยอดรอเบิก"},
        ).json()
        self.assertTrue(r_runcmd["success"])
        self.assertEqual(r_runcmd["card_view"]["kind"], "expense_summary")

        # 8. Strict LINE Messaging API schema validation across all generated responses + edge cases
        for payload in (rec_summary_with_url, stu_data, r_pend, r_sum, r_alg, r_srch, r_menu, r_conf, r_runcmd):
            self._assert_valid_line_messages(payload["line_messages"])

        # Edge case A: Student with non-digit phone ("ไม่มี") MUST NOT emit invalid "tel:" URI
        no_phone_summary = line_service.build_nong_chok_reply_summary(
            {"doc_id": "doc-no-phone", "filename": "student_no_phone.png"},
            student={
                "full_name": "เด็กชายกล้า เก่งกาจ",
                "nickname": "น้องกล้า",
                "classroom": "อนุบาล 2/1",
                "parent_phone": "ไม่มี",
                "allergies": "แพ้อาหารทะเล",
            },
            public_base_url=base_url,
            config=self.cfg,
        )
        self.assertEqual([a for a in no_phone_summary["actions"] if a["action"] == "call_parent"], [])
        self._assert_valid_line_messages(no_phone_summary["line_messages"])

        # Edge case B: International +66 phone & very long student nickname keep valid tel: and <=20-char balanced label
        plus_phone_summary = line_service.build_nong_chok_reply_summary(
            {"doc_id": "doc-plus-phone", "filename": "student_plus.png"},
            student={
                "full_name": "เด็กหญิงใบบัวชมพู รักเรียนยิ่ง",
                "nickname": "น้องใบบัวชมพูหวานใจคุณครู",
                "classroom": "อนุบาล 3/1",
                "parent_phone": "+66 89-777-8888",
                "allergies": "แพ้นมวัว",
            },
            public_base_url=base_url,
            config=self.cfg,
        )
        plus_calls = [a for a in plus_phone_summary["actions"] if a["action"] == "call_parent"]
        self.assertEqual(plus_calls[0]["url"], "tel:+66897778888")
        self._assert_valid_line_messages(plus_phone_summary["line_messages"])

        long_lbl = line_service._truncate_line_label("📞 โทรด่วน (น้องใบบัวชมพูหวานใจคุณครู)", 20)
        self.assertLessEqual(len(long_lbl), 20)
        self.assertEqual(long_lbl.count("("), long_lbl.count(")"))
        self.assertTrue(long_lbl.endswith(")"))

        # Edge case C: Plain http:// base URL omits Flex hero (LINE requires https://) while keeping Simulator mascot_image
        http_summary = line_service.build_nong_chok_reply_summary(
            rec_data["ocr"],
            expense=rec_data["ocr"].get("expense"),
            public_base_url="http://localhost:8765",
            config=self.cfg,
        )
        self.assertNotIn("hero", http_summary["flex_message"]["contents"])
        self.assertEqual(http_summary["card_view"]["mascot_image"], "/static/mascot_cards/hero_receipt.png")
        self._assert_valid_line_messages(http_summary["line_messages"])

        # Edge case D: Search carousel matching student ("ใบบัว") includes actionable footer buttons on LINE
        r_srch_stu = line_service.process_line_text_command(
            "ค้นหา ใบบัว",
            db_path=self.cfg.db_path,
            public_base_url=base_url,
            config=self.cfg,
        )
        self._assert_valid_line_messages(r_srch_stu["line_messages"])
        stu_bubble = r_srch_stu["flex_message"]["contents"]
        if stu_bubble.get("type") == "carousel":
            stu_bubble = stu_bubble["contents"][0]
        self.assertIn("footer", stu_bubble)
        self.assertGreaterEqual(len(stu_bubble["footer"]["contents"]), 1)


if __name__ == "__main__":
    unittest.main()
