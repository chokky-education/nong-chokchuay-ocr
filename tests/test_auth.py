import os
import unittest
from datetime import datetime, timedelta, timezone
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


if __name__ == "__main__":
    unittest.main()
