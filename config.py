import os
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple, Union
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_AUTH_SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
_TRUTHY_VALUES = {"1", "true", "yes", "on"}
_FALSY_VALUES = {"0", "false", "no", "off"}


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
    return raw.strip().lower() in _TRUTHY_VALUES


def _parse_strict_bool(raw: Optional[str], default: bool, field_name: str) -> bool:
    """Parse boolean setting strictly; raise ValueError on unrecognized values."""
    if raw is None:
        return default
    cleaned = raw.strip().lower()
    if not cleaned:
        return default
    if cleaned in _TRUTHY_VALUES:
        return True
    if cleaned in _FALSY_VALUES:
        return False
    raise ValueError(f"Invalid boolean value for {field_name}: {raw!r}")


def _parse_csv_tuple(
    raw: Optional[Union[str, Iterable[str]]],
    lowercase: bool = False,
) -> Tuple[str, ...]:
    """Normalize a comma-separated string or iterable into an ordered unique tuple."""
    if raw is None:
        return ()
    if isinstance(raw, str):
        items = raw.split(",")
    else:
        items = [str(item) for item in raw]
    seen = set()
    result = []
    for item in items:
        cleaned = item.strip()
        if lowercase:
            cleaned = cleaned.lower()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return tuple(result)


def _is_local_dev_url(url: str) -> bool:
    cleaned = (url or "").strip()
    if not cleaned:
        return True
    parsed = urlparse(cleaned)
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and host in {"127.0.0.1", "localhost"}


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
    teacher_name: str = "ครูพิ้งกี้"
    app_env: str = "development"
    auth_required: bool = False
    allowed_emails: Tuple[str, ...] = ()
    google_client_id: str = ""
    google_client_secret: str = ""
    auth_session_secret: str = ""
    auth_session_ttl_seconds: int = DEFAULT_AUTH_SESSION_TTL_SECONDS
    allowed_line_user_ids: Tuple[str, ...] = ()
    line_allow_group_chats: bool = False

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
                if key == "allowed_emails":
                    setattr(cfg, key, _parse_csv_tuple(value, lowercase=True))  # type: ignore[arg-type]
                elif key == "allowed_line_user_ids":
                    setattr(cfg, key, _parse_csv_tuple(value, lowercase=False))  # type: ignore[arg-type]
                else:
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

        app_env = (os.environ.get("APP_ENV") or os.environ.get("ENVIRONMENT") or "development").strip().lower()
        public_base_url = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
        auth_required = _parse_strict_bool(
            os.environ.get("AUTH_REQUIRED"),
            default=True,
            field_name="AUTH_REQUIRED",
        )
        if not auth_required and (app_env == "production" or not _is_local_dev_url(public_base_url)):
            raise ValueError(
                "AUTH_REQUIRED cannot be disabled when APP_ENV=production or PUBLIC_BASE_URL is non-local."
            )

        raw_ttl = (os.environ.get("AUTH_SESSION_TTL_SECONDS") or "").strip()
        if not raw_ttl:
            session_ttl = DEFAULT_AUTH_SESSION_TTL_SECONDS
        else:
            try:
                session_ttl = int(raw_ttl)
            except ValueError as exc:
                raise ValueError(f"Invalid AUTH_SESSION_TTL_SECONDS: {raw_ttl!r}") from exc
            if session_ttl <= 0:
                raise ValueError("AUTH_SESSION_TTL_SECONDS must be positive.")

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
            public_base_url=public_base_url,
            teacher_name=(os.environ.get("TEACHER_NAME") or "ครูพิ้งกี้").strip() or "ครูพิ้งกี้",
            app_env=app_env,
            auth_required=auth_required,
            allowed_emails=_parse_csv_tuple(os.environ.get("ALLOWED_EMAILS"), lowercase=True),
            google_client_id=(os.environ.get("GOOGLE_CLIENT_ID") or "").strip(),
            google_client_secret=(os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip(),
            auth_session_secret=(os.environ.get("AUTH_SESSION_SECRET") or "").strip(),
            auth_session_ttl_seconds=session_ttl,
            allowed_line_user_ids=_parse_csv_tuple(os.environ.get("ALLOWED_LINE_USER_IDS"), lowercase=False),
            line_allow_group_chats=_parse_strict_bool(
                os.environ.get("LINE_ALLOW_GROUP_CHATS"),
                default=False,
                field_name="LINE_ALLOW_GROUP_CHATS",
            ),
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
