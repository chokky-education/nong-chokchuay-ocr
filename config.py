import os
from dataclasses import dataclass
from typing import Dict, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_dotenv_file(env_path: Optional[str] = None) -> Dict[str, str]:
    """Load key=value pairs from a .env file without overriding existing env vars."""
    path = env_path or os.path.join(BASE_DIR, ".env")
    loaded: Dict[str, str] = {}
    if not os.path.exists(path):
        return loaded

    parsed: Dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            if key and (val != "" or key not in parsed):
                parsed[key] = val

    for key, val in parsed.items():
        if key not in os.environ:
            os.environ[key] = val
        loaded[key] = os.environ[key]
    return loaded


def _parse_bool(raw: Optional[str], default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class AppConfig:
    """Runtime paths and optional integration settings for Nong ChokChuay SaaS."""

    data_dir: str
    db_path: str
    uploads_dir: str
    inbox_dir: str
    inbox_failed_dir: str
    inbox_poll_seconds: float = 3.0
    auto_extract_on_ingest: bool = False
    llm_provider: str = "gemini"
    llm_api_key: str = ""
    llm_model: str = "gemini-2.5-flash"
    llm_base_url: str = ""
    line_channel_secret: str = ""
    line_channel_access_token: str = ""
    public_base_url: str = ""

    @classmethod
    def from_dir(cls, data_dir: str, **overrides: object) -> "AppConfig":
        abs_data = os.path.abspath(data_dir)
        cfg = cls(
            data_dir=abs_data,
            db_path=os.path.join(abs_data, "documents.db"),
            uploads_dir=os.path.join(abs_data, "uploads"),
            inbox_dir=os.path.join(abs_data, "inbox"),
            inbox_failed_dir=os.path.join(abs_data, "inbox_failed"),
        )
        for key, value in overrides.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        return cfg

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_dotenv_file()
        data_dir = os.environ.get("OPENTHAI_DATA_DIR") or os.path.join(BASE_DIR, "data")
        try:
            poll_seconds = float(os.environ.get("INBOX_POLL_SECONDS", "3.0"))
        except ValueError:
            poll_seconds = 3.0
        raw_api_key = (
            os.environ.get("LLM_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or ""
        ).strip()
        return cls.from_dir(
            data_dir=data_dir,
            inbox_poll_seconds=poll_seconds,
            auto_extract_on_ingest=_parse_bool(os.environ.get("AUTO_EXTRACT_ON_INGEST"), True),
            llm_provider=(os.environ.get("LLM_PROVIDER") or "gemini").strip().lower(),
            llm_api_key=raw_api_key,
            llm_model=(os.environ.get("LLM_MODEL") or "gemini-2.5-flash").strip(),
            llm_base_url=(os.environ.get("LLM_BASE_URL") or "").strip(),
            line_channel_secret=(os.environ.get("LINE_CHANNEL_SECRET") or "").strip(),
            line_channel_access_token=(os.environ.get("LINE_CHANNEL_ACCESS_TOKEN") or "").strip(),
            public_base_url=(os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/"),
        )

    def ensure_dirs(self) -> None:
        teacher_inbox = os.path.join(self.inbox_dir, "teacher")
        me_inbox = os.path.join(self.inbox_dir, "me")
        for path in (
            self.data_dir,
            self.uploads_dir,
            self.inbox_dir,
            teacher_inbox,
            me_inbox,
            self.inbox_failed_dir,
        ):
            os.makedirs(path, exist_ok=True)
