"""兼容 CLI 启动器；业务实现位于 src/email_agent。"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from email_agent.application.email_service import EmailAgent  # noqa: E402
from email_agent.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
