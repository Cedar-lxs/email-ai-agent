"""Flask 应用工厂。"""
import hmac
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from flask import Flask, jsonify, request, session, send_from_directory
from flask_cors import CORS

from email_agent.bootstrap import create_services
from email_agent.web.routes import knowledge, mail, settings, api

LABELS = {"draft_ready": "待审核", "escalated": "转人工", "replied": "已发送",
          "rejected": "已拒绝", "failed": "失败", "pending": "处理中",
          "skipped_self": "已跳过"}


class CsrfGuard:
    @staticmethod
    def token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    @staticmethod
    def verify():
        if not hmac.compare_digest(session.get("csrf", ""), request.form.get("csrf", "")):
            raise ValueError("页面安全令牌已失效，请刷新后重试")


@dataclass
class WebServices:
    agent: object
    review: object
    knowledge: object
    csrf: CsrfGuard


def create_app(agent=None, review=None, knowledge_service=None, testing=False):
    app = Flask(__name__)
    app.secret_key = os.environ.get("WEB_SECRET_KEY") or secrets.token_hex(32)
    app.config.update(TESTING=testing, MAX_CONTENT_LENGTH=100 * 1024 * 1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                      STATUS_LABELS=LABELS)
    
    # 启用 CORS（开发环境）
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    
    if agent is None:
        agent, review, knowledge_service = create_services()
    app.extensions["services"] = WebServices(agent, review, knowledge_service, CsrfGuard())
    
    # 注册 API 路由（REST API）
    app.register_blueprint(api.bp)
    
    # 注册传统路由（向后兼容）
    app.register_blueprint(mail.bp)
    app.register_blueprint(knowledge.bp)
    app.register_blueprint(settings.bp)
    
    # Vue3 前端静态文件服务
    dist_dir = Path(__file__).parent / "dist"
    
    @app.route("/api", defaults={"path": ""})
    @app.route("/api/<path:path>")
    def missing_api(path):
        return jsonify({"error": "API 接口不存在，请重启后端并刷新页面"}), 404

    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    def serve_spa(path):
        """服务 Vue3 SPA；未知 API 必须返回 JSON 404，不能回退到 HTML。"""
        if path == "api" or path.startswith("api/"):
            return jsonify({"error": "API 接口不存在，请重启后端并刷新页面"}), 404
        if path and (dist_dir / path).is_file():
            return send_from_directory(dist_dir, path)
        # SPA 路由回退到 index.html
        if (dist_dir / "index.html").exists():
            return send_from_directory(dist_dir, "index.html")
        # 如果前端未构建，返回提示
        return """
        <html>
        <body style="font-family: sans-serif; padding: 40px; text-align: center;">
            <h1>Email AI Agent</h1>
            <p>前端应用未构建，请运行：</p>
            <code>cd frontend && npm install && npm run build</code>
            <p style="margin-top: 20px;">或访问传统界面：<a href="/mail">/mail</a></p>
        </body>
        </html>
        """, 404

    @app.context_processor
    def common_context():
        services = app.extensions["services"]
        counts = services.agent.db.get_status_counts()
        mode = services.agent.db.get_setting(
            "workflow_mode", services.agent.config["workflow"]["mode"])
        return {"counts": counts, "total": sum(counts.values()), "labels": LABELS,
                "csrf": CsrfGuard.token(), "mode": mode,
                "knowledge_count": len(services.knowledge.list_files())}

    app.email_agent = agent
    return app
