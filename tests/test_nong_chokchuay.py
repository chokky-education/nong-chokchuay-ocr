import base64
import csv
import hashlib
import hmac
import io
import json
import os
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module  # noqa: E402
import extractor  # noqa: E402
from config import AppConfig  # noqa: E402
from tests.fakes import FakeLineOCR, make_png_bytes, make_temp_data_dir  # noqa: E402


def _sign_line_body(body_bytes: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


class NongChokChuayTests(unittest.TestCase):
    def setUp(self):
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


if __name__ == "__main__":
    unittest.main()
