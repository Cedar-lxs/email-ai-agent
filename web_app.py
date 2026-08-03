"""兼容 Web 启动器；Flask 应用位于 src/email_agent/web。"""
import importlib
import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
src_path = str(SRC)
if src_path in sys.path:
    sys.path.remove(src_path)
sys.path.insert(0, src_path)

create_app = importlib.import_module("email_agent.web.app").create_app


if __name__ == "__main__":
    application = create_app()
    port = int(os.environ.get("WEB_PORT", "8765"))
    application.run(host="127.0.0.1", port=port, debug=False, threaded=False)
