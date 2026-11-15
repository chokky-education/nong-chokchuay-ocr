# Upgrade Default LLM to `gemini-4-hydrogen` & Natural Thai Kindergarten Persona Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Nong ChokChuay's default LLM to `gemini-4-hydrogen` with thinking-safe parsing and replace the stiff robotic system prompt with a natural, empathetic Thai kindergarten teacher sidekick persona (`temperature=0.65`, Dual-Mode Grounding, no forced greeting prefix).

**Architecture:** Define `DEFAULT_GEMINI_MODEL = "gemini-4-hydrogen"` in `config.py` and propagate it across `line_service.py`, `extractor.py`, `worker.js`, `wrangler.toml`, and `.env.example`. Add `extract_gemini_text(payload)` to ignore `thought: true` parts and safely handle empty/truncated candidates, increase `maxOutputTokens` (`2048` chat / `4096` extractor) and `timeout` (`20`s), and rewrite `build_nong_chok_system_instruction()` + `generate_nong_chok_ai_reply()` for natural Thai LINE conversation without forced `"สวัสดีครับ..."` prepending.

**Tech Stack:** Python 3.11+, FastAPI, `requests`, `unittest`, Cloudflare Workers (`worker.js`, `wrangler.toml`), Node built-in test runner (`node --test`).

**Spec:** `docs/superpowers/specs/2026-11-15-gemini-4-hydrogen-design.md`

## Global Constraints

- Default Gemini model ID must be `"gemini-4-hydrogen"` everywhere `gemini-2.5-flash` was previously defaulted (`config.py`, `line_service.py`, `extractor.py`, `worker.js`, `wrangler.toml`, `.env.example`, `docs/STATUS.md`).
- Raw images/PDFs are NEVER sent externally — only OCR text is sent (`docs/adr/0001`, `AGENTS.md` §3).
- When LLM fails, times out, or returns empty output, `extractor.py` falls back to offline Thai Regex and `line_service.py` falls back cleanly without crashing (`CODING_STANDARDS.md` §3).
- Natural persona replies must NOT forcibly prepend `"สวัสดีครับ{target_teacher}!\n"` when the model naturally omits the teacher's name in a turn, and chat `temperature` must be `0.65` (extractor remains `0.1`).
- Preserve all uncommitted user work in `/Users/chalisa/openthai-ocr-app` while syncing runtime changes and `.env` locally (`AGENTS.md` §5).

## Review Focus

- **Gemini `thought: true` part precedes answer text:** `candidates[0].content.parts` contains `[{"thought": True, "text": "internal reasoning"}, {"text": "คำตอบจริง"}]` — both `line_service.py` and `extractor.py` must skip `thought` parts and return only `"คำตอบจริง"`.
- **Gemini `MAX_TOKENS` / missing `parts` payload:** `candidates[0].content` is `{"role": "model"}` with no `"parts"` key — both modules must treat output as empty and fall back cleanly without unhandled `KeyError`.
- **Natural reply without teacher name is NOT mutated with robotic greeting:** When Gemini returns `"ได้เลยครับ เดี๋ยวน้องโชคร่างข้อความส่งผู้ปกครองให้นะครับ"`, `generate_nong_chok_ai_reply` returns it verbatim without prepending `"สวัสดีครับครูพิ้งกี้!\n"`.
- **Blank `LLM_MODEL=""` in `.env` or environment:** `AppConfig.from_env()`, `line_service.py`, `extractor.py`, and `worker.js` `buildContainerEnv({})` resolve `"gemini-4-hydrogen"`, never `""` or `"gemini-2.5-flash"`.
- **Cloudflare Worker container env & fingerprint:** `buildContainerEnv({})` sets `LLM_MODEL: "gemini-4-hydrogen"` and `computeConfigFingerprint` changes when `LLM_MODEL` changes so warm Durable Object containers restart cleanly.

---

### Task 1: Upgrade Python Config, Thinking-Safe Gemini Adapter, and Thai Kindergarten Persona

**Files:**
- Modify: `config.py:5-10`, `config.py:103-107`, `config.py:183-187`
- Modify: `extractor.py:9`, `extractor.py:693-725`
- Modify: `line_service.py:10-15`, `line_service.py:43-57`, `line_service.py:101-171`, `line_service.py:530-540`
- Test: `tests/test_llm_hybrid.py`
- Test: `tests/test_nong_chokchuay.py`

**Interfaces:**
- Produces:
  - `config.DEFAULT_GEMINI_MODEL: str = "gemini-4-hydrogen"`
  - `extractor.extract_gemini_text(payload: Dict[str, Any]) -> str`
  - `line_service.build_nong_chok_system_instruction(teacher_name: str = DEFAULT_TEACHER_NAME) -> str`

- [ ] **Step 1: Write failing tests in `tests/test_llm_hybrid.py` and `tests/test_nong_chokchuay.py`**

Update existing `"gemini-2.5-flash"` assertions and add tests covering:
1. `AppConfig.from_env()` defaulting `llm_model == "gemini-4-hydrogen"` when `LLM_MODEL` is unset or blank (`"LLM_MODEL=   \n"`).
2. `extractor.extract_with_llm` calling `models/gemini-4-hydrogen:generateContent` with `timeout=20`, `"maxOutputTokens": 4096`, and skipping `{"thought": True, "text": "..."}` parts before parsing JSON.
3. `line_service.build_nong_chok_system_instruction()` containing `"ครูพิ้งกี้"`, `"น้องโชค"`, early-childhood context (`"อนุบาล"`, `"ผู้ปกครอง"`, `"แพ้อาหาร"`), Dual-Mode guidance, and forbidding repetitive `"สวัสดีครับ"` prefixes and technical jargon.
4. `line_service.generate_nong_chok_ai_reply()` calling `models/gemini-4-hydrogen:generateContent` with `temperature == 0.65`, `maxOutputTokens == 2048`, `timeout == 20`, skipping `thought: True` parts, NOT prepending `"สวัสดีครับครูพิ้งกี้!"` when the reply omits the teacher's name, and returning `None` cleanly on `MAX_TOKENS` missing `parts`.

- [ ] **Step 2: Run Python unit tests to verify failure**

Run: `PYTHONPATH=.worktrees/gemini-4-hydrogen /Users/chalisa/openthai-ocr-app/.venv/bin/python -m unittest discover -s .worktrees/gemini-4-hydrogen/tests -v`
Expected: FAIL (`"gemini-2.5-flash" != "gemini-4-hydrogen"`, temperature/token/persona assertions fail).

- [ ] **Step 3: Implement `DEFAULT_GEMINI_MODEL`, `extract_gemini_text`, and new `build_nong_chok_system_instruction`**

In `config.py`:
- Add `DEFAULT_GEMINI_MODEL = "gemini-4-hydrogen"`.
- Default `AppConfig.llm_model: str = DEFAULT_GEMINI_MODEL` and `from_env()` `llm_model=(os.environ.get("LLM_MODEL") or "").strip() or DEFAULT_GEMINI_MODEL`.

In `extractor.py`:
- Import `DEFAULT_GEMINI_MODEL` from `config`.
- Implement `extract_gemini_text(payload: Dict[str, Any]) -> str` skipping `part.get("thought") is True` and safely handling empty `candidates`/`content`/`parts`.
- In `extract_with_llm`: use `model = (config.llm_model or "").strip() or DEFAULT_GEMINI_MODEL`, `"maxOutputTokens": 4096`, `timeout=20`, and `raw_content = extract_gemini_text(payload)` (raising `ValueError("Gemini returned empty response")` if empty).

In `line_service.py`:
- Import `DEFAULT_GEMINI_MODEL` from `config` and `extract_gemini_text` from `extractor`.
- Rewrite `build_nong_chok_system_instruction(teacher_name)` with the approved Thai kindergarten assistant Dual-Mode prompt:
```python
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
```
- In `generate_nong_chok_ai_reply`: use `DEFAULT_GEMINI_MODEL`, `temperature: 0.65` (both Gemini and OpenAI-compatible branches), `maxOutputTokens: 2048`, `timeout=20`, parse Gemini output with `extract_gemini_text(payload).strip()`, and remove the forced `if target_teacher not in reply:` greeting prefix.
- In `process_line_text_command`: return `"model": (config.llm_model or DEFAULT_GEMINI_MODEL) if config else DEFAULT_GEMINI_MODEL`.

- [ ] **Step 4: Run Python syntax check and unit tests to verify pass**

Run:
`/Users/chalisa/openthai-ocr-app/.venv/bin/python -m py_compile .worktrees/gemini-4-hydrogen/app.py .worktrees/gemini-4-hydrogen/config.py .worktrees/gemini-4-hydrogen/database.py .worktrees/gemini-4-hydrogen/extractor.py .worktrees/gemini-4-hydrogen/line_service.py .worktrees/gemini-4-hydrogen/ocr_service.py .worktrees/gemini-4-hydrogen/watcher.py`
and
`PYTHONPATH=.worktrees/gemini-4-hydrogen /Users/chalisa/openthai-ocr-app/.venv/bin/python -m unittest discover -s .worktrees/gemini-4-hydrogen/tests -v`
Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git -C .worktrees/gemini-4-hydrogen add config.py extractor.py line_service.py tests/test_llm_hybrid.py tests/test_nong_chokchuay.py docs/superpowers/specs/2026-11-15-gemini-4-hydrogen-design.md docs/superpowers/plans/2026-11-15-gemini-4-hydrogen.md
git -C .worktrees/gemini-4-hydrogen commit -m "feat(llm): upgrade to gemini-4-hydrogen and natural Thai kindergarten persona"
```

---

### Task 2: Update Cloudflare Worker, Config Templates, Docs, PR, and Local Runtime

**Files:**
- Modify: `worker.js:18-20`
- Modify: `wrangler.toml:31-33`
- Modify: `.env.example:17-20`
- Modify: `docs/STATUS.md:17-19`
- Test: `tests/test_worker_js.mjs`

**Interfaces:**
- Consumes: `LLM_MODEL` default `"gemini-4-hydrogen"`
- Produces: `buildContainerEnv(env)` defaulting `LLM_MODEL` to `"gemini-4-hydrogen"`

- [ ] **Step 1: Add failing assertions in `tests/test_worker_js.mjs`**

Assert `envOut.LLM_PROVIDER === 'gemini'`, `envOut.LLM_MODEL === 'gemini-4-hydrogen'`, blank `LLM_MODEL: '   '` resolves to `'gemini-4-hydrogen'`, and `computeConfigFingerprint` changes when `LLM_MODEL` changes.

- [ ] **Step 2: Run Node Worker test to verify failure**

Run: `node --test .worktrees/gemini-4-hydrogen/tests/test_worker_js.mjs`
Expected: FAIL (`'gemini-2.5-flash' !== 'gemini-4-hydrogen'`).

- [ ] **Step 3: Update `worker.js`, `wrangler.toml`, `.env.example`, and `docs/STATUS.md`**

Update all defaults and status descriptions to `gemini-4-hydrogen` and note the upgraded natural Thai kindergarten assistant persona in `docs/STATUS.md`.

- [ ] **Step 4: Run Node tests and full Python test suite**

Run:
`node --test .worktrees/gemini-4-hydrogen/tests/test_worker_js.mjs .worktrees/gemini-4-hydrogen/tests/test_frontend_auth.mjs`
and
`PYTHONPATH=.worktrees/gemini-4-hydrogen /Users/chalisa/openthai-ocr-app/.venv/bin/python -m unittest discover -s .worktrees/gemini-4-hydrogen/tests -v`
Expected: PASS, and `rg "gemini-2\.5-flash" .worktrees/gemini-4-hydrogen --glob '!docs/superpowers/**'` returns zero matches.

- [ ] **Step 5: Commit Task 2, Open PR & Check CI, and Sync Local Dev Server**

1. Commit in `.worktrees/gemini-4-hydrogen`, push `codex/gemini-4-hydrogen`, open PR against `main`, and verify GitHub Actions CI passes (`gh pr checks`).
2. Apply the same non-destructive `gemini-4-hydrogen` + System Prompt updates to `/Users/chalisa/openthai-ocr-app` (preserving all uncommitted mascot card work) and update `LLM_MODEL=gemini-4-hydrogen` in `.env` without modifying secret values.
3. Restart local server on `127.0.0.1:8765` and verify live `/api/system` + `/api/line/simulate` chat response.
