import hashlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.infrastructure.database import EmailDB
from email_agent.web.app import create_app


STRONG_PASSWORD = "SecureAdmin!2026"
NEW_PASSWORD = "SaferAdmin!2027"


class AuthSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = EmailDB(str(Path(self.temp.name) / "auth.db"))
        agent = SimpleNamespace(db=self.db)
        self.app = create_app(agent, object(), object(), True)
        self.client = self.app.test_client()

    def tearDown(self):
        self.db.conn.close()
        self.temp.cleanup()

    def setup_admin(self, client=None, password=STRONG_PASSWORD):
        client = client or self.client
        return client.post(
            "/api/auth/setup",
            json={"username": "admin", "password": password, "confirm_password": password},
        )

    @staticmethod
    def csrf_headers(response):
        return {"X-CSRF-Token": response.get_json()["csrf_token"]}

    def test_first_run_requires_admin_setup_and_rejects_weak_password(self):
        status = self.client.get("/api/auth/status")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.get_json(), {"setup_required": True, "authenticated": False})

        weak = self.setup_admin(password="admin123")
        self.assertEqual(weak.status_code, 400)
        self.assertIn("12", weak.get_json()["error"])

        created = self.setup_admin()
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.get_json()["username"], "admin")
        self.assertNotIn("token", created.get_json())
        cookie = created.headers.get("Set-Cookie", "")
        self.assertIn("email_agent_session=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

        repeated = self.setup_admin()
        self.assertEqual(repeated.status_code, 409)
        self.assertFalse(repeated.get_json()["setup_required"])

    def test_password_hash_is_salted_scrypt_and_plaintext_is_never_stored(self):
        self.assertEqual(self.setup_admin().status_code, 201)
        first_hash = self.db.conn.execute(
            "SELECT password_hash FROM auth_users WHERE username='admin'"
        ).fetchone()["password_hash"]

        other_temp = tempfile.TemporaryDirectory()
        try:
            other_db = EmailDB(str(Path(other_temp.name) / "other.db"))
            other_app = create_app(SimpleNamespace(db=other_db), object(), object(), True)
            other_client = other_app.test_client()
            self.assertEqual(
                other_client.post(
                    "/api/auth/setup",
                    json={
                        "username": "admin",
                        "password": STRONG_PASSWORD,
                        "confirm_password": STRONG_PASSWORD,
                    },
                ).status_code,
                201,
            )
            second_hash = other_db.conn.execute(
                "SELECT password_hash FROM auth_users WHERE username='admin'"
            ).fetchone()["password_hash"]
            self.assertTrue(first_hash.startswith("scrypt$"))
            self.assertNotEqual(first_hash, second_hash)
            self.assertNotIn(STRONG_PASSWORD, first_hash)
            self.assertNotEqual(first_hash, hashlib.sha256(STRONG_PASSWORD.encode()).hexdigest())
        finally:
            other_db.conn.close()
            other_temp.cleanup()

    def test_database_enforces_exactly_one_local_administrator(self):
        self.assertEqual(self.setup_admin().status_code, 201)
        now = datetime.now(timezone.utc).isoformat()

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.conn.execute(
                "INSERT INTO auth_users(username, password_hash, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                ("second-admin", "scrypt$invalid", now, now),
            )

    def test_five_failed_logins_lock_username_and_client(self):
        created = self.setup_admin()
        self.assertEqual(created.status_code, 201)
        self.client.post("/api/auth/logout", headers=self.csrf_headers(created))

        for attempt in range(1, 5):
            response = self.client.post(
                "/api/auth/login", json={"username": "admin", "password": "WrongPassword!9"}
            )
            self.assertEqual(response.status_code, 401, attempt)

        locked = self.client.post(
            "/api/auth/login", json={"username": "admin", "password": "WrongPassword!9"}
        )
        self.assertEqual(locked.status_code, 429)
        self.assertGreater(int(locked.headers["Retry-After"]), 0)

        correct_while_locked = self.client.post(
            "/api/auth/login", json={"username": "admin", "password": STRONG_PASSWORD}
        )
        self.assertEqual(correct_while_locked.status_code, 429)

    def test_login_rejects_oversized_credentials_without_recording_attempt(self):
        created = self.setup_admin()
        self.assertEqual(created.status_code, 201)
        self.client.post("/api/auth/logout", headers=self.csrf_headers(created))

        oversized_username = self.client.post(
            "/api/auth/login", json={"username": "u" * 65, "password": "WrongPassword!9"}
        )
        oversized_password = self.client.post(
            "/api/auth/login", json={"username": "admin", "password": "P" * 257}
        )

        self.assertEqual(oversized_username.status_code, 400)
        self.assertEqual(oversized_password.status_code, 400)
        count = self.db.conn.execute("SELECT COUNT(*) FROM auth_login_attempts").fetchone()[0]
        self.assertEqual(count, 0)

    def test_auth_endpoints_reject_non_string_credentials(self):
        try:
            bad_setup = self.client.post(
                "/api/auth/setup",
                json={"username": 123, "password": STRONG_PASSWORD,
                      "confirm_password": STRONG_PASSWORD},
            )
        except Exception as exc:
            self.fail(f"setup raised instead of returning 400: {exc}")
        self.assertEqual(bad_setup.status_code, 400)

        created = self.setup_admin()
        self.assertEqual(created.status_code, 201)
        try:
            bad_login = self.client.post(
                "/api/auth/login", json={"username": "admin", "password": ["not", "text"]}
            )
        except Exception as exc:
            self.fail(f"login raised instead of returning 400: {exc}")
        self.assertEqual(bad_login.status_code, 400)

    def test_session_survives_new_app_instance_and_database_stores_only_token_hash(self):
        created = self.setup_admin()
        self.assertEqual(created.status_code, 201)
        session_cookie = self.client.get_cookie("email_agent_session")
        self.assertIsNotNone(session_cookie)

        row = self.db.conn.execute(
            "SELECT token_hash, expires_at FROM auth_sessions"
        ).fetchone()
        self.assertNotEqual(row["token_hash"], session_cookie.value)
        self.assertEqual(row["token_hash"], hashlib.sha256(session_cookie.value.encode()).hexdigest())

        second_app = create_app(SimpleNamespace(db=self.db), object(), object(), True)
        second_client = second_app.test_client()
        second_client.set_cookie("email_agent_session", session_cookie.value)
        verified = second_client.get("/api/auth/verify")
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(verified.get_json()["username"], "admin")

        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.db.conn.execute("UPDATE auth_sessions SET expires_at=?", (expired,))
        self.db.conn.commit()
        self.assertEqual(second_client.get("/api/auth/verify").status_code, 401)

    def test_cookie_authenticated_mutations_require_matching_csrf_token(self):
        created = self.setup_admin()
        self.assertEqual(created.status_code, 201)

        missing = self.client.post("/api/auth/logout")
        self.assertEqual(missing.status_code, 403)
        self.assertEqual(self.client.get("/api/auth/verify").status_code, 200)

        wrong = self.client.post(
            "/api/auth/logout", headers={"X-CSRF-Token": "incorrect"}
        )
        self.assertEqual(wrong.status_code, 403)

        valid = self.client.post("/api/auth/logout", headers=self.csrf_headers(created))
        self.assertEqual(valid.status_code, 200)
        self.assertIn("email_agent_session=;", valid.headers.get("Set-Cookie", ""))
        self.assertEqual(self.client.get("/api/auth/verify").status_code, 401)

    def test_status_revokes_incomplete_browser_session_without_csrf_cookie(self):
        created = self.setup_admin()
        self.assertEqual(created.status_code, 201)
        self.client.delete_cookie("email_agent_csrf")

        status = self.client.get("/api/auth/status")

        self.assertEqual(status.status_code, 200)
        self.assertFalse(status.get_json()["authenticated"])
        self.assertEqual(self.client.get("/api/auth/verify").status_code, 401)

    def test_password_change_revokes_every_session_and_requires_new_password(self):
        first_setup = self.setup_admin()
        self.assertEqual(first_setup.status_code, 201)
        first_headers = self.csrf_headers(first_setup)
        second_client = self.app.test_client()
        second_login = second_client.post(
            "/api/auth/login", json={"username": "admin", "password": STRONG_PASSWORD}
        )
        self.assertEqual(second_login.status_code, 200)

        wrong_current = self.client.post(
            "/api/auth/password",
            json={"current_password": "WrongPassword!9", "new_password": NEW_PASSWORD,
                  "confirm_password": NEW_PASSWORD},
            headers=first_headers,
        )
        self.assertEqual(wrong_current.status_code, 401)

        changed = self.client.post(
            "/api/auth/password",
            json={"current_password": STRONG_PASSWORD, "new_password": NEW_PASSWORD,
                  "confirm_password": NEW_PASSWORD},
            headers=first_headers,
        )
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.client.get("/api/auth/verify").status_code, 401)
        self.assertEqual(second_client.get("/api/auth/verify").status_code, 401)

        old_login = self.app.test_client().post(
            "/api/auth/login", json={"username": "admin", "password": STRONG_PASSWORD}
        )
        self.assertEqual(old_login.status_code, 401)
        new_login = self.app.test_client().post(
            "/api/auth/login", json={"username": "admin", "password": NEW_PASSWORD}
        )
        self.assertEqual(new_login.status_code, 200)

    def test_environment_api_token_remains_available_without_cookie(self):
        with mock.patch.dict("os.environ", {"EMAIL_AGENT_API_TOKEN": "machine-secret"}):
            response = self.client.get(
                "/api/auth/verify", headers={"Authorization": "Bearer machine-secret"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["username"], "mcpilot")


if __name__ == "__main__":
    unittest.main()
