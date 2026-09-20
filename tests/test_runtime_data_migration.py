import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from email_agent.infrastructure.database import EmailDB
from email_agent.paths import get_project_paths
from email_agent.web.app import create_app


STRONG_PASSWORD = "MigratedAdmin!2026"


class RuntimeDataMigrationTests(unittest.TestCase):
    def test_runtime_root_reuses_legacy_mail_while_adding_authentication(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime_root = Path(directory).resolve()
            with mock.patch.dict(
                os.environ, {"EMAIL_AGENT_RUNTIME_ROOT": str(runtime_root)}
            ):
                paths = get_project_paths()

                self.assertEqual(paths.data, runtime_root / "data")
                self.assertEqual(paths.drafts, runtime_root / "drafts")

                db = EmailDB(str(paths.data / "emails.db"))
                try:
                    db.mark_processed(
                        "legacy-message@example.com",
                        "Legacy support request",
                        "customer@example.com",
                        status="draft_ready",
                        original_body="Existing customer email",
                    )

                    app = create_app(SimpleNamespace(db=db), object(), object(), True)
                    response = app.test_client().post(
                        "/api/auth/setup",
                        json={
                            "username": "admin",
                            "password": STRONG_PASSWORD,
                            "confirm_password": STRONG_PASSWORD,
                        },
                    )

                    self.assertEqual(response.status_code, 201)
                    self.assertEqual(len(db.get_emails()), 1)
                    self.assertEqual(
                        db.get_email("legacy-message@example.com")["original_body"],
                        "Existing customer email",
                    )
                    self.assertEqual(
                        db.conn.execute("SELECT COUNT(*) FROM auth_users").fetchone()[0],
                        1,
                    )
                finally:
                    db.conn.close()


if __name__ == "__main__":
    unittest.main()
