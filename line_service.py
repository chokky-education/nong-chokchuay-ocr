import base64
import hashlib
import hmac
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

import requests

import database
from config import AppConfig, DEFAULT_GEMINI_MODEL, resolve_llm_provider_and_model
from extractor import extract_gemini_text

DEFAULT_TEACHER_NAME = "ครูพิ้งกี้"

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


def _resolve_teacher_name(
    config: Optional[AppConfig] = None,
    teacher_name: Optional[str] = None,
) -> str:
    candidate = (teacher_name or "").strip()
    if candidate:
        return candidate
    if config is not None and (getattr(config, "teacher_name", "") or "").strip():
        return config.teacher_name.strip()
    return DEFAULT_TEACHER_NAME


def build_nong_chok_system_instruction(teacher_name: str = DEFAULT_TEACHER_NAME) -> str:
    """Build natural Thai kindergarten teacher sidekick systemInstruction for Nong ChokChuay."""
    target = (teacher_name or DEFAULT_TEACHER_NAME).strip() or DEFAULT_TEACHER_NAME
    return (
        f'คุณคือ "น้องโชค" (จากระบบน้องโชคช่วย) ผู้ช่วยครูรุ่นน้องคู่ใจประจำห้องอนุบาลของ "{target}"\n'
        "บุคลิกของคุณคือลูกมือที่คล่องแคล่ว อบอุ่น ช่างสังเกต เข้าใจธรรมชาติเด็กปฐมวัย (3-6 ขวบ) "
        "และเข้าใจดีว่างานครูอนุบาลไทยยุ่งแค่ไหน ทั้งดูแลเด็กหน้าห้อง เตรียมสื่อ ประเมินพัฒนาการ 4 ด้าน "
        "ทำบิลเบิกโรงเรียน และคอยตอบ LINE ผู้ปกครอง\n\n"
        "แนวทางการคุยใน LINE:\n"
        '- คุยเป็นภาษาไทยธรรมชาติเหมือนผู้ช่วยรุ่นน้องคุยกับครูพี่เลี้ยง แทนตัวเองว่า "น้องโชค" '
        'และใช้หางเสียงนุ่มนวลหลากหลายตามจังหวะประโยค เช่น "ครับ", "นะครับ", "ได้เลยครับ"\n'
        f'- ห้ามขึ้นต้นด้วย "สวัสดีครับ{target}" ซ้ำๆ ทุกข้อความ ให้ทักสวัสดีเฉพาะตอนที่ครูทักทายเปิดบทสนทนาเท่านั้น '
        f'นอกนั้นตอบเข้าประเด็นอย่างเป็นกันเอง และเรียกชื่อ "{target}" แทรกตามจังหวะธรรมชาติ\n'
        "- หากครูถามเรื่องยอดเงินสำรองจ่าย เอกสารในตู้เซฟ รายชื่อเด็ก หรือข้อมูลแพ้อาหาร/โรคประจำตัว "
        "ต้องยึดข้อมูลจาก CONTEXT เท่านั้น ห้ามเดาหรือแต่งตัวเลขและชื่อเด็กขึ้นเองเด็ดขาด "
        "(เรื่องแพ้อาหารต้องระบุให้ชัดและช่วยเตือนความเสี่ยงเสมอ) ถ้ายังไม่มีข้อมูลในระบบ "
        "ให้บอกตรงๆ อย่างนุ่มนวลและแนะให้ส่งรูปบิลหรือใบสมัครเข้ามาในแชทได้\n"
        "- หากครูชวนคุย ระบายความเหนื่อย ขอให้ช่วยร่างข้อความแจ้งผู้ปกครองในกลุ่ม LINE "
        "หรือขอไอเดียกิจกรรม นิทาน เพลงสั้นๆ สำหรับเด็กอนุบาล ให้ช่วยคิดและเกลาคำพูดอย่างเข้าใจหัวอกครูได้ทันทีโดยไม่ต้องจำกัดแค่ใน CONTEXT\n"
        "- จัดข้อความสั้นกระชับ อ่านกวาดตาบนจอมือถือได้ในไม่กี่วินาที (ประมาณ 2-5 บรรทัด) "
        "และห้ามใช้ศัพท์เทคนิคอย่างคำว่า CONTEXT, OCR, ฐานข้อมูล หรือระบบ AI"
    )


def _build_teacher_db_context(db_path: str, search_query: str = "") -> str:
    """Collect live Vault, Expense Reimbursement, and Kindergarten Student summary for Gemini context."""
    stats = database.get_db_stats(db_path=db_path)
    exp_data = database.list_expenses(db_path=db_path)
    exp_sum = exp_data["summary"]
    stu_data = database.list_students(db_path=db_path)
    students = stu_data["students"]
    pending_docs = database.search_documents(review_status="pending_review", limit=5, db_path=db_path)

    lines = [
        f"- เอกสารทั้งหมดในตู้เซฟ: {stats['total_documents']} ฉบับ (รอตรวจยืนยัน: {stats['pending_review_count']} ฉบับ)",
        f"- ยอดเงินสำรองจ่ายรอเบิกคืนโรงเรียน: {exp_sum['pending_reimbursement_total']:,.2f} บาท ({exp_sum['pending_reimbursement_count']} ใบ)",
        f"- ยอดที่เบิกคืนแล้ว: {exp_sum['reimbursed_total']:,.2f} บาท",
        f"- นักเรียนในแฟ้ม: {stu_data['summary']['total_students']} คน (แพ้อาหาร/ระวังพิเศษ: {stu_data['summary']['allergy_count']} คน)",
    ]

    if pending_docs:
        lines.append("เอกสารรอตรวจล่าสุด:")
        for doc in pending_docs[:5]:
            lines.append(f"  * {doc['filename']} (หมวด {doc['category']})")

    allergy_students = [s for s in students if s.get("allergies") or s.get("medical_notes")]
    if allergy_students:
        lines.append("รายชื่อเด็กที่ต้องระวังแพ้อาหาร/สุขภาพ:")
        for stu in allergy_students[:8]:
            nick = f" ({stu['nickname']})" if stu.get("nickname") else ""
            warn = " / ".join(p for p in (stu.get("allergies"), stu.get("medical_notes")) if p)
            phone = f" โทร {stu['parent_phone']}" if stu.get("parent_phone") else ""
            lines.append(f"  * {stu['full_name']}{nick} [{stu['classroom']}]: {warn}{phone}")

    clean_q = (search_query or "").strip()
    if clean_q and len(clean_q) >= 2:
        matched_docs = database.search_documents(query=clean_q, limit=3, db_path=db_path)
        if matched_docs:
            lines.append(f'ผลการค้นหาเอกสารสำหรับ "{clean_q}":')
            for d in matched_docs:
                snippet = (d.get("snippet") or "").replace("\n", " ")[:90]
                lines.append(f"  * {d['filename']}: {snippet}")

    return "\n".join(lines)


def generate_nong_chok_ai_reply(
    user_text: str,
    db_context: str,
    config: Optional[AppConfig] = None,
    teacher_name: Optional[str] = None,
) -> Optional[str]:
    """Generate a warm Gemini Flash reply using Nong Chok's systemInstruction."""
    if config is None:
        return None
    provider, model = resolve_llm_provider_and_model(config.llm_provider, config.llm_model)
    api_key = (config.llm_api_key or "").strip()
    if not api_key and not (provider != "gemini" and (config.llm_base_url or "").strip()):
        return None

    target_teacher = _resolve_teacher_name(config, teacher_name)
    system_instruction = build_nong_chok_system_instruction(target_teacher)
    user_prompt = f"CONTEXT:\n{db_context}\n\nUSER_MESSAGE:\n{user_text}"

    try:
        if provider == "gemini":
            base = (config.llm_base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
            url = f"{base}/models/{model}:generateContent"
            resp = requests.post(
                url,
                headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                json={
                    "systemInstruction": {"parts": [{"text": system_instruction}]},
                    "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                    "generationConfig": {
                        "temperature": 0.65,
                        "maxOutputTokens": 2048,
                    },
                },
                timeout=20,
            )
            resp.raise_for_status()
            payload = resp.json()
            reply = extract_gemini_text(payload).strip()
        else:
            base = (config.llm_base_url or "https://api.openai.com/v1").rstrip("/")
            url = f"{base}/chat/completions"
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            resp = requests.post(
                url,
                headers=headers,
                json={
                    "model": model,
                    "temperature": 0.65,
                    "messages": [
                        {"role": "system", "content": system_instruction},
                        {"role": "user", "content": user_prompt},
                    ],
                },
                timeout=20,
            )
            resp.raise_for_status()
            payload = resp.json()
            reply = payload["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        print(f"[LINE AI] Gemini/LLM chat reply fallback: {exc}")
        return None

    if not reply:
        return None
    return reply[:1800]


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


def authorize_line_event_source(source: Any, config: AppConfig) -> Tuple[bool, str]:
    """Check whether a LINE webhook event source is permitted under the active auth policy."""
    src = source if isinstance(source, dict) else {}
    source_type = (src.get("type") or "").strip().lower()
    user_id = (src.get("userId") or "").strip()
    allowed_users = {uid.strip() for uid in (config.allowed_line_user_ids or ()) if uid and uid.strip()}

    if config.auth_required:
        allowed_source_types = {"user"}
        if config.line_allow_group_chats:
            allowed_source_types.update({"group", "room"})
        if source_type not in allowed_source_types:
            return (
                False,
                "ขออภัยครับ เพื่อความปลอดภัยของข้อมูลเด็กและเอกสาร "
                "น้องโชคช่วยรับคำสั่งเฉพาะในแชทส่วนตัวกับคุณครูที่ได้รับอนุญาตเท่านั้นครับ 🔒",
            )
        if not user_id or user_id not in allowed_users:
            return False, "ขออภัยครับ บัญชี LINE นี้ยังไม่ได้รับอนุญาตให้เข้าถึงระบบนี้ครับ 🙏"
        return True, ""

    if source_type in ("group", "room") and not config.line_allow_group_chats:
        return (
            False,
            "ขออภัยครับ ระบบปิดการใช้งานในแชทกลุ่มไว้ครับ 🔒",
        )
    if allowed_users and user_id not in allowed_users:
        return False, "ขออภัยครับ บัญชี LINE นี้ยังไม่ได้รับอนุญาตให้เข้าถึงระบบนี้ครับ 🙏"
    return True, ""


def append_external_browser_param(url: str) -> str:
    """Append openExternalBrowser=1 to http(s) URLs so LINE opens links in Safari/Chrome for OAuth."""
    raw = (url or "").strip()
    if not raw or not raw.lower().startswith(("http://", "https://")):
        return raw
    parts = urlparse(raw)
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    if not any(key == "openExternalBrowser" for key, _ in query_pairs):
        query_pairs.append(("openExternalBrowser", "1"))
    return urlunparse(parts._replace(query=urlencode(query_pairs)))


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
    config: Optional[AppConfig] = None,
    teacher_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Build Nong Chok's warm reply text, LINE Flex Message, and interactive postback actions."""
    target_teacher = _resolve_teacher_name(config, teacher_name)
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

        header_title = f"🧾 น้องโชคช่วยจดบิลให้{target_teacher}แล้วครับ!"
        body_lines = [
            f"ร้านค้า: {merchant}",
            f"ยอดรวม: {total:,.2f} บาท",
            f"หมวด: {cat_label}",
            f"วันที่: {exp_date}",
            f"สถานะ: {reimb_label}",
        ]
        reply_text = (
            f"น้องโชคช่วยอ่านบิลให้เรียบร้อยแล้วครับ{target_teacher} 🌤️\n"
            f"• ร้านค้า: {merchant}\n"
            f"• ยอดรวม: {total:,.2f} บาท ({cat_label})\n"
            f"• สถานะ: {reimb_label}\n"
            f"{target_teacher}กดยืนยันด้านล่าง หรือเปิดแก้บนโต๊ะทำงานได้เลยนะครับ"
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

        header_title = f"👶 น้องโชคบันทึกข้อมูลเด็กให้{target_teacher}แล้วครับ!"
        body_lines = [
            f"ชื่อเด็ก: {stu_name}{nick}",
            f"ห้องเรียน: {classroom}",
            f"เบอร์ผู้ปกครอง: {phone}",
            f"แพ้อาหาร: {allergy}",
        ]
        reply_text = (
            f"น้องโชคช่วยเก็บประวัติ {stu_name}{nick} ให้{target_teacher}เข้าแฟ้มห้องเรียนแล้วครับ 🎒\n"
            f"• ห้อง: {classroom}\n"
            f"• เบอร์ผู้ปกครอง: {phone}\n"
            f"• แพ้อาหาร/ระวังพิเศษ: {allergy}"
        )
    else:
        snippet = (doc.get("text") or "").strip().replace("\n", " ")[:90]
        header_title = f"📂 น้องโชคเก็บเข้าตู้เซฟให้{target_teacher}แล้วครับ!"
        body_lines = [
            f"ไฟล์: {filename}",
            f"หมวดหมู่: {doc.get('category') or 'general'}",
            f"ตัวอย่างข้อความ: {snippet or 'อ่านข้อความครบแล้ว'}",
        ]
        reply_text = (
            f'น้องโชคเก็บไฟล์ "{filename}" เข้าตู้เซฟเอกสารให้{target_teacher}แล้วครับ 📂\n'
            f"ต่อไป{target_teacher}พิมพ์คำค้นหาในแชทนี้เพื่อเรียกดูได้ตลอดเลย ไม่ต้องกลัวไฟล์หมดอายุครับ!"
        )

    actions.append(
        {
            "label": "💻 เปิดดูบนโต๊ะทำงาน",
            "action": "open_doc",
            "doc_id": doc_id,
            "url": append_external_browser_param(f"{base_url}/?doc={doc_id}") if base_url else f"/?doc={doc_id}",
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
    config: Optional[AppConfig] = None,
    teacher_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Process teacher chat text commands, Gemini Flash assistant chat, or vault searches."""
    target_teacher = _resolve_teacher_name(config, teacher_name)
    clean = (text or "").strip()
    lower = clean.lower()

    # Exact short command shortcuts for instant zero-latency Rich Menu & ledger queries
    if lower in {"ยอดรอเบิก", "สรุปยอด", "เบิกเงิน", "ค่าใช้จ่าย"}:
        exp_data = database.list_expenses(db_path=db_path)
        summary = exp_data["summary"]
        stats = database.get_db_stats(db_path=db_path)
        pending_total = summary["pending_reimbursement_total"]
        pending_cnt = summary["pending_reimbursement_count"]
        reimb_total = summary["reimbursed_total"]
        pending_docs = stats["pending_review_count"]
        reply_text = (
            f"💸 สรุปยอดเงินของ{target_teacher}ตอนนี้ครับ:\n"
            f"• รอเบิกคืนโรงเรียน: {pending_total:,.2f} บาท ({pending_cnt} ใบ)\n"
            f"• เบิกคืนแล้ว: {reimb_total:,.2f} บาท\n"
            f"• เอกสารรอ{target_teacher}กดตรวจ: {pending_docs} ฉบับ"
        )
        return {
            "intent": "reimbursement_summary",
            "reply_text": reply_text,
            "summary": summary,
            "line_messages": [{"type": "text", "text": reply_text}],
            "actions": [],
        }

    if lower in {"แพ้อาหาร", "รายชื่อเด็ก", "ข้อมูลเด็ก", "เด็กในห้อง"}:
        stu_data = database.list_students(db_path=db_path)
        all_students = stu_data["students"]
        allergy_students = [s for s in all_students if s.get("allergies") or s.get("medical_notes")]
        lines = [
            f"👶 แฟ้มเด็กอนุบาลของ{target_teacher}ตอนนี้มี {stu_data['summary']['total_students']} คนครับ "
            f"(ต้องระวังแพ้อาหาร/สุขภาพ {len(allergy_students)} คน)"
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

    if lower in {"รอตรวจ", "ค้างตรวจ"}:
        pending = database.search_documents(review_status="pending_review", limit=5, db_path=db_path)
        if not pending:
            reply_text = f"เย้! ไม่มีเอกสารค้างตรวจแล้วครับ{target_teacher} พักผ่อนได้เลยนะครับ ✨"
        else:
            lines = [f"📥 มีเอกสารรอ{target_teacher}ตรวจยืนยัน {len(pending)} รายการล่าสุดครับ:"]
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

    # Explicit search prefix goes directly to FTS5 + student roster lookup
    is_explicit_search = lower.startswith(("ค้นหา ", "หา ", "search "))
    if not is_explicit_search:
        ai_reply = generate_nong_chok_ai_reply(
            user_text=clean or "สวัสดี",
            db_context=_build_teacher_db_context(db_path=db_path, search_query=clean),
            config=config,
            teacher_name=target_teacher,
        )
        if ai_reply:
            resolved_model = (
                resolve_llm_provider_and_model(config.llm_provider, config.llm_model)[1]
                if config
                else DEFAULT_GEMINI_MODEL
            )
            return {
                "intent": "ai_chat",
                "model": resolved_model,
                "reply_text": ai_reply,
                "line_messages": [{"type": "text", "text": ai_reply}],
                "actions": [],
            }

    if not clean or any(kw in lower for kw in ("สวัสดี", "ช่วยด้วย", "เมนู", "help", "menu")):
        reply_text = (
            f"สวัสดีครับ{target_teacher}! น้องโชคช่วยพร้อมดูแลเอกสารให้แล้วครับ 🌤️\n"
            "• ส่งรูปใบเสร็จ / สลิป -> น้องโชคช่วยจดยอดรอเบิกให้\n"
            "• ส่งรูปสูติบัตร / ใบสมัครเด็ก -> เก็บชื่อ เลข 13 หลัก และป้ายเตือนแพ้อาหาร\n"
            "• ส่ง PDF คำสั่งโรงเรียน -> เก็บเข้าตู้เซฟค้นหาได้ตลอด\n"
            '• พิมพ์ "ยอดรอเบิก", "แพ้อาหาร", หรือพิมพ์คุย/ค้นหาได้เลยครับ'
        )
        return {
            "intent": "help",
            "reply_text": reply_text,
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
        reply_text = (
            f'น้องโชคลองค้นคำว่า "{search_term}" ในตู้เซฟและแฟ้มเด็กให้{target_teacher}แล้ว '
            "ยังไม่พบข้อมูลครับ 🔍"
        )
    else:
        lines = [f'🔍 น้องโชคค้นคำว่า "{search_term}" เจอข้อมูลให้{target_teacher}ตามนี้ครับ:']
        for stu in stus:
            nick = f" ({stu['nickname']})" if stu.get("nickname") else ""
            lines.append(f"👶 {stu['full_name']}{nick} [{stu['classroom']}] โทร {stu.get('parent_phone') or '-'}")
            if stu.get("allergies"):
                lines.append(f"   ⚠️ แพ้อาหาร: {stu['allergies']}")
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


def handle_line_postback(
    data_str: str,
    db_path: str,
    config: Optional[AppConfig] = None,
    teacher_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Handle LINE Postback / simulator button clicks such as confirm_review or mark_reimbursed."""
    target_teacher = _resolve_teacher_name(config, teacher_name)
    parsed = parse_qs(data_str or "")
    action = (parsed.get("action") or [""])[0].strip()
    doc_id = (parsed.get("doc_id") or [""])[0].strip()

    if not doc_id:
        reply_text = f"น้องโชคไม่พบรหัสเอกสารสำหรับทำรายการนี้ครับ{target_teacher}"
        return {
            "success": False,
            "reply_text": reply_text,
            "line_messages": [{"type": "text", "text": reply_text}],
        }

    doc = database.get_document_by_id(doc_id, db_path=db_path)
    if not doc:
        reply_text = f"ไม่พบเอกสารนี้ในฐานข้อมูลแล้วครับ{target_teacher}"
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
        reply_text = f'✅ น้องโชคยืนยันตรวจเอกสาร "{doc["filename"]}" เรียบร้อยแล้วครับ{target_teacher}!'
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
                f"สำหรับบิล {updated_exp['merchant'] or doc['filename']} ครับ{target_teacher}!"
            )
            return {
                "success": True,
                "action": action,
                "expense": updated_exp,
                "reply_text": reply_text,
                "line_messages": [{"type": "text", "text": reply_text}],
            }

    reply_text = f"น้องโชคได้รับคำสั่ง ({action}) แล้วครับ{target_teacher}"
    return {
        "success": True,
        "action": action,
        "reply_text": reply_text,
        "line_messages": [{"type": "text", "text": reply_text}],
    }
