"""Flask 应用工厂。"""
from dataclasses import dataclass
from pathlib import Path

from flask import Flask, jsonify, redirect, send_from_directory
from flask_cors import CORS

from email_agent.bootstrap import create_services
from email_agent.web.routes import api


@dataclass
class WebServices:
    agent: object
    review: object
    knowledge: object


def create_app(agent=None, review=None, knowledge_service=None, testing=False):
    app = Flask(__name__)
    app.config.update(TESTING=testing, MAX_CONTENT_LENGTH=100 * 1024 * 1024)
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    if agent is None:
        agent, review, knowledge_service = create_services()
    app.extensions["services"] = WebServices(agent, review, knowledge_service)
    app.register_blueprint(api.bp)

    dist_dir = Path(__file__).parent / "dist"

    @app.get("/mail")
    @app.get("/mail/<path:message_id>")
    def redirect_legacy_mail(message_id=None):
        target = "/mails" if message_id is None else f"/mails/{message_id}"
        return redirect(target, code=308)

    @app.route("/api", defaults={"path": ""})
    @app.route("/api/<path:path>")
    def missing_api(path):
        return jsonify({"error": "API 接口不存在，请重启后端并刷新页面"}), 404

    @app.get("/")
    @app.get("/<path:path>")
    def serve_spa(path=""):
        """服务 Vue SPA，API 请求永不回退为 HTML。"""
        if path and (dist_dir / path).is_file():
            return send_from_directory(dist_dir, path)
        if (dist_dir / "index.html").is_file():
            return send_from_directory(dist_dir, "index.html")
        return jsonify({"error": "前端应用未构建，请运行 npm run build"}), 503

    app.email_agent = agent
    return app
