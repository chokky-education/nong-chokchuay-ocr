import base64
import hashlib
import hmac
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

import requests

import database
from config import AppConfig, DEFAULT_GEMINI_MODEL, resolve_llm_provider_and_model
from extractor import extract_gemini_text
from storage_service import VaultStorageCoordinator

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

_MASCOT_CARD_ASSETS = {
    "avatar": "/static/mascot_cards/avatar.png",
    "receipt": "/static/mascot_cards/hero_receipt.png",
    "confirmed": "/static/mascot_cards/hero_confirmed.png",
    "student": "/static/mascot_cards/hero_student.png",
    "pending": "/static/mascot_cards/hero_pending.png",
    "summary": "/static/mascot_cards/hero_summary.png",
    "allergy": "/static/mascot_cards/hero_allergy.png",
    "search": "/static/mascot_cards/hero_search.png",
    "menu": "/static/mascot_cards/hero_menu.png",
}

_CARD_THEMES = {
    "receipt": {"bg": "#DDF5EB", "header_bg": "#ECFDF5", "header_text": "#065F46", "accent": "#059669"},
    "student": {"bg": "#F4E5FA", "header_bg": "#FAF5FF", "header_text": "#6B21A8", "accent": "#7C3AED"},
    "pending": {"bg": "#DFF0FC", "header_bg": "#EFF6FF", "header_text": "#1E3A8A", "accent": "#2563EB"},
    "summary": {"bg": "#FDF1CE", "header_bg": "#FFFBEB", "header_text": "#92400E", "accent": "#D97706"},
    "allergy": {"bg": "#FCE3E5", "header_bg": "#FFF1F2", "header_text": "#9F1239", "accent": "#E11D48"},
    "search": {"bg": "#DBF4F5", "header_bg": "#F0FDFA", "header_text": "#115E59", "accent": "#0D9488"},
    "menu": {"bg": "#EDE5FA", "header_bg": "#F5F3FF", "header_text": "#5B21B6", "accent": "#7C3AED"},
    "document": {"bg": "#EFF6FF", "header_bg": "#EFF6FF", "header_text": "#1E3A8A", "accent": "#2563EB"},
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


def _mascot_path(asset_key: str) -> str:
    return _MASCOT_CARD_ASSETS.get(asset_key, _MASCOT_CARD_ASSETS["menu"])


def _maybe_hero(
    asset_key: str,
    public_base_url: str = "",
    aspect_ratio: str = "16:10",
    background_color: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    base_url = (public_base_url or "").strip().rstrip("/")
    if not base_url.lower().startswith("https://"):
        return None
    hero: Dict[str, Any] = {
        "type": "image",
        "url": f"{base_url}{_mascot_path(asset_key)}",
        "size": "full",
        "aspectRatio": aspect_ratio,
        "aspectMode": "fit",
    }
    if background_color:
        hero["backgroundColor"] = background_color
    return hero


def _truncate_line_label(label: str, limit: int = 20) -> str:
    clean = " ".join((label or "").split()).strip()
    if not clean:
        return "กดเลือก"
    if len(clean) <= limit:
        return clean
    if "(" in clean and clean.endswith(")"):
        head, tail = clean.rsplit("(", 1)
        suffix = f"({tail}"
        if len(suffix) <= 7 and limit - len(suffix) - 1 >= 4:
            prefix = head.rstrip()[: limit - len(suffix) - 1].rstrip(" (")
            return f"{prefix}…{suffix}"[:limit]
        inner_cut = clean[: max(1, limit - 2)].rstrip(" (")
        if "(" in inner_cut:
            return f"{inner_cut}…)"[:limit]
    trimmed = clean[: max(1, limit - 1)].rstrip(" (")
    return f"{trimmed}…"[:limit]


def _encode_postback(params: Dict[str, str]) -> str:
    return urlencode({k: v for k, v in params.items() if v is not None})[:300]


def _build_quick_replies(pairs: List[Tuple[str, str]]) -> List[Dict[str, str]]:
    return [{"label": label, "text": cmd} for label, cmd in pairs if label and cmd]


def _attach_line_quick_reply(
    line_messages: List[Dict[str, Any]],
    quick_replies: List[Dict[str, str]],
) -> List[Dict[str, Any]]:
    if not line_messages or not quick_replies:
        return line_messages
    items = [
        {
            "type": "action",
            "action": {
                "type": "message",
                "label": _truncate_line_label(item["label"], 20),
                "text": (item["text"] or "")[:300],
            },
        }
        for item in quick_replies[:13]
        if (item.get("text") or "").strip()
    ]
    if not items:
        return line_messages
    last = dict(line_messages[-1])
    last["quickReply"] = {"items": items}
    return [*line_messages[:-1], last]


def _digits_only(phone: str) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit())


def _format_tel_uri(phone: str) -> str:
    raw = (phone or "").strip()
    digits = _digits_only(raw)
    if len(digits) < 3:
        return ""
    prefix = "+" if raw.startswith("+") else ""
    return f"tel:{prefix}{digits}"


def _build_flex_button(
    label: str,
    action_obj: Dict[str, Any],
    style: str = "secondary",
    color: Optional[str] = None,
) -> Dict[str, Any]:
    btn: Dict[str, Any] = {
        "type": "button",
        "style": style,
        "height": "sm",
        "action": {**action_obj, "label": _truncate_line_label(label, 20)},
    }
    if color:
        btn["color"] = color
    return btn


def _assemble_bubble(
    theme_key: str,
    header_title: str,
    body_contents: List[Dict[str, Any]],
    footer_buttons: Optional[List[Dict[str, Any]]] = None,
    hero_asset: Optional[str] = None,
    public_base_url: str = "",
) -> Dict[str, Any]:
    theme = _CARD_THEMES.get(theme_key, _CARD_THEMES["document"])
    bubble: Dict[str, Any] = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": theme["header_bg"],
            "contents": [
                {
                    "type": "text",
                    "text": header_title,
                    "weight": "bold",
                    "size": "md",
                    "color": theme["header_text"],
                    "wrap": True,
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": body_contents,
        },
    }
    if hero_asset:
        hero = _maybe_hero(
            hero_asset,
            public_base_url=public_base_url,
            background_color=theme["bg"],
        )
        if hero:
            bubble["hero"] = hero
    if footer_buttons:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": footer_buttons,
        }
    return bubble


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


def _build_teacher_db_context(
    db_path: str,
    search_query: str = "",
    r2_enabled: bool = False,
) -> str:
    """Collect live Vault, Expense Reimbursement, and Kindergarten Student summary for Gemini context."""
    stats = database.get_db_stats(db_path=db_path)
    exp_data = database.list_expenses(db_path=db_path)
    exp_sum = exp_data["summary"]
    stu_data = database.list_students(db_path=db_path)
    students = stu_data["students"]
    pending_docs = database.search_documents(
        review_status="pending_review",
        limit=5,
        db_path=db_path,
        r2_enabled=r2_enabled,
    )

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
        matched_docs = database.search_documents(
            query=clean_q,
            limit=3,
            db_path=db_path,
            r2_enabled=r2_enabled,
        )
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
    """Generate a warm Gemini / OpenAI-compatible reply using Nong Chok's systemInstruction."""
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


def start_line_loading(user_id: str, access_token: str) -> bool:
    """Request a short-lived typing indicator without affecting message processing."""
    if not user_id or not access_token:
        return False
    try:
        response = requests.post(
            "https://api.line.me/v2/bot/chat/loading/start",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"chatId": user_id, "loadingSeconds": 60},
            timeout=(1, 1),
        )
        if response.status_code != 202:
            print(f"[LINE] Loading indicator failed: HTTP {response.status_code}")
            return False
        return True
    except requests.RequestException as exc:
        print(f"[LINE] Loading indicator failed: {type(exc).__name__}")
        return False


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
    """Build Nong Chok's warm Mascot Flex Card, reply text, Quick Replies, and actions."""
    target_teacher = _resolve_teacher_name(config, teacher_name)
    doc_id = doc.get("doc_id") or doc.get("id") or ""
    filename = doc.get("filename") or "เอกสารใหม่"
    base_url = (public_base_url or "").strip().rstrip("/")
    open_doc_url = (
        append_external_browser_param(f"{base_url}/?doc={doc_id}")
        if base_url
        else f"/?doc={doc_id}"
    )

    actions: List[Dict[str, str]] = [
        {
            "label": "✓ ยืนยันยอดถูก" if expense else "✅ ยืนยันข้อมูล",
            "action": "confirm_review",
            "doc_id": doc_id,
            "data": _encode_postback({"action": "confirm_review", "doc_id": doc_id}),
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
                    "label": "💸 เบิกคืนแล้ว",
                    "action": "mark_reimbursed",
                    "doc_id": doc_id,
                    "data": _encode_postback({"action": "mark_reimbursed", "doc_id": doc_id}),
                }
            )
        actions.append(
            {
                "label": "💻 เปิดดูบนโต๊ะทำงาน",
                "action": "open_doc",
                "doc_id": doc_id,
                "url": open_doc_url,
            }
        )

        body_contents = [
            {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#ECFDF5",
                "cornerRadius": "md",
                "paddingAll": "md",
                "contents": [
                    {"type": "text", "text": f"ยอดรวม ({merchant})", "size": "xs", "color": "#475569", "align": "center", "wrap": True},
                    {"type": "text", "text": f"฿ {total:,.2f}", "size": "xxl", "weight": "bold", "color": "#065F46", "align": "center"},
                ],
            },
            {"type": "text", "text": f"หมวด: {cat_label}", "size": "sm", "color": "#334155", "wrap": True},
            {"type": "text", "text": f"วันที่: {exp_date} • สถานะ: {reimb_label}", "size": "xs", "color": "#64748B", "wrap": True},
        ]
        theme_key = "receipt"
        hero_asset = "receipt"
        card_view: Dict[str, Any] = {
            "kind": "receipt",
            "theme": "receipt",
            "step_num": "1",
            "badge_title": "ส่งรูปใบเสร็จ / สลิป",
            "mascot_image": _mascot_path("receipt"),
            "confirmed_mascot_image": _mascot_path("confirmed"),
            "doc_id": doc_id,
            "merchant": merchant,
            "total_amount": total,
            "category_label": cat_label,
            "expense_date": exp_date,
            "reimbursement_status": reimb_status,
            "reimbursement_label": reimb_label,
        }
        quick_replies = _build_quick_replies([
            ("💸 ยอดรอเบิก", "ยอดรอเบิก"),
            ("📥 รอตรวจ", "รอตรวจ"),
            ("📋 เมนู", "เมนู"),
        ])

    elif student:
        stu_name = student.get("full_name") or student.get("nickname") or "นักเรียนใหม่"
        nick = f" ({student['nickname']})" if student.get("nickname") else ""
        classroom = student.get("classroom") or "อนุบาล"
        allergy = (student.get("allergies") or "").strip()
        medical = (student.get("medical_notes") or "").strip()
        warn_text = " / ".join(p for p in (allergy, medical) if p) or "ไม่มีระบุ"
        has_allergy = bool(allergy or medical)
        phone = (student.get("parent_phone") or "").strip() or "-"

        header_title = f"👶 น้องโชคบันทึกข้อมูลเด็กให้{target_teacher}แล้วครับ!"
        body_lines = [
            f"ชื่อเด็ก: {stu_name}{nick}",
            f"ห้องเรียน: {classroom}",
            f"เบอร์ผู้ปกครอง: {phone}",
            f"แพ้อาหาร: {warn_text}",
        ]
        reply_text = (
            f"น้องโชคช่วยเก็บประวัติ {stu_name}{nick} ให้{target_teacher}เข้าแฟ้มห้องเรียนแล้วครับ 🎒\n"
            f"• ห้อง: {classroom}\n"
            f"• เบอร์ผู้ปกครอง: {phone}\n"
            f"• แพ้อาหาร/ระวังพิเศษ: {warn_text}"
        )
        tel_uri = _format_tel_uri(phone)
        if tel_uri:
            actions.append(
                {
                    "label": "📞 โทรผู้ปกครอง",
                    "action": "call_parent",
                    "doc_id": doc_id,
                    "phone": phone,
                    "url": tel_uri,
                }
            )
        actions.append(
            {
                "label": "💻 เปิดดูบนโต๊ะทำงาน",
                "action": "open_doc",
                "doc_id": doc_id,
                "url": open_doc_url,
            }
        )

        body_contents = [
            {"type": "text", "text": f"{stu_name}{nick} • {classroom}", "size": "md", "weight": "bold", "color": "#1E293B", "wrap": True},
        ]
        if has_allergy:
            body_contents.append(
                {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#FFF1F2",
                    "cornerRadius": "md",
                    "paddingAll": "sm",
                    "contents": [
                        {"type": "text", "text": f"⚠️ แพ้อาหาร/ระวังพิเศษ: {warn_text}", "size": "sm", "weight": "bold", "color": "#BE123C", "wrap": True},
                    ],
                }
            )
        else:
            body_contents.append({"type": "text", "text": "แพ้อาหาร: ไม่มีระบุ", "size": "sm", "color": "#475569"})
        body_contents.append({"type": "text", "text": f"📞 เบอร์ผู้ปกครอง: {phone}", "size": "sm", "color": "#334155"})

        theme_key = "student"
        hero_asset = "student"
        card_view = {
            "kind": "student",
            "theme": "student",
            "step_num": "2",
            "badge_title": "ส่งสูติบัตร / ใบสมัครเด็ก",
            "mascot_image": _mascot_path("student"),
            "doc_id": doc_id,
            "full_name": stu_name,
            "nickname": student.get("nickname") or "",
            "classroom": classroom,
            "parent_phone": phone,
            "warning_text": warn_text,
            "has_allergy": has_allergy,
        }
        quick_replies = _build_quick_replies([
            ("⚠️ แพ้อาหารหน้าห้อง", "แพ้อาหาร"),
            ("📥 รอตรวจ", "รอตรวจ"),
            ("📋 เมนู", "เมนู"),
        ])

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
                "url": open_doc_url,
            }
        )
        body_contents = [
            {"type": "text", "text": line, "size": "sm", "color": "#334155", "wrap": True}
            for line in body_lines
        ]
        theme_key = "document"
        hero_asset = "confirmed"
        card_view = {
            "kind": "document",
            "theme": "document",
            "step_num": "📄",
            "badge_title": "เก็บเข้าตู้เซฟเอกสาร",
            "mascot_image": _mascot_path("confirmed"),
            "doc_id": doc_id,
            "filename": filename,
            "category": doc.get("category") or "general",
            "snippet": snippet or "อ่านข้อความครบแล้ว",
        }
        quick_replies = _build_quick_replies([
            ("📥 รอตรวจ", "รอตรวจ"),
            ("💸 ยอดรอเบิก", "ยอดรอเบิก"),
            ("📋 เมนู", "เมนู"),
        ])

    flex_buttons: List[Dict[str, Any]] = []
    for act in actions:
        if act.get("action") == "open_doc":
            if base_url.startswith("http"):
                flex_buttons.append(
                    _build_flex_button(act["label"], {"type": "uri", "uri": act["url"]}, style="secondary")
                )
        elif act.get("action") == "call_parent":
            flex_buttons.append(
                _build_flex_button(act["label"], {"type": "uri", "uri": act["url"]}, style="primary", color="#7C3AED")
            )
        else:
            is_confirm = act["action"] == "confirm_review"
            btn_color = "#059669" if (is_confirm and expense) else ("#7C3AED" if is_confirm else "#D97706")
            flex_buttons.append(
                _build_flex_button(
                    act["label"],
                    {"type": "postback", "data": act["data"]},
                    style="primary" if is_confirm else "secondary",
                    color=btn_color if is_confirm else None,
                )
            )

    flex_message = {
        "type": "flex",
        "altText": reply_text[:400],
        "contents": _assemble_bubble(
            theme_key=theme_key,
            header_title=header_title,
            body_contents=body_contents,
            footer_buttons=flex_buttons,
            hero_asset=hero_asset,
            public_base_url=base_url,
        ),
    }

    line_messages = _attach_line_quick_reply([flex_message], quick_replies)

    return {
        "reply_text": reply_text,
        "header_title": header_title,
        "body_lines": body_lines,
        "actions": actions,
        "card_view": card_view,
        "quick_replies": quick_replies,
        "flex_message": flex_message,
        "line_messages": line_messages,
    }


def _build_reimbursement_summary_response(
    db_path: str,
    target_teacher: str,
    public_base_url: str = "",
) -> Dict[str, Any]:
    exp_data = database.list_expenses(db_path=db_path)
    summary = exp_data["summary"]
    stats = database.get_db_stats(db_path=db_path)
    pending_total = float(summary["pending_reimbursement_total"] or 0.0)
    pending_cnt = int(summary["pending_reimbursement_count"] or 0)
    reimb_total = float(summary["reimbursed_total"] or 0.0)
    reimb_cnt = int(summary.get("reimbursed_count") or 0)
    pending_docs = int(stats["pending_review_count"] or 0)

    denom = pending_total + reimb_total
    reimb_pct = int(round((reimb_total / denom) * 100)) if denom > 0 else 0

    reply_text = (
        f"💸 สรุปยอดเงินของ{target_teacher}ตอนนี้ครับ:\n"
        f"• รอเบิกคืนโรงเรียน: {pending_total:,.2f} บาท ({pending_cnt} ใบ)\n"
        f"• เบิกคืนแล้ว: {reimb_total:,.2f} บาท\n"
        f"• เอกสารรอ{target_teacher}กดตรวจ: {pending_docs} ฉบับ"
    )

    actions: List[Dict[str, str]] = [
        {
            "label": f"📄 รอตรวจ ({pending_docs} ใบ)" if pending_docs else "📄 ดูรายการรอตรวจ",
            "action": "run_cmd",
            "cmd": "รอตรวจ",
            "data": _encode_postback({"action": "run_cmd", "cmd": "รอตรวจ"}),
        }
    ]
    base_url = (public_base_url or "").strip().rstrip("/")
    external_web_url = (
        append_external_browser_param(base_url)
        if base_url.lower().startswith(("http://", "https://"))
        else ""
    )
    if external_web_url:
        actions.append({"label": "💻 เปิดตารางเบิกเงิน", "action": "open_web", "url": external_web_url})

    body_contents: List[Dict[str, Any]] = [
        {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "contents": [
                {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#FEF3C7",
                    "cornerRadius": "md",
                    "paddingAll": "sm",
                    "contents": [
                        {"type": "text", "text": "⏳ รอเบิกคืน", "size": "xs", "color": "#92400E", "align": "center"},
                        {"type": "text", "text": f"{pending_total:,.0f} ฿", "size": "lg", "weight": "bold", "color": "#B45309", "align": "center"},
                        {"type": "text", "text": f"{pending_cnt} ใบ", "size": "xxs", "color": "#92400E", "align": "center"},
                    ],
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#D1FAE5",
                    "cornerRadius": "md",
                    "paddingAll": "sm",
                    "contents": [
                        {"type": "text", "text": "✓ เบิกคืนแล้ว", "size": "xs", "color": "#065F46", "align": "center"},
                        {"type": "text", "text": f"{reimb_total:,.0f} ฿", "size": "lg", "weight": "bold", "color": "#047857", "align": "center"},
                        {"type": "text", "text": f"{reimb_cnt} ใบ", "size": "xxs", "color": "#065F46", "align": "center"},
                    ],
                },
            ],
        },
        {"type": "text", "text": f"สัดส่วนเบิกคืนแล้ว: {reimb_pct}% • รอตรวจ: {pending_docs} ฉบับ", "size": "xs", "color": "#475569", "wrap": True},
    ]
    footer_buttons = [
        _build_flex_button(actions[0]["label"], {"type": "postback", "data": actions[0]["data"]}, style="primary", color="#2563EB")
    ]
    if external_web_url:
        footer_buttons.append(
            _build_flex_button("💻 เปิดดูบนเว็บ", {"type": "uri", "uri": external_web_url}, style="secondary")
        )

    flex_message = {
        "type": "flex",
        "altText": reply_text[:400],
        "contents": _assemble_bubble(
            theme_key="summary",
            header_title=f"💸 สรุปงบห้องเรียนของ{target_teacher}",
            body_contents=body_contents,
            footer_buttons=footer_buttons,
            hero_asset="summary",
            public_base_url=base_url,
        ),
    }
    quick_replies = _build_quick_replies([
        ("📥 ดูรายการค้างตรวจ", "รอตรวจ"),
        ("⚠️ เด็กแพ้อาหาร", "แพ้อาหาร"),
        ("📋 เมนู", "เมนู"),
    ])
    card_view = {
        "kind": "expense_summary",
        "theme": "summary",
        "step_num": "4",
        "badge_title": "ยอดรอเบิก / สรุปงบ",
        "mascot_image": _mascot_path("summary"),
        "pending_total": pending_total,
        "pending_count": pending_cnt,
        "reimbursed_total": reimb_total,
        "reimbursed_count": reimb_cnt,
        "reimbursed_pct": reimb_pct,
        "pending_docs": pending_docs,
    }
    return {
        "intent": "reimbursement_summary",
        "reply_text": reply_text,
        "summary": summary,
        "actions": actions,
        "card_view": card_view,
        "quick_replies": quick_replies,
        "flex_message": flex_message,
        "line_messages": _attach_line_quick_reply([flex_message], quick_replies),
    }


def _build_allergy_watchlist_response(
    db_path: str,
    target_teacher: str,
    public_base_url: str = "",
) -> Dict[str, Any]:
    stu_data = database.list_students(db_path=db_path)
    all_students = stu_data["students"]
    allergy_students = [s for s in all_students if s.get("allergies") or s.get("medical_notes")]
    lines = [
        f"👶 แฟ้มเด็กอนุบาลของ{target_teacher}ตอนนี้มี {stu_data['summary']['total_students']} คนครับ "
        f"(ต้องระวังแพ้อาหาร/สุขภาพ {len(allergy_students)} คน)"
    ]
    display_students = allergy_students[:5] if allergy_students else all_students[:5]
    card_students: List[Dict[str, str]] = []
    for stu in display_students:
        nick = f" ({stu['nickname']})" if stu.get("nickname") else ""
        warn = " / ".join(p for p in (stu.get("allergies"), stu.get("medical_notes")) if p)
        phone_val = (stu.get("parent_phone") or "").strip()
        if allergy_students:
            phone_str = f" • โทร {phone_val}" if phone_val else ""
            lines.append(f"⚠️ {stu['full_name']}{nick} [{stu['classroom']}]: {warn}{phone_str}")
        else:
            lines.append(f"• {stu['full_name']}{nick} [{stu['classroom']}]")
        card_students.append({
            "id": str(stu.get("id") or ""),
            "name": stu.get("nickname") or stu["full_name"],
            "full_name": stu["full_name"],
            "classroom": stu.get("classroom") or "อนุบาล",
            "warning": warn or "ไม่มีระบุแพ้อาหาร",
            "parent_phone": phone_val or "-",
        })
    reply_text = "\n".join(lines)

    body_contents: List[Dict[str, Any]] = []
    if card_students:
        for cs in card_students[:4]:
            body_contents.append(
                {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#FFF1F2" if allergy_students else "#F8FAFC",
                    "cornerRadius": "md",
                    "paddingAll": "sm",
                    "contents": [
                        {"type": "text", "text": f"{cs['name']} ({cs['classroom']})", "size": "sm", "weight": "bold", "color": "#1E293B", "wrap": True},
                        {"type": "text", "text": f"⚠️ {cs['warning']} • 📞 {cs['parent_phone']}", "size": "xs", "color": "#BE123C" if allergy_students else "#475569", "wrap": True},
                    ],
                }
            )
    else:
        body_contents.append({"type": "text", "text": f"ยังไม่มีรายชื่อเด็กในแฟ้มครับ{target_teacher}", "size": "sm", "color": "#475569"})

    footer_buttons: List[Dict[str, Any]] = []
    actions: List[Dict[str, str]] = []
    first_phone_stu = next(
        (cs for cs in card_students if _format_tel_uri(cs["parent_phone"])),
        None,
    )
    if first_phone_stu:
        tel_uri = _format_tel_uri(first_phone_stu["parent_phone"])
        actions.append({
            "label": _truncate_line_label(f"📞 โทรด่วน ({first_phone_stu['name']})", 20),
            "action": "call_parent",
            "phone": first_phone_stu["parent_phone"],
            "name": first_phone_stu["name"],
            "url": tel_uri,
        })
        footer_buttons.append(
            _build_flex_button(actions[0]["label"], {"type": "uri", "uri": tel_uri}, style="primary", color="#E11D48")
        )

    flex_message = {
        "type": "flex",
        "altText": reply_text[:400],
        "contents": _assemble_bubble(
            theme_key="allergy",
            header_title=f"⚠️ รายชื่อเด็กแพ้อาหาร ({len(allergy_students)} คน)",
            body_contents=body_contents,
            footer_buttons=footer_buttons,
            hero_asset="allergy",
            public_base_url=public_base_url,
        ),
    }
    quick_replies = _build_quick_replies([
        ("💸 ยอดรอเบิก", "ยอดรอเบิก"),
        ("📥 รอตรวจ", "รอตรวจ"),
        ("📋 เมนู", "เมนู"),
    ])
    card_view = {
        "kind": "allergy_watchlist",
        "theme": "allergy",
        "step_num": "5",
        "badge_title": "แพ้อาหารหน้าห้องเรียน",
        "mascot_image": _mascot_path("allergy"),
        "total_students": stu_data["summary"]["total_students"],
        "allergy_count": len(allergy_students),
        "students": card_students,
    }
    return {
        "intent": "student_allergy_summary",
        "reply_text": reply_text,
        "students": all_students[:10],
        "actions": actions,
        "card_view": card_view,
        "quick_replies": quick_replies,
        "flex_message": flex_message,
        "line_messages": _attach_line_quick_reply([flex_message], quick_replies),
    }


def _build_pending_review_carousel_response(
    db_path: str,
    target_teacher: str,
    public_base_url: str = "",
    r2_enabled: bool = False,
) -> Dict[str, Any]:
    pending = database.search_documents(
        review_status="pending_review",
        limit=6,
        db_path=db_path,
        r2_enabled=r2_enabled,
    )
    quick_replies = _build_quick_replies([
        ("💸 ยอดรอเบิก", "ยอดรอเบิก"),
        ("⚠️ เด็กแพ้อาหาร", "แพ้อาหาร"),
        ("📋 เมนู", "เมนู"),
    ])
    if not pending:
        reply_text = f"เย้! ไม่มีเอกสารค้างตรวจแล้วครับ{target_teacher} พักผ่อนได้เลยนะครับ ✨"
        flex_message = {
            "type": "flex",
            "altText": reply_text[:400],
            "contents": _assemble_bubble(
                theme_key="pending",
                header_title=f"✨ ตรวจครบหมดแล้วครับ{target_teacher}!",
                body_contents=[{"type": "text", "text": reply_text, "size": "sm", "color": "#334155", "wrap": True}],
                hero_asset="confirmed",
                public_base_url=public_base_url,
            ),
        }
        return {
            "intent": "pending_review_list",
            "reply_text": reply_text,
            "documents": [],
            "actions": [],
            "card_view": {
                "kind": "pending_carousel",
                "theme": "pending",
                "step_num": "3",
                "badge_title": "รอตรวจ (ค้างหลายใบ)",
                "mascot_image": _mascot_path("confirmed"),
                "items": [],
            },
            "quick_replies": quick_replies,
            "flex_message": flex_message,
            "line_messages": _attach_line_quick_reply([flex_message], quick_replies),
        }

    lines = [f"📥 มีเอกสารรอ{target_teacher}ตรวจยืนยัน {len(pending)} รายการล่าสุดครับ:"]
    items: List[Dict[str, Any]] = []
    bubbles: List[Dict[str, Any]] = []
    base_url = (public_base_url or "").strip().rstrip("/")

    for idx, d in enumerate(pending, start=1):
        lines.append(f"• {d['filename']} ({d['category']})")
        full_doc = database.get_document_by_id(d["id"], db_path=db_path, r2_enabled=r2_enabled) or {}
        exp = full_doc.get("expense")
        stu = full_doc.get("student")
        if exp:
            title = exp.get("merchant") or d["filename"]
            subtitle = f"฿{float(exp.get('total_amount') or 0):,.2f}"
            icon = "🧾"
        elif stu:
            title = stu.get("nickname") or stu.get("full_name") or d["filename"]
            subtitle = stu.get("allergies") or stu.get("classroom") or "ข้อมูลเด็ก"
            icon = "👶"
        else:
            title = d["filename"]
            subtitle = d.get("category") or "เอกสาร"
            icon = "📄"

        confirm_data = _encode_postback({"action": "confirm_review", "doc_id": str(d["id"])})
        items.append({
            "index": idx,
            "doc_id": d["id"],
            "filename": d["filename"],
            "category": d.get("category") or "general",
            "title": title,
            "subtitle": subtitle,
            "icon": icon,
            "confirm_data": confirm_data,
        })
        bubbles.append(
            _assemble_bubble(
                theme_key="pending",
                header_title=f"{icon} รอตรวจ #{idx}/{len(pending)}",
                body_contents=[
                    {"type": "text", "text": title, "size": "md", "weight": "bold", "color": "#1E293B", "wrap": True},
                    {"type": "text", "text": subtitle, "size": "sm", "color": "#2563EB", "weight": "bold", "wrap": True},
                    {"type": "text", "text": f"ไฟล์: {d['filename']}", "size": "xs", "color": "#64748B", "wrap": True},
                ],
                footer_buttons=[
                    _build_flex_button("✅ ยืนยันตรวจแล้ว", {"type": "postback", "data": confirm_data}, style="primary", color="#2563EB")
                ],
                hero_asset="pending",
                public_base_url=base_url,
            )
        )

    reply_text = "\n".join(lines)
    flex_message = {
        "type": "flex",
        "altText": f"📥 มีเอกสารรอ{target_teacher}ตรวจ {len(pending)} ใบ",
        "contents": {"type": "carousel", "contents": bubbles[:10]},
    }
    return {
        "intent": "pending_review_list",
        "reply_text": reply_text,
        "documents": pending,
        "actions": [
            {"label": f"✅ ตรวจ #{it['index']}", "action": "confirm_review", "doc_id": it["doc_id"], "data": it["confirm_data"]}
            for it in items
        ],
        "card_view": {
            "kind": "pending_carousel",
            "theme": "pending",
            "step_num": "3",
            "badge_title": f"รอตรวจ ({len(pending)} ใบ)",
            "mascot_image": _mascot_path("pending"),
            "items": items,
        },
        "quick_replies": quick_replies,
        "flex_message": flex_message,
        "line_messages": _attach_line_quick_reply([flex_message], quick_replies),
    }


def _build_search_carousel_response(
    search_term: str,
    db_path: str,
    target_teacher: str,
    public_base_url: str = "",
    r2_enabled: bool = False,
) -> Dict[str, Any]:
    docs = database.search_documents(query=search_term, limit=5, db_path=db_path, r2_enabled=r2_enabled)
    stus = database.list_students(query=search_term, db_path=db_path)["students"][:3]
    quick_replies = _build_quick_replies([
        ("💸 ยอดรอเบิก", "ยอดรอเบิก"),
        ("⚠️ แพ้อาหาร", "แพ้อาหาร"),
        ("📥 รอตรวจ", "รอตรวจ"),
    ])
    items: List[Dict[str, Any]] = []
    bubbles: List[Dict[str, Any]] = []
    base_url = (public_base_url or "").strip().rstrip("/")

    if not docs and not stus:
        reply_text = (
            f'น้องโชคลองค้นคำว่า "{search_term}" ในตู้เซฟและแฟ้มเด็กให้{target_teacher}แล้ว '
            "ยังไม่พบข้อมูลครับ 🔍"
        )
        flex_message = {
            "type": "flex",
            "altText": reply_text[:400],
            "contents": _assemble_bubble(
                theme_key="search",
                header_title=f'🔍 ค้นหา "{search_term}"',
                body_contents=[{"type": "text", "text": reply_text, "size": "sm", "color": "#334155", "wrap": True}],
                hero_asset="search",
                public_base_url=base_url,
            ),
        }
    else:
        lines = [f'🔍 น้องโชคค้นคำว่า "{search_term}" เจอข้อมูลให้{target_teacher}ตามนี้ครับ:']
        for stu in stus:
            nick = f" ({stu['nickname']})" if stu.get("nickname") else ""
            lines.append(f"👶 {stu['full_name']}{nick} [{stu['classroom']}] โทร {stu.get('parent_phone') or '-'}")
            if stu.get("allergies"):
                lines.append(f"   ⚠️ แพ้อาหาร: {stu['allergies']}")
            badge = "รายชื่อเด็ก"
            items.append({
                "kind": "student",
                "icon": "👦",
                "title": stu.get("nickname") or stu["full_name"],
                "sub": stu.get("allergies") or stu.get("classroom") or "ข้อมูลเด็ก",
                "badge": f"{badge} →",
                "doc_id": stu.get("document_id") or "",
                "cmd": "แพ้อาหาร" if not stu.get("document_id") else "",
            })
            stu_doc_id = (stu.get("document_id") or "").strip()
            stu_tel = _format_tel_uri(stu.get("parent_phone") or "")
            stu_footer_btns: List[Dict[str, Any]] = []
            if stu_doc_id and base_url.lower().startswith(("http://", "https://")):
                stu_doc_url = append_external_browser_param(f"{base_url}/?doc={stu_doc_id}")
                stu_footer_btns.append(
                    _build_flex_button("เปิดเอกสาร", {"type": "uri", "uri": stu_doc_url}, style="primary", color="#0D9488")
                )
            if stu_tel:
                stu_footer_btns.append(
                    _build_flex_button(
                        "📞 โทรผู้ปกครอง",
                        {"type": "uri", "uri": stu_tel},
                        style="secondary" if stu_footer_btns else "primary",
                        color=None if stu_footer_btns else "#0D9488",
                    )
                )
            if not stu_footer_btns:
                stu_footer_btns.append(
                    _build_flex_button(
                        "⚠️ ดูแพ้อาหาร",
                        {"type": "postback", "data": _encode_postback({"action": "run_cmd", "cmd": "แพ้อาหาร"})},
                        style="secondary",
                    )
                )
            bubbles.append(
                _assemble_bubble(
                    theme_key="search",
                    header_title="👦 แฟ้มนักเรียน",
                    body_contents=[
                        {"type": "text", "text": f"{stu['full_name']}{nick}", "size": "md", "weight": "bold", "color": "#1E293B", "wrap": True},
                        {"type": "text", "text": f"ห้อง {stu.get('classroom') or 'อนุบาล'} • โทร {stu.get('parent_phone') or '-'}", "size": "xs", "color": "#475569", "wrap": True},
                    ],
                    footer_buttons=stu_footer_btns,
                    hero_asset="search",
                    public_base_url=base_url,
                )
            )
        for doc in docs:
            snippet = (doc.get("snippet") or "").replace("\n", " ")[:70]
            lines.append(f"📄 {doc['filename']}: {snippet}...")
            is_exp = doc.get("category") == "receipt"
            items.append({
                "kind": "expense" if is_exp else "document",
                "icon": "🧾" if is_exp else "📄",
                "title": doc["filename"],
                "sub": snippet or "เอกสารในตู้เซฟ",
                "badge": "ใบเสร็จ →" if is_exp else "เอกสาร →",
                "doc_id": doc["id"],
                "cmd": "",
            })
            footer_btns = []
            if base_url.startswith("http"):
                doc_url = append_external_browser_param(f"{base_url}/?doc={doc['id']}")
                footer_btns.append(
                    _build_flex_button("เปิดเอกสาร", {"type": "uri", "uri": doc_url}, style="primary", color="#0D9488")
                )
            bubbles.append(
                _assemble_bubble(
                    theme_key="search",
                    header_title="🧾 ใบเสร็จ / เอกสาร" if is_exp else "📄 เอกสารในตู้เซฟ",
                    body_contents=[
                        {"type": "text", "text": doc["filename"], "size": "sm", "weight": "bold", "color": "#1E293B", "wrap": True},
                        {"type": "text", "text": snippet or "พบคำค้นในเอกสารนี้", "size": "xs", "color": "#475569", "wrap": True},
                    ],
                    footer_buttons=footer_btns,
                    hero_asset="search",
                    public_base_url=base_url,
                )
            )
        reply_text = "\n".join(lines)
        flex_message = {
            "type": "flex",
            "altText": f'🔍 พบผลลัพธ์สำหรับ "{search_term}"',
            "contents": {"type": "carousel", "contents": bubbles[:10]} if len(bubbles) > 1 else bubbles[0],
        }

    return {
        "intent": "search",
        "query": search_term,
        "reply_text": reply_text,
        "documents": docs,
        "students": stus,
        "actions": [],
        "card_view": {
            "kind": "search_carousel",
            "theme": "search",
            "step_num": "6",
            "badge_title": f'ค้นหา "{search_term}"',
            "mascot_image": _mascot_path("search"),
            "query": search_term,
            "items": items[:6],
        },
        "quick_replies": quick_replies,
        "flex_message": flex_message,
        "line_messages": _attach_line_quick_reply([flex_message], quick_replies),
    }


def _build_help_menu_response(target_teacher: str, public_base_url: str = "") -> Dict[str, Any]:
    reply_text = (
        f"สวัสดีครับ{target_teacher}! น้องโชคช่วยพร้อมดูแลเอกสารให้แล้วครับ 🌤️\n"
        "• ส่งรูปใบเสร็จ / สลิป -> น้องโชคช่วยจดยอดรอเบิกให้\n"
        "• ส่งรูปสูติบัตร / ใบสมัครเด็ก -> เก็บชื่อ เลข 13 หลัก และป้ายเตือนแพ้อาหาร\n"
        "• ส่ง PDF คำสั่งโรงเรียน -> เก็บเข้าตู้เซฟค้นหาได้ตลอด\n"
        '• สรุปยอดเบิก: ดูยอดรอเบิกและเบิกคืนแล้ว\n'
        '• รายการบิล: เปิดสมุดเบิกจ่าย แก้ไขสถานะและส่งออก CSV\n'
        '• แฟ้มเด็ก: ดูรายชื่อ ข้อมูลผู้ปกครอง และกรองเด็กแพ้อาหาร\n'
        '• เอกสารรอตรวจ: ตรวจข้อมูลที่อ่านมาแล้วกดยืนยัน\n'
        '• ค้นหาเอกสาร: พิมพ์ "ค้นหา" ตามด้วยคำ เช่น ค้นหา สีเทียน\n'
        '• ตั้งค่า: เปลี่ยนชื่อเรียก ห้องเรียน และสถานะเบิกจ่ายเริ่มต้น\n'
        'ปุ่มที่เปิดเว็บจะใช้ Safari / Chrome และให้เข้าสู่ระบบ Google ก่อนครับ'
    )
    shortcuts = [
        {"label": "🏷️ ยอดรอเบิก", "cmd": "ยอดรอเบิก", "color": "amber"},
        {"label": "⚠️ แพ้อาหาร", "cmd": "แพ้อาหาร", "color": "rose"},
        {"label": "📥 รอตรวจ", "cmd": "รอตรวจ", "color": "blue"},
    ]
    footer_buttons = [
        _build_flex_button(
            sc["label"],
            {"type": "postback", "data": _encode_postback({"action": "run_cmd", "cmd": sc["cmd"]})},
            style="secondary",
        )
        for sc in shortcuts
    ]
    flex_message = {
        "type": "flex",
        "altText": reply_text[:400],
        "contents": _assemble_bubble(
            theme_key="menu",
            header_title=f"🌤️ เมนูลัดน้องโชคช่วย ({target_teacher})",
            body_contents=[
                {"type": "text", "text": reply_text, "size": "sm", "color": "#334155", "wrap": True}
            ],
            footer_buttons=footer_buttons,
            hero_asset="menu",
            public_base_url=public_base_url,
        ),
    }
    quick_replies = _build_quick_replies([
        ("💸 ยอดรอเบิก", "ยอดรอเบิก"),
        ("⚠️ แพ้อาหาร", "แพ้อาหาร"),
        ("📥 รอตรวจ", "รอตรวจ"),
    ])
    return {
        "intent": "help",
        "reply_text": reply_text,
        "actions": [],
        "card_view": {
            "kind": "menu",
            "theme": "menu",
            "help_text": reply_text,
            "step_num": "7",
            "badge_title": "เมนูคำสั่งลัดในแชท",
            "mascot_image": _mascot_path("menu"),
            "shortcuts": shortcuts,
        },
        "quick_replies": quick_replies,
        "flex_message": flex_message,
        "line_messages": _attach_line_quick_reply([flex_message], quick_replies),
    }


_NATURAL_CARD_COMMAND_PATTERNS = (
    ("ยอดรอเบิก", (
        r"(?:มี)?(?:ยอด(?:เงิน)?รอเบิก(?:คืน)?|เงินรอเบิก(?:คืน)?|เงินสำรองจ่าย)"
        r"(?:อยู่)?(?:เท่าไหร่|เท่าไร|กี่บาท)",
        r"(?:ขอ(?:ดู)?|ช่วย(?:ดู|สรุป)|ดู|สรุป)"
        r"(?:ยอด(?:เงิน)?(?:รอเบิก(?:คืน)?|เบิกคืนแล้ว)|ค่าใช้จ่าย|ยอดเงิน|งบ(?:ห้องเรียน)?)",
    )),
    ("แพ้อาหาร", (
        r"(?:ใคร|เด็กคนไหน|มีใคร|มีเด็กคนไหน)(?:ที่)?แพ้อาหาร(?:บ้าง)?",
        r"(?:มี)?(?:เด็ก|นักเรียน)(?:ที่)?แพ้อาหาร(?:กี่คน|คนไหนบ้าง|บ้าง)",
        r"(?:ขอ(?:ดู)?|ช่วย(?:ดู|แสดง)|ดู|แสดง)(?:รายชื่อ)?(?:เด็ก|นักเรียน)(?:ที่)?แพ้อาหาร(?:บ้าง)?",
    )),
    ("รอตรวจ", (
        r"(?:มี)?(?:เอกสาร|รายการ|บิล)(?:ที่)?(?:ยัง)?(?:รอตรวจ(?:ทาน)?|ค้างตรวจ)"
        r"(?:อยู่)?(?:กี่(?:ใบ|ฉบับ|รายการ)|อะไรบ้าง|บ้าง|ไหม|หรือเปล่า)",
        r"(?:ขอ(?:ดู)?|ช่วย(?:ดู|แสดง)|ดู|แสดง)(?:เอกสาร|รายการ|บิล)"
        r"(?:ที่)?(?:ยัง)?(?:รอตรวจ(?:ทาน)?|ค้างตรวจ)(?:บ้าง)?",
        r"มีอะไร(?:รอตรวจ(?:ทาน)?|ค้างตรวจ)(?:บ้าง)?",
    )),
)


def _resolve_natural_card_command(text: str) -> Optional[str]:
    """Route supported data requests locally; leave search/writing/ambiguous text to existing flows."""
    compact = re.sub(r"\s+", "", text.lower()).rstrip("?!！？.。")
    prefix = r"(?:ตอนนี้|วันนี้)?"
    suffix = r"(?:ตอนนี้|วันนี้)?(?:ให้หน่อย|หน่อย|ด้วย|ได้ไหม|ได้มั้ย|นะ|ครับ|ค่ะ|คะ|จ๊ะ|จ้า)*"
    for command, patterns in _NATURAL_CARD_COMMAND_PATTERNS:
        for pattern in patterns:
            if re.fullmatch(prefix + pattern + suffix, compact):
                return command
    return None


def process_line_text_command(
    text: str,
    db_path: str,
    public_base_url: str = "",
    config: Optional[AppConfig] = None,
    teacher_name: Optional[str] = None,
    storage: Optional[VaultStorageCoordinator] = None,
) -> Dict[str, Any]:
    """Process teacher chat text commands, Gemini assistant chat, or vault searches."""
    if storage is not None:
        storage.require_ready()

    def _run_db_read(read_fn):
        return storage.execute_read(read_fn) if storage is not None else read_fn()

    target_teacher = _resolve_teacher_name(config, teacher_name)
    r2_enabled = bool(config and config.is_r2_enabled())
    clean = (text or "").strip()
    lower = clean.lower()
    card_command = _resolve_natural_card_command(clean) or lower

    if card_command in {"ยอดรอเบิก", "สรุปยอด", "เบิกเงิน", "ค่าใช้จ่าย"}:
        return _run_db_read(
            lambda: _build_reimbursement_summary_response(
                db_path=db_path,
                target_teacher=target_teacher,
                public_base_url=public_base_url,
            )
        )

    if card_command in {"แพ้อาหาร", "รายชื่อเด็ก", "ข้อมูลเด็ก", "เด็กในห้อง"}:
        return _run_db_read(
            lambda: _build_allergy_watchlist_response(
                db_path=db_path,
                target_teacher=target_teacher,
                public_base_url=public_base_url,
            )
        )

    if card_command in {"รอตรวจ", "ค้างตรวจ"}:
        return _run_db_read(
            lambda: _build_pending_review_carousel_response(
                db_path=db_path,
                target_teacher=target_teacher,
                public_base_url=public_base_url,
                r2_enabled=r2_enabled,
            )
        )

    if lower == "ส่งเอกสาร":
        reply_text = (
            f"ส่งรูปบิล / เอกสารได้เลยครับ{target_teacher}\n"
            "เลือกถ่ายรูปหรือเลือกรูปจากอัลบั้มด้านล่าง ส่วน PDF ส่งเป็นไฟล์แนบในแชทได้เลยครับ\n"
            "เมื่ออ่านเสร็จ กรุณาตรวจชื่อ ยอดเงิน และข้อมูลเด็กก่อนกดยืนยัน"
        )
        items = [
            {"type": "action", "action": {"type": "camera", "label": "ถ่ายรูปเอกสาร"}},
            {"type": "action", "action": {"type": "cameraRoll", "label": "เลือกรูปจากอัลบั้ม"}},
        ]
        return {"intent": "upload_guide", "reply_text": reply_text,
                "line_messages": [{"type": "text", "text": reply_text, "quickReply": {"items": items}}]}

    if lower in {"ค้นหาเอกสาร", "ค้นหา", "หา", "search"}:
        reply_text = 'พิมพ์ "ค้นหา" ตามด้วยคำที่ต้องการครับ เช่น ค้นหา สีเทียน หรือ ค้นหา น้องมะลิ'
        return {"intent": "search_guide", "reply_text": reply_text,
                "line_messages": [{"type": "text", "text": reply_text}]}

    if not clean or lower in {"เมนู", "คำสั่ง", "คำสั่งลัด", "help", "menu", "ช่วยด้วย", "วิธีใช้"}:
        return _build_help_menu_response(target_teacher=target_teacher, public_base_url=public_base_url)

    is_explicit_search = lower.startswith(("ค้นหา ", "หา ", "search "))
    if not is_explicit_search:
        db_context = _run_db_read(
            lambda: _build_teacher_db_context(
                db_path=db_path,
                search_query=clean,
                r2_enabled=r2_enabled,
            )
        )
        ai_reply = generate_nong_chok_ai_reply(
            user_text=clean or "สวัสดี",
            db_context=db_context,
            config=config,
            teacher_name=target_teacher,
        )
        if ai_reply:
            quick_replies = _build_quick_replies([
                ("💸 ยอดรอเบิก", "ยอดรอเบิก"),
                ("⚠️ แพ้อาหาร", "แพ้อาหาร"),
                ("📥 รอตรวจ", "รอตรวจ"),
            ])
            resolved_model = (
                resolve_llm_provider_and_model(config.llm_provider, config.llm_model)[1]
                if config
                else DEFAULT_GEMINI_MODEL
            )
            return {
                "intent": "ai_chat",
                "model": resolved_model,
                "reply_text": ai_reply,
                "quick_replies": quick_replies,
                "line_messages": _attach_line_quick_reply([{"type": "text", "text": ai_reply}], quick_replies),
                "actions": [],
            }

    if not clean or any(kw in lower for kw in ("สวัสดี", "ช่วยด้วย", "เมนู", "help", "menu")):
        return _build_help_menu_response(target_teacher=target_teacher, public_base_url=public_base_url)

    search_term = clean
    for prefix in ("ค้นหา ", "หา ", "search "):
        if lower.startswith(prefix):
            search_term = clean[len(prefix):].strip()
            break

    return _run_db_read(
        lambda: _build_search_carousel_response(
            search_term=search_term,
            db_path=db_path,
            target_teacher=target_teacher,
            public_base_url=public_base_url,
            r2_enabled=r2_enabled,
        )
    )


def handle_line_postback(
    data_str: str,
    db_path: str,
    config: Optional[AppConfig] = None,
    teacher_name: Optional[str] = None,
    public_base_url: str = "",
    storage: Optional[VaultStorageCoordinator] = None,
    operation_id: str = "",
) -> Dict[str, Any]:
    """Handle LINE Postback / simulator button clicks such as confirm_review, mark_reimbursed, or run_cmd."""
    if storage is not None:
        storage.require_ready()

    target_teacher = _resolve_teacher_name(config, teacher_name)
    r2_enabled = bool(config and config.is_r2_enabled())
    resolved_base_url = (
        public_base_url
        or (getattr(config, "public_base_url", "") if config is not None else "")
        or ""
    ).strip()

    parsed = parse_qs(data_str or "")
    action = (parsed.get("action") or [""])[0].strip()
    doc_id = (parsed.get("doc_id") or [""])[0].strip()

    if action == "run_cmd":
        cmd = (parsed.get("cmd") or ["เมนู"])[0].strip()
        cmd_res = process_line_text_command(
            cmd,
            db_path=db_path,
            public_base_url=resolved_base_url,
            config=config,
            teacher_name=target_teacher,
            storage=storage,
        )
        return {"success": True, "action": action, **cmd_res}

    if storage is not None and operation_id:
        existing = storage.get_committed_operation(operation_id)
        if existing is not None:
            return existing

    if not doc_id:
        reply_text = f"น้องโชคไม่พบรหัสเอกสารสำหรับทำรายการนี้ครับ{target_teacher}"
        return {
            "success": False,
            "reply_text": reply_text,
            "line_messages": [{"type": "text", "text": reply_text}],
        }

    doc = (
        storage.execute_read(lambda: database.get_document_by_id(doc_id, db_path=db_path, r2_enabled=r2_enabled))
        if storage is not None
        else database.get_document_by_id(doc_id, db_path=db_path, r2_enabled=r2_enabled)
    )
    if not doc:
        reply_text = f"ไม่พบเอกสารนี้ในฐานข้อมูลแล้วครับ{target_teacher}"
        return {
            "success": False,
            "reply_text": reply_text,
            "line_messages": [{"type": "text", "text": reply_text}],
        }

    shortcuts = [
        {"label": "🏷️ ยอดรอเบิก", "cmd": "ยอดรอเบิก", "color": "amber"},
        {"label": "⚠️ แพ้อาหาร", "cmd": "แพ้อาหาร", "color": "rose"},
        {"label": "📥 รอตรวจ", "cmd": "รอตรวจ", "color": "blue"},
    ]
    quick_replies = _build_quick_replies([
        ("💸 ยอดรอเบิก", "ยอดรอเบิก"),
        ("⚠️ แพ้อาหาร", "แพ้อาหาร"),
        ("📥 รอตรวจ", "รอตรวจ"),
    ])

    if action == "confirm_review":
        def _do_confirm(_rev: int) -> Dict[str, Any]:
            updated = database.update_document_metadata(
                doc_id=doc_id,
                review_status="reviewed",
                db_path=db_path,
                r2_enabled=r2_enabled,
            )
            fresh_doc = updated or doc
            doc_filename = fresh_doc.get("filename") or "เอกสาร"
            reply_text = f'✅ น้องโชคยืนยันตรวจเอกสาร "{doc_filename}" เรียบร้อยแล้วครับ{target_teacher}!'
            flex_message = {
                "type": "flex",
                "altText": reply_text[:400],
                "contents": _assemble_bubble(
                    theme_key="menu",
                    header_title="✅ ยืนยันตรวจเรียบร้อยแล้วครับ!",
                    body_contents=[{"type": "text", "text": reply_text, "size": "sm", "color": "#334155", "wrap": True}],
                    hero_asset="confirmed",
                    public_base_url=resolved_base_url,
                ),
            }
            return {
                "success": True,
                "doc_id": doc_id,
                "action": action,
                "document": updated,
                "reply_text": reply_text,
                "card_view": {
                    "kind": "menu",
                    "theme": "menu",
                    "step_num": "7",
                    "badge_title": "ยืนยันสำเร็จ • ปุ่มลัดในแชท",
                    "mascot_image": _mascot_path("confirmed"),
                    "status_message": reply_text,
                    "shortcuts": shortcuts,
                },
                "quick_replies": quick_replies,
                "flex_message": flex_message,
                "line_messages": _attach_line_quick_reply([flex_message], quick_replies),
            }

        if storage is not None:
            return storage.execute_mutation(operation_id, "line_postback_confirm_review", _do_confirm)
        return _do_confirm(0)

    if action == "mark_reimbursed":
        def _do_reimburse(_rev: int) -> Dict[str, Any]:
            updated = database.update_document_metadata(
                doc_id=doc_id,
                review_status="reviewed",
                db_path=db_path,
                r2_enabled=r2_enabled,
            )
            fresh_doc = updated or doc
            doc_filename = fresh_doc.get("filename") or "เอกสาร"
            exp = fresh_doc.get("expense")
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
                    f"สำหรับบิล {updated_exp['merchant'] or doc_filename} ครับ{target_teacher}!"
                )
                flex_message = {
                    "type": "flex",
                    "altText": reply_text[:400],
                    "contents": _assemble_bubble(
                        theme_key="receipt",
                        header_title="💸 บันทึกเบิกเงินคืนเรียบร้อยแล้ว!",
                        body_contents=[{"type": "text", "text": reply_text, "size": "sm", "color": "#334155", "wrap": True}],
                        hero_asset="confirmed",
                        public_base_url=resolved_base_url,
                    ),
                }
                return {
                    "success": True,
                    "doc_id": doc_id,
                    "action": action,
                    "expense": updated_exp,
                    "reply_text": reply_text,
                    "card_view": {
                        "kind": "menu",
                        "theme": "menu",
                        "step_num": "7",
                        "badge_title": "เบิกคืนแล้ว • ปุ่มลัดในแชท",
                        "mascot_image": _mascot_path("confirmed"),
                        "status_message": reply_text,
                        "shortcuts": shortcuts,
                    },
                    "quick_replies": quick_replies,
                    "flex_message": flex_message,
                    "line_messages": _attach_line_quick_reply([flex_message], quick_replies),
                }

            reply_text = f"น้องโชคได้รับคำสั่ง ({action}) แล้วครับ{target_teacher}"
            return {
                "success": True,
                "doc_id": doc_id,
                "action": action,
                "reply_text": reply_text,
                "quick_replies": quick_replies,
                "line_messages": _attach_line_quick_reply([{"type": "text", "text": reply_text}], quick_replies),
            }

        if storage is not None:
            return storage.execute_mutation(operation_id, "line_postback_reimburse", _do_reimburse)
        return _do_reimburse(0)

    reply_text = f"น้องโชคได้รับคำสั่ง ({action}) แล้วครับ{target_teacher}"
    return {
        "success": True,
        "action": action,
        "reply_text": reply_text,
        "quick_replies": quick_replies,
        "line_messages": _attach_line_quick_reply([{"type": "text", "text": reply_text}], quick_replies),
    }
