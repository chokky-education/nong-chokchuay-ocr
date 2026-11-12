import json
import re
from datetime import datetime
from typing import Any, Dict, List

import requests

from config import AppConfig

THAI_MONTHS = {
    "ม.ค.": 1, "มค": 1, "มกราคม": 1,
    "ก.พ.": 2, "กพ": 2, "กุมภาพันธ์": 2,
    "มี.ค.": 3, "มีค": 3, "มีนาคม": 3,
    "เม.ย.": 4, "เมย": 4, "เมษายน": 4,
    "พ.ค.": 5, "พค": 5, "พฤษภาคม": 5,
    "มิ.ย.": 6, "มิย": 6, "มิถุนายน": 6,
    "ก.ค.": 7, "กค": 7, "กรกฎาคม": 7,
    "ส.ค.": 8, "สค": 8, "สิงหาคม": 8,
    "ก.ย.": 9, "กย": 9, "กันยายน": 9,
    "ต.ค.": 10, "ตค": 10, "ตุลาคม": 10,
    "พ.ย.": 11, "พย": 11, "พฤศจิกายน": 11,
    "ธ.ค.": 12, "ธค": 12, "ธันวาคม": 12,
}

_GENERIC_HEADER_RE = re.compile(
    r"^(?:ใบเสร็จรับเงิน|ใบกำกับภาษี(?:อย่างย่อ)?|ใบเสร็จรับเงิน/ใบกำกับภาษี(?:อย่างย่อ)?|"
    r"tax\s*invoice(?:\s*\(abb\))?|receipt|โอนเงินสำเร็จ|สลิปโอนเงิน|รายการสำเร็จ|"
    r"เลขประจำตัวผู้เสียภาษี.*|tax\s*id.*)$",
    re.IGNORECASE,
)
_PURE_META_LINE_RE = re.compile(r"^[\d\s/\-.:#%()]+$")

_EXPENSE_CATEGORY_KEYWORDS = {
    "food": ("กาแฟ", "cafe", "amazon", "starbucks", "ข้าว", "อาหาร", "ก๋วยเตี๋ยว", "7-eleven", "เซเว่น", "restaurant", "coffee"),
    "transport": ("grab", "bolt", "ptt", "บางจาก", "ปั๊ม", "น้ำมัน", "ทางด่วน", "bts", "mrt", "แท็กซี่", "สายการบิน"),
    "utilities": ("การไฟฟ้า", "การประปา", "ค่าไฟ", "ค่าน้ำ", "ais", "true", "dtac", "3bb", "อินเทอร์เน็ต"),
    "office": ("officemate", "b2s", "เครื่องเขียน", "กระดาษ", "หมึก", "cloud", "software", "hosting"),
}


def is_llm_configured(config: AppConfig) -> bool:
    """Return True when a cloud LLM API key or local OpenAI-compatible URL is configured."""
    return bool(config.llm_api_key or (config.llm_provider != "gemini" and config.llm_base_url))


def _normalize_year(raw_year: int) -> int:
    if raw_year < 100:
        raw_year = (2500 + raw_year) if raw_year >= 40 else (2000 + raw_year)
    if raw_year >= 2400:
        return raw_year - 543
    return raw_year


def _valid_iso_date(year: int, month: int, day: int) -> str:
    if not (1990 <= year <= 2100):
        return ""
    try:
        datetime(year, month, day)
        return f"{year:04d}-{month:02d}-{day:02d}"
    except ValueError:
        return ""


def _extract_date(text: str) -> str:
    month_keys = "|".join(sorted((re.escape(k) for k in THAI_MONTHS), key=len, reverse=True))
    thai_date_re = re.compile(rf"(\d{{1,2}})\s*({month_keys})\s*(\d{{2,4}})")
    for m in thai_date_re.finditer(text):
        iso = _valid_iso_date(
            _normalize_year(int(m.group(3))),
            THAI_MONTHS.get(m.group(2), 0),
            int(m.group(1)),
        )
        if iso:
            return iso

    iso_re = re.compile(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b")
    for m in iso_re.finditer(text):
        iso = _valid_iso_date(_normalize_year(int(m.group(1))), int(m.group(2)), int(m.group(3)))
        if iso:
            return iso

    dmy_re = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")
    for m in dmy_re.finditer(text):
        iso = _valid_iso_date(_normalize_year(int(m.group(3))), int(m.group(2)), int(m.group(1)))
        if iso:
            return iso

    return ""


def _parse_amount(num_str: str) -> float:
    try:
        return round(float(num_str.replace(",", "").strip()), 2)
    except ValueError:
        return 0.0


def _extract_amount_for_patterns(lines: List[str], patterns: List[re.Pattern[str]]) -> float:
    for pat in patterns:
        for line in reversed(lines):
            m = pat.search(line)
            if m:
                val = _parse_amount(m.group(1))
                if val > 0:
                    return val
    return 0.0


def _infer_merchant(lines: List[str]) -> str:
    recipient_re = re.compile(r"(?:ไปยัง|ผู้รับ|ชื่อบัญชี|ร้านค้า)[:\s]+(.+)")
    for line in lines:
        m = recipient_re.search(line)
        if m:
            candidate = m.group(1).strip()
            if candidate and not _PURE_META_LINE_RE.match(candidate):
                return candidate[:120]

    for line in lines[:8]:
        cleaned = line.strip()
        if not cleaned or len(cleaned) < 2:
            continue
        if _GENERIC_HEADER_RE.match(cleaned) or _PURE_META_LINE_RE.match(cleaned):
            continue
        if re.search(r"วันที่|เวลา|ยอดสุทธิ|รวมทั้งสิ้น|จำนวนเงิน|vat|tax\s*id", cleaned, re.IGNORECASE):
            continue
        return cleaned[:120]
    return ""


def _infer_expense_category(text: str) -> str:
    lower = text.lower()
    for category, keywords in _EXPENSE_CATEGORY_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return category
    return "other"


def is_likely_expense(text: str) -> bool:
    """Heuristic check for whether OCR text looks like a receipt, invoice, or transfer slip."""
    lower = (text or "").lower()
    signals = (
        "ใบเสร็จ",
        "ใบกำกับภาษี",
        "ยอดสุทธิ",
        "รวมทั้งสิ้น",
        "จำนวนเงิน",
        "โอนเงิน",
        "vat",
        "บาท",
        "thb",
        "เลขประจำตัวผู้เสียภาษี",
        "tax invoice",
    )
    hits = sum(1 for sig in signals if sig in lower)
    return hits >= 2


def extract_expense_regex(text: str) -> Dict[str, Any]:
    """Extract structured Expense Record fields purely offline using Thai/English regexes."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    amt_token = r"(?:thb|฿)?\s*([\d,]+\.\d{2}|[\d,]+(?!\s*%)(?!\s*(?:items?|ชิ้น|รายการ|pcs|ea)\b))\s*(?:บาท|thb|฿)?"

    total_patterns = [
        re.compile(rf"(?:ยอดสุทธิ|รวมทั้งสิ้น|ยอดรวมสุทธิ|รวมเงินทั้งสิ้น|grand\s*total|net\s*total)[:\s]*{amt_token}", re.IGNORECASE),
        re.compile(rf"(?:จำนวนเงิน|ยอดชำระ|ชำระเงิน|total\s*amount)[:\s]*{amt_token}", re.IGNORECASE),
        re.compile(rf"(?<!sub\s)(?<!sub-)\b(?:total)\b[:\s]*{amt_token}", re.IGNORECASE),
    ]
    vat_patterns = [
        re.compile(rf"(?<!before\s)(?<!pre-)(?:ภาษีมูลค่าเพิ่ม|\bvat\b)\s*(?:\d+(?:\.\d+)?\s*%)?[:\s]+{amt_token}", re.IGNORECASE),
    ]
    subtotal_patterns = [
        re.compile(rf"(?:รวมเป็นเงิน|ยอดก่อนภาษี|มูลค่าสินค้า|before\s*vat|sub\s*total|subtotal)[:\s]*{amt_token}", re.IGNORECASE),
    ]

    total_amount = _extract_amount_for_patterns(lines, total_patterns)
    vat_amount = _extract_amount_for_patterns(lines, vat_patterns)
    subtotal = _extract_amount_for_patterns(lines, subtotal_patterns)

    if total_amount == 0.0:
        fallback_baht = re.compile(r"([\d,]+\.\d{2})\s*(?:บาท|thb|฿)", re.IGNORECASE)
        candidates = [_parse_amount(m.group(1)) for ln in lines for m in fallback_baht.finditer(ln)]
        if candidates:
            total_amount = max(candidates)

    if subtotal == 0.0 and total_amount > 0.0:
        subtotal = round(max(0.0, total_amount - vat_amount), 2)
    elif total_amount == 0.0 and subtotal > 0.0:
        total_amount = round(subtotal + vat_amount, 2)

    return {
        "merchant": _infer_merchant(lines),
        "expense_date": _extract_date(text or ""),
        "subtotal": round(subtotal, 2),
        "vat_amount": round(vat_amount, 2),
        "total_amount": round(total_amount, 2),
        "currency": "THB",
        "expense_category": _infer_expense_category(text or ""),
        "extraction_method": "regex",
    }


def _strip_json_fences(raw_text: str) -> str:
    cleaned = (raw_text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _to_float(val: Any, default: float = 0.0) -> float:
    try:
        return round(float(val), 2)
    except (TypeError, ValueError):
        return default


_LLM_SYSTEM_PROMPT = (
    "You extract structured metadata and receipt/expense fields strictly from Thai/English OCR text. "
    "Return ONLY valid JSON with keys: "
    "is_expense (boolean), doc_category (one of: general, receipt, tax, official, contract, manual), "
    "tags (array of 1-5 short Thai/English tags), merchant (string), expense_date (YYYY-MM-DD or ''), "
    "subtotal (number), vat_amount (number), total_amount (number), currency (string like THB), "
    "expense_category (one of: food, transport, utilities, office, other). "
    "Convert Thai Buddhist Era years (>= 2400) to Gregorian YYYY-MM-DD."
)


def extract_with_llm(text: str, config: AppConfig) -> Dict[str, Any]:
    """Call optional text-only LLM API (Gemini or OpenAI-compatible/Ollama) using OCR text only."""
    if not is_llm_configured(config):
        raise ValueError("LLM endpoint is not configured")

    prompt_text = f"{_LLM_SYSTEM_PROMPT}\n\nOCR_TEXT:\n{text[:8000]}"
    provider = (config.llm_provider or "gemini").lower()

    if provider == "gemini":
        base = (config.llm_base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        model = config.llm_model or "gemini-2.5-flash"
        url = f"{base}/models/{model}:generateContent"
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json", "x-goog-api-key": config.llm_api_key},
            json={
                "contents": [{"parts": [{"text": prompt_text}]}],
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json",
                },
            },
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        raw_content = payload["candidates"][0]["content"]["parts"][0]["text"]
    else:
        base = (config.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        url = f"{base}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if config.llm_api_key:
            headers["Authorization"] = f"Bearer {config.llm_api_key}"
        resp = requests.post(
            url,
            headers=headers,
            json={
                "model": config.llm_model or "gpt-4o-mini",
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                    {"role": "user", "content": f"OCR_TEXT:\n{text[:8000]}"},
                ],
            },
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        raw_content = payload["choices"][0]["message"]["content"]

    parsed = json.loads(_strip_json_fences(raw_content))
    if not isinstance(parsed, dict):
        raise ValueError("LLM output was not a JSON object")

    valid_doc_cats = {"general", "receipt", "tax", "official", "contract", "manual"}
    valid_exp_cats = {"food", "transport", "utilities", "office", "other"}
    doc_cat = str(parsed.get("doc_category") or "general").strip().lower()
    exp_cat = str(parsed.get("expense_category") or "other").strip().lower()
    raw_tags = parsed.get("tags") or []
    tags = [str(t).strip() for t in raw_tags if str(t).strip()][:5] if isinstance(raw_tags, list) else []

    return {
        "is_expense": bool(parsed.get("is_expense", False)),
        "doc_category": doc_cat if doc_cat in valid_doc_cats else "general",
        "tags": tags,
        "expense": {
            "merchant": str(parsed.get("merchant") or "").strip()[:120],
            "expense_date": str(parsed.get("expense_date") or "").strip()[:10],
            "subtotal": _to_float(parsed.get("subtotal"), 0.0),
            "vat_amount": _to_float(parsed.get("vat_amount"), 0.0),
            "total_amount": _to_float(parsed.get("total_amount"), 0.0),
            "currency": str(parsed.get("currency") or "THB").strip() or "THB",
            "expense_category": exp_cat if exp_cat in valid_exp_cats else "other",
            "extraction_method": "llm",
        },
    }


def extract_document_intelligence(
    text: str,
    config: AppConfig,
    *,
    extraction_method: str = "auto",
    require_expense_hint: bool = False,
) -> Dict[str, Any]:
    """Run hybrid extraction (text-only LLM when configured, offline Thai Regex fallback)."""
    clean_method = (extraction_method or "auto").strip().lower()
    if clean_method in ("auto", "llm") and is_llm_configured(config):
        try:
            return extract_with_llm(text, config)
        except Exception as exc:
            print(f"[Extractor] LLM extraction failed, falling back to offline regex: {exc}")

    likely_exp = is_likely_expense(text)
    expense_data = extract_expense_regex(text) if (likely_exp or not require_expense_hint) else None
    return {
        "is_expense": likely_exp if require_expense_hint else True,
        "doc_category": "receipt" if likely_exp else "general",
        "tags": [],
        "expense": expense_data,
    }
