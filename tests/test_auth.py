import os
import unittest
from unittest.mock import patch

import config
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


if __name__ == "__main__":
    unittest.main()
