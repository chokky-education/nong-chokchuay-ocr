import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import config
import database
from config import AppConfig
from tests.fakes import make_temp_data_dir


class AuthConfigTests(unittest.TestCase):
    def test_from_env_defaults_auth_required_true_and_rejects_typos_and_prod_opt_out(self) -> None:
        with make_temp_data_dir() as tmpdir:
            with patch.object(config, "BASE_DIR", tmpdir):
                with patch.dict(os.environ, {"OPENTHAI_DATA_DIR": tmpdir}, clear=True):
                    cfg = AppConfig.from_env()
                    self.assertTrue(cfg.auth_required)
                    self.assertFalse(cfg.line_allow_group_chats)

                with patch.dict(os.environ, {"OPENTHAI_DATA_DIR": tmpdir, "AUTH_REQUIRED": "tru"}, clear=True):
                    with self.assertRaises(ValueError):
                        AppConfig.from_env()

                with patch.dict(
                    os.environ,
                    {
                        "OPENTHAI_DATA_DIR": tmpdir,
                        "AUTH_REQUIRED": "false",
                        "PUBLIC_BASE_URL": "https://nong-chokchuay-ocr.entrace-space.workers.dev",
                    },
                    clear=True,
                ):
                    with self.assertRaises(ValueError):
                        AppConfig.from_env()

                with patch.dict(
                    os.environ,
                    {"OPENTHAI_DATA_DIR": tmpdir, "AUTH_REQUIRED": "false", "APP_ENV": "production"},
                    clear=True,
                ):
                    with self.assertRaises(ValueError):
                        AppConfig.from_env()

                with patch.dict(
                    os.environ,
                    {
                        "OPENTHAI_DATA_DIR": tmpdir,
                        "AUTH_REQUIRED": "false",
                        "PUBLIC_BASE_URL": "http://127.0.0.1:8765",
                        "ALLOWED_EMAILS": " CGIKCHIK07@gmail.com , ppksaruenee@gmail.com ",
                        "ALLOWED_LINE_USER_IDS": " U111 , U222 ",
                    },
                    clear=True,
                ):
                    local_cfg = AppConfig.from_env()
                    self.assertFalse(local_cfg.auth_required)
                    self.assertEqual(local_cfg.allowed_emails, ("cgikchik07@gmail.com", "ppksaruenee@gmail.com"))
                    self.assertEqual(local_cfg.allowed_line_user_ids, ("U111", "U222"))

                with patch.dict(
                    os.environ,
                    {"OPENTHAI_DATA_DIR": tmpdir, "AUTH_SESSION_TTL_SECONDS": "0"},
                    clear=True,
                ):
                    with self.assertRaises(ValueError):
                        AppConfig.from_env()


class AuthSessionDbTests(unittest.TestCase):
    def test_auth_sessions_idempotent_init_create_expiry_and_revocation(self) -> None:
        with make_temp_data_dir() as tmpdir:
            db_path = os.path.join(tmpdir, "documents.db")
            database.init_db(db_path)

            now = datetime.now(timezone.utc)
            future_iso = (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            past_iso = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

            created = database.create_auth_session(
                session_id="sid-active-1",
                email="cgikchik07@gmail.com",
                name="Owner",
                picture="https://example.com/p.jpg",
                csrf_token="csrf-abc",
                expires_at=future_iso,
                db_path=db_path,
            )
            self.assertEqual(created["email"], "cgikchik07@gmail.com")
            self.assertEqual(created["csrf_token"], "csrf-abc")

            # Idempotent init_db must preserve active sessions
            database.init_db(db_path)
            active = database.get_active_auth_session("sid-active-1", now_iso=now.isoformat(), db_path=db_path)
            self.assertIsNotNone(active)
            self.assertEqual(active["email"], "cgikchik07@gmail.com")

            # Expired session returns None
            database.create_auth_session(
                session_id="sid-expired-1",
                email="ppksaruenee@gmail.com",
                name="Teacher",
                picture="",
                csrf_token="csrf-exp",
                expires_at=past_iso,
                db_path=db_path,
            )
            self.assertIsNone(database.get_active_auth_session("sid-expired-1", db_path=db_path))

            # Revoked session immediately returns None
            self.assertTrue(database.revoke_auth_session("sid-active-1", db_path=db_path))
            self.assertFalse(database.revoke_auth_session("sid-missing", db_path=db_path))
            self.assertIsNone(database.get_active_auth_session("sid-active-1", db_path=db_path))


def _make_fake_request(url: str, headers: dict = None, cookies: dict = None):
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return SimpleNamespace(
        url=SimpleNamespace(scheme=parsed.scheme, netloc=parsed.netloc, hostname=parsed.hostname, port=parsed.port),
        base_url=f"{parsed.scheme}://{parsed.netloc}/",
        headers={k.lower(): v for k, v in (headers or {}).items()},
        cookies=cookies or {},
    )


class AuthServiceTests(unittest.TestCase):
    def test_external_url_and_secure_cookie_ignore_internal_http_and_spoofed_host(self) -> None:
        import auth_service

        with make_temp_data_dir() as tmpdir:
            cfg = AppConfig.from_dir(
                tmpdir,
                app_env="production",
                auth_required=True,
                public_base_url="https://nong-chokchuay-ocr.entrace-space.workers.dev",
            )
            req = _make_fake_request(
                "http://container/api/auth/google/login",
                headers={"Host": "evil.example", "X-Forwarded-Host": "evil.example"},
            )
            self.assertEqual(
                auth_service.resolve_external_base_url(req, cfg),
                "https://nong-chokchuay-ocr.entrace-space.workers.dev",
            )
            self.assertTrue(auth_service.should_use_secure_cookie(req, cfg))
            self.assertEqual(
                auth_service.get_allowed_origins(req, cfg),
                ("https://nong-chokchuay-ocr.entrace-space.workers.dev",),
            )

            # Local dev with empty public_base_url clamps untrusted hosts back to localhost/127.0.0.1
            local_cfg = AppConfig.from_dir(tmpdir, app_env="development", auth_required=True, public_base_url="")
            spoof_req = _make_fake_request("http://evil.example:8765/api/auth/google/login")
            self.assertEqual(auth_service.resolve_external_base_url(spoof_req, local_cfg), "http://127.0.0.1:8765")
            self.assertFalse(auth_service.should_use_secure_cookie(spoof_req, local_cfg))

    def test_signed_cookie_tamper_expiry_revocation_and_allowlist_removal(self) -> None:
        import auth_service

        with make_temp_data_dir() as tmpdir:
            database.init_db(os.path.join(tmpdir, "documents.db"))
            cfg = AppConfig.from_dir(
                tmpdir,
                auth_required=True,
                allowed_emails=("cgikchik07@gmail.com", "ppksaruenee@gmail.com"),
                auth_session_secret="test-secret-at-least-32-bytes-long-1234",
            )
            exp_ts = int(time.time()) + 3600
            exp_iso = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            database.create_auth_session(
                session_id="sid-1",
                email="cgikchik07@gmail.com",
                name="Owner",
                picture="",
                csrf_token="csrf-1",
                expires_at=exp_iso,
                db_path=cfg.db_path,
            )
            token = auth_service.sign_payload({"sid": "sid-1", "email": "cgikchik07@gmail.com", "exp": exp_ts}, cfg.auth_session_secret)
            req = _make_fake_request("https://nong-chokchuay-ocr.entrace-space.workers.dev/api/system", cookies={auth_service.SESSION_COOKIE_NAME: token})
            self.assertIsNotNone(auth_service.authenticate_request(req, cfg))

            # Tampered token rejected
            tampered = token[:-4] + "AAAA"
            req_tampered = _make_fake_request("https://nong-chokchuay-ocr.entrace-space.workers.dev/api/system", cookies={auth_service.SESSION_COOKIE_NAME: tampered})
            self.assertIsNone(auth_service.authenticate_request(req_tampered, cfg))

            # Email removed from allowed_emails rejected immediately even with valid session
            cfg_removed = AppConfig.from_dir(
                tmpdir,
                auth_required=True,
                allowed_emails=("ppksaruenee@gmail.com",),
                auth_session_secret=cfg.auth_session_secret,
            )
            self.assertIsNone(auth_service.authenticate_request(req, cfg_removed))

            # Revoked in SQLite rejected immediately
            database.revoke_auth_session("sid-1", db_path=cfg.db_path)
            self.assertIsNone(auth_service.authenticate_request(req, cfg))

    def test_validate_csrf_and_origin(self) -> None:
        import auth_service

        with make_temp_data_dir() as tmpdir:
            cfg = AppConfig.from_dir(
                tmpdir,
                app_env="production",
                auth_required=True,
                public_base_url="https://nong-chokchuay-ocr.entrace-space.workers.dev",
            )
            session = {"session_id": "sid-1", "csrf_token": "csrf-xyz"}

            ok_req = _make_fake_request(
                "http://container/api/students",
                headers={
                    "Origin": "https://nong-chokchuay-ocr.entrace-space.workers.dev",
                    "X-CSRF-Token": "csrf-xyz",
                },
            )
            self.assertTrue(auth_service.validate_csrf_and_origin(ok_req, cfg, session))

            referer_only_req = _make_fake_request(
                "http://container/api/students",
                headers={
                    "Referer": "https://nong-chokchuay-ocr.entrace-space.workers.dev/?doc=123",
                    "X-CSRF-Token": "csrf-xyz",
                },
            )
            self.assertTrue(auth_service.validate_csrf_and_origin(referer_only_req, cfg, session))

            bad_origin_req = _make_fake_request(
                "http://container/api/students",
                headers={"Origin": "https://evil.example", "X-CSRF-Token": "csrf-xyz"},
            )
            self.assertFalse(auth_service.validate_csrf_and_origin(bad_origin_req, cfg, session))

            missing_origin_req = _make_fake_request(
                "http://container/api/students",
                headers={"X-CSRF-Token": "csrf-xyz"},
            )
            self.assertFalse(auth_service.validate_csrf_and_origin(missing_origin_req, cfg, session))

            wrong_csrf_req = _make_fake_request(
                "http://container/api/students",
                headers={"Origin": "https://nong-chokchuay-ocr.entrace-space.workers.dev", "X-CSRF-Token": "wrong"},
            )
            self.assertFalse(auth_service.validate_csrf_and_origin(wrong_csrf_req, cfg, session))


if __name__ == "__main__":
    unittest.main()
