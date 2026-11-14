import base64
import hashlib
import hmac
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs

import requests

import database

_REIMBURSEMENT_LABELS = {
    "pending_reimbursement": "รอเบิกคืนโรงเรียน",
    "reimbursed": "เบิกคืนแล้ว",
    "personal": "จ่ายส่วนตัว",
}

_EXPENSE_CATEGORY_LABELS = {
    "classroom": "สื่อการสอน / ห้องเรียน",
    "kids_snack": "นม / ขนม / อาหารเด็ก",
    "food": "อาหาร / เครื่องดื่ม",
    "transport": "เดินทาง / น้ำมัน",
    "utilities": "ค่าน้ำ / ค่าไฟ / เน็ต",
    "office": "เครื่องเขียน / สำนักงาน",
    "other": "อื่นๆ",
}


def verify_line_signature(body: bytes, signature: str, channel_secret: str) -> bool:
    """Verify LINE X-Line-Signature HMAC-SHA256 digest when channel_secret is configured."""
    secret = (channel_secret or "").strip()
    if not secret:
        return True
    if not signature:
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature.strip())


def download_line_content(message_id: str, access_token: str) -> tuple[bytes, str]:
    """Download image or file bytes from LINE Messaging API Content endpoint."""
    if not access_token:
        raise ValueError("LINE_CHANNEL_ACCESS_TOKEN is not configured")
    url = f"https://api-data.line.me/v2/bot/message/{message_id}/content"
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.content, resp.headers.get("content-type", "")


def reply_line_messages(
    reply_token: str,
    messages: List[Dict[str, Any]],
    access_token: str,
) -> bool:
    """Send free unlimited LINE Reply messages using replyToken."""
    if not reply_token or not access_token or not messages:
        return False
    url = "https://api.line.me/v2/bot/message/reply"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={"replyToken": reply_token, "messages": messages[:5]},
        timeout=15,
    )
    resp.raise_for_status()
    return True


def build_nong_chok_reply_summary(
    doc: Dict[str, Any],
    expense: Optional[Dict[str, Any]] = None,
    student: Optional[Dict[str, Any]] = None,
    public_base_url: str = "",
) -> Dict[str, Any]:
    """Build Nong Chok's warm reply text, LINE Flex Message, and interactive postback actions."""
    doc_id = doc.get("doc_id") or doc.get("id") or ""
    filename = doc.get("filename") or "เอกสารใหม่"
    base_url = (public_base_url or "").rstrip("/")

    actions: List[Dict[str, str]] = [
        {
            "label": "✅ ยืนยันตรวจแล้ว",
            "action": "confirm_review",
            "doc_id": doc_id,
            "data": f"action=confirm_review&doc_id={doc_id}",
        }
    ]

    if expense:
        merchant = expense.get("merchant") or "ร้านค้าไม่ระบุชื่อ"
        total = float(expense.get("total_amount") or 0.0)
        cat_label = _EXPENSE_CATEGORY_LABELS.get(expense.get("expense_category") or "other", "อื่นๆ")
        reimb_status = expense.get("reimbursement_status") or "pending_reimbursement"
        reimb_label = _REIMBURSEMENT_LABELS.get(reimb_status, "รอเบิกคืนโรงเรียน")
        exp_date = expense.get("expense_date") or "ไม่ระบุวันที่"

        header_title = "🧾 น้องโชคช่วยจดบิลให้แล้วครับคุณครู!"
        body_lines = [
            f"ร้านค้า: {merchant}",
            f"ยอดรวม: {total:,.2f} บาท",
            f"หมวด: {cat_label}",
            f"วันที่: {exp_date}",
            f"สถานะ: {reimb_label}",
        ]
        reply_text = (
            f"น้องโชคช่วยอ่านบิลให้เรียบร้อยแล้วครับคุณครู 🌤️\n"
            f"• ร้านค้า: {merchant}\n"
            f"• ยอดรวม: {total:,.2f} บาท ({cat_label})\n"
            f"• สถานะ: {reimb_label}\n"
            f"คุณครูกดยืนยันด้านล่าง หรือเปิดแก้บนโต๊ะทำงานได้เลยนะครับ"
        )
        if reimb_status == "pending_reimbursement":
            actions.append(
                {
                    "label": "💸 เบิกเงินคืนแล้ว",
                    "action": "mark_reimbursed",
                    "doc_id": doc_id,
                    "data": f"action=mark_reimbursed&doc_id={doc_id}",
                }
            )
    elif student:
        stu_name = student.get("full_name") or student.get("nickname") or "นักเรียนใหม่"
        nick = f" ({student['nickname']})" if student.get("nickname") else ""
        classroom = student.get("classroom") or "อนุบาล"
        allergy = student.get("allergies") or "ไม่มีระบุ"
        phone = student.get("parent_phone") or "-"

        header_title = "👶 น้องโชคบันทึกข้อมูลเด็กให้แล้วครับ!"
        body_lines = [
            f"ชื่อเด็ก: {stu_name}{nick}",
            f"ห้องเรียน: {classroom}",
            f"เบอร์ผู้ปกครอง: {phone}",
            f"แพ้อาหาร: {allergy}",
        ]
        reply_text = (
            f"น้องโชคช่วยเก็บประวัติ {stu_name}{nick} เข้าแฟ้มห้องเรียนแล้วครับ 🎒\n"
            f"• ห้อง: {classroom}\n"
            f"• เบอร์ผู้ปกครอง: {phone}\n"
            f"• แพ้อาหาร/ระวังพิเศษ: {allergy}"
        )
    else:
        snippet = (doc.get("text") or "").strip().replace("\n", " ")[:90]
        header_title = "📂 น้องโชคเก็บเข้าตู้เซฟเอกสารแล้วครับ!"
        body_lines = [
            f"ไฟล์: {filename}",
            f"หมวดหมู่: {doc.get('category') or 'general'}",
            f"ตัวอย่างข้อความ: {snippet or 'อ่านข้อความครบแล้ว'}",
        ]
        reply_text = (
            f"น้องโชคเก็บไฟล์ \"{filename}\" เข้าตู้เซฟเอกสารโรงเรียนให้แล้วครับ 📂\n"
            f"ต่อไปพิมพ์คำค้นหาในแชทนี้เพื่อเรียกดูได้ตลอดเลย ไม่ต้องกลัวไฟล์หมดอายุครับคุณครู!"
        )

    actions.append(
        {
            "label": "💻 เปิดดูบนโต๊ะทำงาน",
            "action": "open_doc",
            "doc_id": doc_id,
            "url": f"{base_url}/?doc={doc_id}" if base_url else f"/?doc={doc_id}",
        }
    )

    flex_buttons: List[Dict[str, Any]] = []
    for act in actions:
        if act.get("action") == "open_doc":
            if base_url.startswith("http"):
                flex_buttons.append(
                    {
                        "type": "button",
                        "style": "secondary",
                        "height": "sm",
                        "action": {"type": "uri", "label": act["label"][:20], "uri": act["url"]},
                    }
                )
        else:
            flex_buttons.append(
                {
                    "type": "button",
                    "style": "primary" if act["action"] == "confirm_review" else "secondary",
                    "color": "#2563EB" if act["action"] == "confirm_review" else None,
                    "height": "sm",
                    "action": {
                        "type": "postback",
                        "label": act["label"][:20],
                        "data": act["data"],
                    },
                }
            )

    # Clean None color fields for LINE API compliance
    for btn in flex_buttons:
        if btn.get("color") is None:
            btn.pop("color", None)

    flex_message = {
        "type": "flex",
        "altText": reply_text[:400],
        "contents": {
            "type": "bubble",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#EFF6FF",
                "contents": [
                    {
                        "type": "text",
                        "text": header_title,
                        "weight": "bold",
                        "size": "md",
                        "color": "#1E3A8A",
                        "wrap": True,
                    }
                ],
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "text",
                        "text": line,
                        "size": "sm",
                        "color": "#334155",
                        "wrap": True,
                    }
                    for line in body_lines
                ],
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": flex_buttons,
            },
        },
    }

    return {
        "reply_text": reply_text,
        "header_title": header_title,
        "body_lines": body_lines,
        "actions": actions,
        "flex_message": flex_message,
        "line_messages": [flex_message],
    }


def process_line_text_command(
    text: str,
    db_path: str,
    public_base_url: str = "",
) -> Dict[str, Any]:
    """Process teacher chat text commands or search queries sent to Nong ChokChuay LINE OA."""
    clean = (text or "").strip()
    lower = clean.lower()

    if not clean or any(kw in lower for kw in ("สวัสดี", "ช่วยด้วย", "เมนู", "help", "menu")):
        reply_text = (
            "สวัสดีครับคุณครู! น้องโชคช่วยพร้อมดูแลเอกสารให้แล้วครับ 🌤️\n"
            "• ส่งรูปใบเสร็จ / สลิป -> น้องโชคช่วยจดยอดรอเบิกให้\n"
            "• ส่งรูปสูติบัตร / ใบสมัครเด็ก -> เก็บชื่อ เลข 13 หลัก และป้ายเตือนแพ้อาหาร\n"
            "• ส่ง PDF คำสั่งโรงเรียน -> เก็บเข้าตู้เซฟค้นหาได้ตลอด\n"
            "• พิมพ์ \"ยอดรอเบิก\", \"แพ้อาหาร\", หรือพิมพ์คำที่ต้องการค้นหาได้เลยครับ"
        )
        return {
            "intent": "help",
            "reply_text": reply_text,
            "line_messages": [{"type": "text", "text": reply_text}],
            "actions": [],
        }

    if any(kw in lower for kw in ("ยอดรอเบิก", "สรุปยอด", "เบิกเงิน", "ค่าใช้จ่าย")):
        exp_data = database.list_expenses(db_path=db_path)
        summary = exp_data["summary"]
        stats = database.get_db_stats(db_path=db_path)
        pending_total = summary["pending_reimbursement_total"]
        pending_cnt = summary["pending_reimbursement_count"]
        reimb_total = summary["reimbursed_total"]
        pending_docs = stats["pending_review_count"]
        reply_text = (
            f"💸 สรุปยอดเงินของคุณครูตอนนี้ครับ:\n"
            f"• รอเบิกคืนโรงเรียน: {pending_total:,.2f} บาท ({pending_cnt} ใบ)\n"
            f"• เบิกคืนแล้ว: {reimb_total:,.2f} บาท\n"
            f"• เอกสารรอคุณครูกดตรวจ: {pending_docs} ฉบับ"
        )
        return {
            "intent": "reimbursement_summary",
            "reply_text": reply_text,
            "summary": summary,
            "line_messages": [{"type": "text", "text": reply_text}],
            "actions": [],
        }

    if any(kw in lower for kw in ("แพ้อาหาร", "รายชื่อเด็ก", "ข้อมูลเด็ก", "เด็กในห้อง")):
        stu_data = database.list_students(db_path=db_path)
        all_students = stu_data["students"]
        allergy_students = [s for s in all_students if s.get("allergies") or s.get("medical_notes")]
        lines = [
            f"👶 แฟ้มเด็กอนุบาลตอนนี้มี {stu_data['summary']['total_students']} คนครับ (ต้องระวังแพ้อาหาร/สุขภาพ {len(allergy_students)} คน)"
        ]
        for stu in allergy_students[:5]:
            nick = f" ({stu['nickname']})" if stu.get("nickname") else ""
            warn = " / ".join(p for p in (stu.get("allergies"), stu.get("medical_notes")) if p)
            phone = f" • โทร {stu['parent_phone']}" if stu.get("parent_phone") else ""
            lines.append(f"⚠️ {stu['full_name']}{nick} [{stu['classroom']}]: {warn}{phone}")
        if not allergy_students and all_students:
            for stu in all_students[:5]:
                nick = f" ({stu['nickname']})" if stu.get("nickname") else ""
                lines.append(f"• {stu['full_name']}{nick} [{stu['classroom']}]")
        reply_text = "\n".join(lines)
        return {
            "intent": "student_allergy_summary",
            "reply_text": reply_text,
            "students": all_students[:10],
            "line_messages": [{"type": "text", "text": reply_text}],
            "actions": [],
        }

    if any(kw in lower for kw in ("รอตรวจ", "ค้างตรวจ")):
        pending = database.search_documents(review_status="pending_review", limit=5, db_path=db_path)
        if not pending:
            reply_text = "เย้! ไม่มีเอกสารค้างตรวจแล้วครับคุณครู ไปพักผ่อนได้เลยครับ ✨"
        else:
            lines = [f"📥 มีเอกสารรอคุณครูตรวจยืนยัน {len(pending)} รายการล่าสุดครับ:"]
            for d in pending:
                lines.append(f"• {d['filename']} ({d['category']})")
            reply_text = "\n".join(lines)
        return {
            "intent": "pending_review_list",
            "reply_text": reply_text,
            "documents": pending,
            "line_messages": [{"type": "text", "text": reply_text}],
            "actions": [],
        }

    # Search fallback across documents and student profiles
    search_term = clean
    for prefix in ("ค้นหา ", "หา ", "search "):
        if lower.startswith(prefix):
            search_term = clean[len(prefix):].strip()
            break

    docs = database.search_documents(query=search_term, limit=5, db_path=db_path)
    stus = database.list_students(query=search_term, db_path=db_path)["students"][:3]

    if not docs and not stus:
        reply_text = f"น้องโชคลองค้นคำว่า \"{search_term}\" ในตู้เซฟและแฟ้มเด็กแล้ว ยังไม่พบข้อมูลครับคุณครู 🔍"
    else:
        lines = [f"🔍 น้องโชคค้นคำว่า \"{search_term}\" เจอข้อมูลตามนี้ครับ:"]
        for stu in stus:
            nick = f" ({stu['nickname']})" if stu.get("nickname") else ""
            lines.append(f"👶 {stu['full_name']}{nick} [{stu['classroom']}] โทร {stu.get('parent_phone') or '-'}")
        for doc in docs:
            snippet = (doc.get("snippet") or "").replace("\n", " ")[:70]
            lines.append(f"📄 {doc['filename']}: {snippet}...")
        reply_text = "\n".join(lines)

    return {
        "intent": "search",
        "query": search_term,
        "reply_text": reply_text,
        "documents": docs,
        "students": stus,
        "line_messages": [{"type": "text", "text": reply_text}],
        "actions": [],
    }


def handle_line_postback(data_str: str, db_path: str) -> Dict[str, Any]:
    """Handle LINE Postback / simulator button clicks such as confirm_review or mark_reimbursed."""
    parsed = parse_qs(data_str or "")
    action = (parsed.get("action") or [""])[0].strip()
    doc_id = (parsed.get("doc_id") or [""])[0].strip()

    if not doc_id:
        reply_text = "น้องโชคไม่พบรหัสเอกสารสำหรับทำรายการนี้ครับคุณครู"
        return {
            "success": False,
            "reply_text": reply_text,
            "line_messages": [{"type": "text", "text": reply_text}],
        }

    doc = database.get_document_by_id(doc_id, db_path=db_path)
    if not doc:
        reply_text = "ไม่พบเอกสารนี้ในฐานข้อมูลแล้วครับคุณครู"
        return {
            "success": False,
            "reply_text": reply_text,
            "line_messages": [{"type": "text", "text": reply_text}],
        }

    if action == "confirm_review":
        updated = database.update_document_metadata(
            doc_id=doc_id,
            review_status="reviewed",
            db_path=db_path,
        )
        reply_text = f"✅ น้องโชคยืนยันตรวจเอกสาร \"{doc['filename']}\" เรียบร้อยแล้วครับคุณครู!"
        return {
            "success": True,
            "action": action,
            "document": updated,
            "reply_text": reply_text,
            "line_messages": [{"type": "text", "text": reply_text}],
        }

    if action == "mark_reimbursed":
        database.update_document_metadata(doc_id=doc_id, review_status="reviewed", db_path=db_path)
        exp = doc.get("expense")
        if exp:
            updated_exp = database.upsert_expense(
                document_id=doc_id,
                merchant=exp["merchant"],
                expense_date=exp["expense_date"],
                subtotal=exp["subtotal"],
                vat_amount=exp["vat_amount"],
                total_amount=exp["total_amount"],
                currency=exp["currency"],
                expense_category=exp["expense_category"],
                extraction_method=exp["extraction_method"],
                reimbursement_status="reimbursed",
                budget_source=exp.get("budget_source") or "classroom",
                raw_json=exp.get("raw_json"),
                db_path=db_path,
            )
            reply_text = (
                f"💸 บันทึกว่าเบิกเงินคืนจากโรงเรียนแล้ว ({updated_exp['total_amount']:,.2f} บาท) "
                f"สำหรับบิล {updated_exp['merchant'] or doc['filename']} ครับคุณครู!"
            )
            return {
                "success": True,
                "action": action,
                "expense": updated_exp,
                "reply_text": reply_text,
                "line_messages": [{"type": "text", "text": reply_text}],
            }

    reply_text = f"น้องโชคได้รับคำสั่ง ({action}) แล้วครับคุณครู"
    return {
        "success": True,
        "action": action,
        "reply_text": reply_text,
        "line_messages": [{"type": "text", "text": reply_text}],
    }
