import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

import config
import database
from app import create_app
from config import AppConfig
from tests.fakes import FakeLineOCR, make_png_bytes, make_temp_data_dir


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


class AuthApiIntegrationTests(unittest.TestCase):
    def _make_auth_config(self, tmpdir: str, **overrides) -> AppConfig:
        defaults = dict(
            inbox_poll_seconds=0,
            app_env="production",
            auth_required=True,
            allowed_emails=("cgikchik07@gmail.com", "ppksaruenee@gmail.com"),
            google_client_id="google-client-id-123",
            google_client_secret="google-client-secret-456",
            auth_session_secret="super-secret-session-signing-key-32bytes!",
            public_base_url="https://nong-chokchuay-ocr.entrace-space.workers.dev",
            line_channel_secret="line-secret-xyz",
            allowed_line_user_ids=("U-OWNER-1", "U-TEACHER-2"),
        )
        defaults.update(overrides)
        return AppConfig.from_dir(tmpdir, **defaults)

    def test_protected_routes_fail_closed_401_and_public_allowlist_and_static_mascot_cards(self) -> None:
        with make_temp_data_dir() as tmpdir:
            static_dir = os.path.join(tmpdir, "custom_static")
            os.makedirs(os.path.join(static_dir, "mascot_cards"), exist_ok=True)
            with open(os.path.join(static_dir, "index.html"), "w", encoding="utf-8") as fh:
                fh.write("<html><body>login</body></html>")
            with open(os.path.join(static_dir, "styles.css"), "w", encoding="utf-8") as fh:
                fh.write("body {}")
            with open(os.path.join(static_dir, "app.js"), "w", encoding="utf-8") as fh:
                fh.write("console.log('ok');")
            with open(os.path.join(static_dir, "mascot.jpg"), "wb") as fh:
                fh.write(make_png_bytes(16, 16))
            with open(os.path.join(static_dir, "mascot_cards", "sample.jpg"), "wb") as fh:
                fh.write(make_png_bytes(16, 16))
            with open(os.path.join(static_dir, "private_note.txt"), "w", encoding="utf-8") as fh:
                fh.write("secret")

            cfg = self._make_auth_config(tmpdir)
            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR(), static_dir=static_dir)

            with TestClient(test_app, base_url="https://nong-chokchuay-ocr.entrace-space.workers.dev") as client:
                # Public routes succeed without auth
                self.assertEqual(client.get("/").status_code, 200)
                health = client.get("/api/health")
                self.assertEqual(health.status_code, 200)
                health_json = health.json()
                self.assertEqual(health_json["status"], "ok")
                self.assertTrue(health_json["auth_required"])
                self.assertTrue(health_json["oauth_configured"])
                self.assertNotIn("database", health_json)
                self.assertNotIn("path", health_json)

                me = client.get("/api/auth/me")
                self.assertEqual(me.status_code, 200)
                self.assertFalse(me.json()["authenticated"])
                self.assertIsNone(me.json().get("csrf_token"))

                self.assertEqual(client.get("/static/styles.css").status_code, 200)
                self.assertEqual(client.get("/static/mascot.jpg").status_code, 200)
                self.assertEqual(client.get("/static/mascot_cards/sample.jpg").status_code, 200)

                # Traversal or non-allowlisted static files blocked
                self.assertEqual(client.get("/static/private_note.txt").status_code, 401)
                self.assertEqual(client.get("/static/mascot_cards/../private_note.txt").status_code, 401)

                # Protected endpoints return 401
                for path in (
                    "/api/system",
                    "/api/samples",
                    "/api/documents",
                    "/api/expenses",
                    "/api/students",
                    "/api/database/download",
                    "/api/watcher/status",
                ):
                    self.assertEqual(client.get(path).status_code, 401, f"Expected 401 for GET {path}")

                for path in ("/api/ocr", "/api/line/simulate", "/api/students"):
                    self.assertEqual(client.post(path).status_code, 401, f"Expected 401 for POST {path}")

    def test_case_a_proxy_internal_http_sets_secure_cookies_and_external_redirect_uri(self) -> None:
        import auth_service

        with make_temp_data_dir() as tmpdir:
            cfg = self._make_auth_config(tmpdir)
            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR())

            mock_token_resp = MagicMock()
            mock_token_resp.raise_for_status.return_value = None
            mock_token_resp.json.return_value = {"access_token": "tok-123"}
            mock_user_resp = MagicMock()
            mock_user_resp.raise_for_status.return_value = None
            mock_user_resp.json.return_value = {
                "email": "cgikchik07@gmail.com",
                "email_verified": True,
                "name": "Owner",
                "picture": "",
            }

            with patch("auth_service.requests.post", return_value=mock_token_resp), patch(
                "auth_service.requests.get", return_value=mock_user_resp
            ):
                with TestClient(test_app, base_url="http://container") as client:
                    login_resp = client.get("/api/auth/google/login", follow_redirects=False)
                    self.assertEqual(login_resp.status_code, 302)
                    loc = login_resp.headers["location"]
                    qs = parse_qs(urlparse(loc).query)
                    self.assertEqual(
                        qs["redirect_uri"][0],
                        "https://nong-chokchuay-ocr.entrace-space.workers.dev/api/auth/google/callback",
                    )
                    state_val = qs["state"][0]
                    state_set_cookie = "; ".join(login_resp.headers.get_list("set-cookie"))
                    self.assertIn(f"{auth_service.OAUTH_STATE_COOKIE_NAME}=", state_set_cookie)
                    self.assertIn("HttpOnly", state_set_cookie)
                    self.assertIn("Secure", state_set_cookie)

                    # Extract raw cookie pair and forward explicitly (since http:// won't auto-send Secure cookies)
                    raw_cookie_pair = login_resp.headers.get_list("set-cookie")[0].split(";", 1)[0]
                    cb_resp = client.get(
                        f"/api/auth/google/callback?code=auth-code-1&state={state_val}",
                        headers={"Cookie": raw_cookie_pair},
                        follow_redirects=False,
                    )
                    self.assertEqual(cb_resp.status_code, 302)
                    self.assertEqual(cb_resp.headers["location"], "/")
                    set_cookies = cb_resp.headers.get_list("set-cookie")
                    session_hdr = [c for c in set_cookies if c.startswith(f"{auth_service.SESSION_COOKIE_NAME}=")][0]
                    csrf_hdr = [c for c in set_cookies if c.startswith(f"{auth_service.CSRF_COOKIE_NAME}=")][0]
                    self.assertIn("HttpOnly", session_hdr)
                    self.assertIn("Secure", session_hdr)
                    self.assertIn("Secure", csrf_hdr)

    def test_case_b_https_oauth_flow_allowlist_csrf_and_logout_revocation(self) -> None:
        import auth_service

        with make_temp_data_dir() as tmpdir:
            cfg = self._make_auth_config(tmpdir)
            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR())
            origin = "https://nong-chokchuay-ocr.entrace-space.workers.dev"

            with TestClient(test_app, base_url=origin) as client:
                # 1. Invalid/missing/expired OAuth state MUST fail BEFORE calling Google
                with patch("auth_service.requests.post") as mock_post, patch(
                    "auth_service.requests.get"
                ) as mock_get:
                    # Missing cookie state
                    r1 = client.get("/api/auth/google/callback?code=c1&state=no-cookie", follow_redirects=False)
                    self.assertEqual(r1.status_code, 302)
                    self.assertIn("auth_error=invalid_state", r1.headers["location"])

                    # Start valid login to obtain state cookie, then send tampered state
                    r_login = client.get("/api/auth/google/login", follow_redirects=False)
                    self.assertEqual(r_login.status_code, 302)
                    r2 = client.get("/api/auth/google/callback?code=c1&state=tampered-state", follow_redirects=False)
                    self.assertEqual(r2.status_code, 302)
                    self.assertIn("auth_error=invalid_state", r2.headers["location"])

                    # Expired signed state cookie
                    expired_state = auth_service.sign_payload(
                        {"state": "old-state", "exp": int(time.time()) - 10},
                        cfg.auth_session_secret,
                    )
                    r3 = client.get(
                        "/api/auth/google/callback?code=c1&state=old-state",
                        headers={"Cookie": f"{auth_service.OAUTH_STATE_COOKIE_NAME}={expired_state}"},
                        follow_redirects=False,
                    )
                    self.assertEqual(r3.status_code, 302)
                    self.assertIn("auth_error=invalid_state", r3.headers["location"])

                    mock_post.assert_not_called()
                    mock_get.assert_not_called()

                # 2. Disallowed email or unverified email is rejected without issuing session cookie
                for bad_user in (
                    {"email": "stranger@gmail.com", "email_verified": True, "name": "Stranger"},
                    {"email": "cgikchik07@gmail.com", "email_verified": False, "name": "Unverified"},
                ):
                    r_login = client.get("/api/auth/google/login", follow_redirects=False)
                    state_val = parse_qs(urlparse(r_login.headers["location"]).query)["state"][0]
                    tok_resp = MagicMock()
                    tok_resp.raise_for_status.return_value = None
                    tok_resp.json.return_value = {"access_token": "tok"}
                    usr_resp = MagicMock()
                    usr_resp.raise_for_status.return_value = None
                    usr_resp.json.return_value = bad_user
                    with patch("auth_service.requests.post", return_value=tok_resp), patch(
                        "auth_service.requests.get", return_value=usr_resp
                    ):
                        cb = client.get(f"/api/auth/google/callback?code=c2&state={state_val}", follow_redirects=False)
                        self.assertEqual(cb.status_code, 302)
                        self.assertIn("auth_error=unauthorized_email", cb.headers["location"])
                        self.assertIsNone(client.cookies.get(auth_service.SESSION_COOKIE_NAME))

                # 3. Allowed email (case-insensitive) completes login -> API -> CSRF -> logout revocation
                r_login = client.get("/api/auth/google/login", follow_redirects=False)
                state_val = parse_qs(urlparse(r_login.headers["location"]).query)["state"][0]
                tok_resp = MagicMock()
                tok_resp.raise_for_status.return_value = None
                tok_resp.json.return_value = {"access_token": "tok-ok"}
                usr_resp = MagicMock()
                usr_resp.raise_for_status.return_value = None
                usr_resp.json.return_value = {
                    "email": "PPKSARUENEE@gmail.com",
                    "email_verified": True,
                    "name": "Kru Pinky",
                    "picture": "https://example.com/p.png",
                }
                with patch("auth_service.requests.post", return_value=tok_resp), patch(
                    "auth_service.requests.get", return_value=usr_resp
                ):
                    cb = client.get(f"/api/auth/google/callback?code=c3&state={state_val}", follow_redirects=False)
                    self.assertEqual(cb.status_code, 302)
                    self.assertEqual(cb.headers["location"], "/")

                me_resp = client.get("/api/auth/me")
                self.assertEqual(me_resp.status_code, 200)
                me_json = me_resp.json()
                self.assertTrue(me_json["authenticated"])
                self.assertEqual(me_json["user"]["email"], "ppksaruenee@gmail.com")
                csrf_token = me_json["csrf_token"]
                self.assertTrue(csrf_token)

                self.assertEqual(client.get("/api/system").status_code, 200)

                # CSRF & Origin checks on state mutation
                self.assertEqual(
                    client.post("/api/students", json={"full_name": "เด็กหญิงเอ"}).status_code,
                    403,
                )
                self.assertEqual(
                    client.post(
                        "/api/students",
                        json={"full_name": "เด็กหญิงเอ"},
                        headers={"Origin": origin, "X-CSRF-Token": "wrong"},
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    client.post(
                        "/api/students",
                        json={"full_name": "เด็กหญิงเอ"},
                        headers={"Origin": "https://evil.example", "X-CSRF-Token": csrf_token},
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    client.post(
                        "/api/students",
                        json={"full_name": "เด็กหญิงเอ"},
                        headers={"X-CSRF-Token": csrf_token},
                    ).status_code,
                    403,
                )
                ok_create = client.post(
                    "/api/students",
                    json={"full_name": "เด็กหญิงเอ"},
                    headers={"Origin": origin, "X-CSRF-Token": csrf_token},
                )
                self.assertEqual(ok_create.status_code, 200)

                # Copy stolen session cookie before logout
                stolen_session_cookie = client.cookies.get(auth_service.SESSION_COOKIE_NAME)
                self.assertTrue(stolen_session_cookie)

                logout_resp = client.post(
                    "/api/auth/logout",
                    headers={"Origin": origin, "X-CSRF-Token": csrf_token},
                )
                self.assertEqual(logout_resp.status_code, 200)
                self.assertEqual(client.get("/api/system").status_code, 401)

                # Replaying stolen cookie after server-side logout MUST fail with 401
                with TestClient(test_app, base_url=origin) as attacker_client:
                    replay_resp = attacker_client.get(
                        "/api/system",
                        headers={"Cookie": f"{auth_service.SESSION_COOKIE_NAME}={stolen_session_cookie}"},
                    )
                    self.assertEqual(replay_resp.status_code, 401)

    def test_case_c_localhost_http_oauth_flow_without_secure_cookie(self) -> None:
        import auth_service

        with make_temp_data_dir() as tmpdir:
            cfg = self._make_auth_config(
                tmpdir,
                app_env="development",
                public_base_url="",
            )
            test_app = create_app(config=cfg, engine_factory=lambda: FakeLineOCR())
            local_origin = "http://127.0.0.1:8765"

            with TestClient(test_app, base_url=local_origin) as client:
                r_login = client.get("/api/auth/google/login", follow_redirects=False)
                self.assertEqual(r_login.status_code, 302)
                state_hdr = "; ".join(r_login.headers.get_list("set-cookie"))
                self.assertNotIn("Secure", state_hdr)
                state_val = parse_qs(urlparse(r_login.headers["location"]).query)["state"][0]

                tok_resp = MagicMock()
                tok_resp.raise_for_status.return_value = None
                tok_resp.json.return_value = {"access_token": "tok-local"}
                usr_resp = MagicMock()
                usr_resp.raise_for_status.return_value = None
                usr_resp.json.return_value = {
                    "email": "cgikchik07@gmail.com",
                    "email_verified": True,
                    "name": "Local Owner",
                }
                with patch("auth_service.requests.post", return_value=tok_resp), patch(
                    "auth_service.requests.get", return_value=usr_resp
                ):
                    cb = client.get(f"/api/auth/google/callback?code=c4&state={state_val}", follow_redirects=False)
                    self.assertEqual(cb.status_code, 302)
                self.assertEqual(client.get("/api/system").status_code, 200)


if __name__ == "__main__":
    unittest.main()
