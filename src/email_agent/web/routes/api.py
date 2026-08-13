"""API 路由：认证和邮件管理。"""
from pathlib import Path

from flask import Blueprint, current_app, request, jsonify
from email_agent.web.auth import AuthManager, token_required, get_current_user

bp = Blueprint("api", __name__, url_prefix="/api")


# ============================================================
# 认证相关 API
# ============================================================

@bp.post("/auth/login")
def login():
    """用户登录"""
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"error": "用户名和密码不能为空"}), 400

    if not AuthManager.verify_password(username, password):
        return jsonify({"error": "用户名或密码错误"}), 401

    token = AuthManager.generate_token(username)
    return jsonify({
        "token": token,
        "username": username,
        "message": "登录成功"
    })


@bp.get("/auth/verify")
@token_required
def verify_token():
    """验证 token"""
    return jsonify({
        "username": get_current_user(),
        "message": "认证有效"
    })


@bp.post("/auth/logout")
@token_required
def logout():
    """登出"""
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token = auth_header[7:]
        AuthManager.revoke_token(token)
    return jsonify({"message": "已登出"})


# ============================================================
# 邮件相关 API
# ============================================================

@bp.get("/mails")
@token_required
def get_mails():
    """获取邮件列表"""
    from flask import current_app
    agent = current_app.extensions["services"].agent

    status = request.args.get("status", "draft_ready")
    query = request.args.get("q", "").strip()
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
    except (TypeError, ValueError):
        return jsonify({"error": "分页参数必须是整数"}), 400

    if status not in {"draft_ready", "replied", "escalated", "rejected", "failed", "pending", "skipped_self", "all"}:
        status = "draft_ready"

    all_mails = agent.db.get_emails(status, query)
    total = len(all_mails)

    # 分页
    start = (page - 1) * page_size
    end = start + page_size
    mails = [dict(row) for row in all_mails[start:end]]

    return jsonify({
        "mails": mails,
        "total": total,
        "page": page,
        "page_size": page_size
    })


@bp.get("/mails/<path:message_id>")
@token_required
def get_mail_detail(message_id):
    """获取邮件详情"""
    from flask import current_app
    agent = current_app.extensions["services"].agent
    review = current_app.extensions["services"].review

    mail = agent.db.get_email(message_id)
    if not mail:
        return jsonify({"error": "邮件不存在"}), 404

    draft_body = review.draft_body(mail)
    retrieval = agent.db.parse_retrieval_trace(mail)
    reply_subject = agent.sender.build_reply_subject(mail["subject"])

    return jsonify({
        "mail": dict(mail),
        "draft_body": draft_body,
        "retrieval": retrieval,
        "reply_subject": reply_subject
    })


@bp.post("/mails/<path:message_id>/save")
@token_required
def save_draft(message_id):
    """保存草稿"""
    from flask import current_app
    review = current_app.extensions["services"].review

    data = request.get_json(silent=True) or {}
    body = data.get("body", "")

    if not body.strip():
        return jsonify({"error": "回复内容不能为空"}), 400

    try:
        review.edit(message_id, body)
        return jsonify({"message": "草稿已保存"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.post("/mails/<path:message_id>/approve")
@token_required
def approve_mail(message_id):
    """批准并发送"""
    from flask import current_app
    review = current_app.extensions["services"].review

    try:
        review.approve(message_id)
        return jsonify({"message": "邮件已发送"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.post("/mails/<path:message_id>/reject")
@token_required
def reject_mail(message_id):
    """拒绝草稿"""
    from flask import current_app
    review = current_app.extensions["services"].review

    data = request.get_json(silent=True) or {}
    reason = data.get("reason", "").strip()

    if not reason:
        return jsonify({"error": "请填写拒绝原因"}), 400

    try:
        review.reject(message_id, reason)
        return jsonify({"message": "草稿已拒绝"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.post("/mails/delete")
@token_required
def delete_mails():
    """删除邮件"""
    from flask import current_app
    review = current_app.extensions["services"].review

    data = request.get_json(silent=True) or {}
    message_ids = data.get("message_ids", [])

    if not message_ids:
        return jsonify({"error": "未选择邮件"}), 400

    try:
        deleted = review.delete(message_ids)
        return jsonify({
            "message": f"已删除 {len(deleted)} 封邮件",
            "deleted": deleted
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.get("/knowledge")
@token_required
def get_knowledge():
    """获取知识文件和索引状态。"""
    service = current_app.extensions["services"].knowledge
    files = [
    {
        "name": service.relative_name(path),
        "filename": path.name,
        "directory": path.relative_to(service.knowledge_dir).parent.as_posix() or "root",
        "suffix": path.suffix.upper().lstrip("."),
        "size": path.stat().st_size,
        "editable": service.is_editable_article(path),
    }
    for path in service.list_files()
   ]
    try:
        stats = service.retriever.get_stats()
    except Exception as exc:
        stats = {"entries": 0, "sources": len(files), "mode": "degraded",
                 "vector": {}, "errors": [f"索引状态读取失败：{exc}"]}
    return jsonify({"files": files, "index": stats})


@bp.post("/knowledge/upload")
@token_required
def upload_knowledge():
    """上传知识文件并重建索引。"""
    service = current_app.extensions["services"].knowledge
    try:
        count = service.upload(request.files.getlist("files"))
        return jsonify({"message": f"成功上传 {count} 个知识文件", "count": count,
                        "index": service.retriever.get_stats()})
    except (ValueError, OSError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"知识文件上传失败：{exc}"}), 500


@bp.post("/knowledge/articles/preview")
@token_required
def preview_knowledge_article():
    """校验结构化知识并生成规范 Markdown。"""
    service = current_app.extensions["services"].knowledge
    try:
        article, filename, markdown = service.render_article(request.get_json(silent=True) or {})
        return jsonify({"filename": filename, "article": article, "markdown": markdown})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.post("/knowledge/articles")
@token_required
def create_knowledge_article():
    """创建结构化知识文件并重建索引。"""
    service = current_app.extensions["services"].knowledge
    try:
        result = service.save_article(request.get_json(silent=True) or {})
        return jsonify({"message": "知识条目已保存", **result}), 201
    except (ValueError, FileNotFoundError, OSError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"知识条目保存失败：{exc}"}), 500


@bp.get("/knowledge/articles/<path:filename>")
@token_required
def get_knowledge_article(filename):
    """读取由知识表单生成的文档。"""
    service = current_app.extensions["services"].knowledge
    try:
        return jsonify(service.read_article(filename))
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except (ValueError, OSError) as exc:
        return jsonify({"error": str(exc)}), 400


@bp.put("/knowledge/articles/<path:filename>")
@token_required
def update_knowledge_article(filename):
    """更新结构化知识文件并重建索引。"""
    service = current_app.extensions["services"].knowledge
    try:
        result = service.save_article(request.get_json(silent=True) or {}, filename)
        return jsonify({"message": "知识条目已更新", **result})
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except (ValueError, OSError) as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"知识条目更新失败：{exc}"}), 500


@bp.post("/knowledge/delete")
@token_required
def delete_knowledge():
    """删除知识文件并重建索引。"""
    service = current_app.extensions["services"].knowledge
    names = (request.get_json(silent=True) or {}).get("names", [])
    if not isinstance(names, list) or not names:
        return jsonify({"error": "请选择至少一个知识文件"}), 400
    try:
        count = service.delete(names)
        return jsonify({"message": f"已删除 {count} 个知识文件", "count": count,
                        "index": service.retriever.get_stats()})
    except Exception as exc:
        return jsonify({"error": f"知识文件删除失败：{exc}"}), 500


@bp.post("/knowledge/rebuild")
@token_required
def rebuild_knowledge():
    """手动重建知识索引。"""
    service = current_app.extensions["services"].knowledge
    try:
        result = service.retriever.rebuild()
        stats = service.retriever.get_stats()
        if result.errors:
            return jsonify({"error": "索引重建存在错误：" + "；".join(result.errors),
                            "index": stats}), 400
        return jsonify({"message": "知识索引重建完成", "index": stats})
    except Exception as exc:
        return jsonify({"error": f"索引重建失败：{exc}"}), 500


def _public_settings(agent):
    config = agent.config
    mail, ai = config.get("mail", {}), config.get("ai", {})
    rag, workflow = config.get("rag", {}), config.get("workflow", {})
    return {
        "mode": agent.db.get_setting("workflow_mode", workflow.get("mode", "semi_auto")),
        "mail": {key: mail.get(key) for key in (
            "account", "imap_server", "imap_port", "smtp_server", "smtp_port", "poll_interval")},
        "ai": {key: ai.get(key) for key in ("provider", "api_base", "model", "company")},
        "rag": {"mode": rag.get("mode", "lexical"), "top_k": rag.get("top_k"),
                "min_confidence": rag.get("min_confidence"),
                "embedding_model": rag.get("embedding", {}).get("model", "")},
        "auto_reply_types": workflow.get("auto_reply_types", []),
        "web_search": {
            "enabled": bool(config.get("web_search", {}).get("enabled", False)),
            "provider": config.get("web_search", {}).get("provider", ""),
            "api_key_env": config.get("web_search", {}).get("api_key_env", "BOCHA_API_KEY"),
            "auto_send_low_risk": bool(config.get("web_search", {}).get("auto_send_low_risk", False)),
            "allowed_intents": config.get("web_search", {}).get("allowed_intents", []),
        },
        "config_file": Path("config.yaml").as_posix(),
    }


@bp.get("/settings")
@token_required
def get_settings():
    """返回不含密码和 API Key 的运行配置。"""
    return jsonify(_public_settings(current_app.extensions["services"].agent))


@bp.put("/settings/mode")
@token_required
def update_mode():
    """即时更新运行模式并持久化到数据库设置。"""
    agent = current_app.extensions["services"].agent
    mode = (request.get_json(silent=True) or {}).get("mode", "")
    if mode not in {"semi_auto", "full_auto"}:
        return jsonify({"error": "无效的发送模式"}), 400
    agent.db.set_setting("workflow_mode", mode)
    agent.mode = mode
    return jsonify({"message": "发送模式已更新", "mode": mode})


@bp.post("/settings/test-mail")
@token_required
def test_mail_connection():
    """登录 IMAP 后立即断开，不读取或发送邮件。"""
    fetcher = current_app.extensions["services"].agent._fetcher()
    try:
        fetcher.connect()
        return jsonify({"message": "IMAP 邮箱连接成功"})
    except Exception as exc:
        return jsonify({"error": f"IMAP 邮箱连接失败：{exc}"}), 502
    finally:
        fetcher.disconnect()


@bp.post("/settings/test-ai")
@token_required
def test_ai_connection():
    """发送最小请求验证当前 AI 服务配置。"""
    agent = current_app.extensions["services"].agent
    try:
        response = agent.ai._call_llm("Reply with exactly: OK", max_tokens=8).strip()
        if not response:
            raise ValueError("AI 服务返回空响应")
        return jsonify({"message": "AI 服务连接成功", "response": response[:80]})
    except Exception as exc:
        return jsonify({"error": f"AI 服务连接失败：{exc}"}), 502


@bp.get("/mails/stats")
@token_required
def get_stats():
    """获取统计信息"""
    from flask import current_app
    agent = current_app.extensions["services"].agent

    counts = agent.db.get_status_counts()
    mode = agent.db.get_setting("workflow_mode", agent.config["workflow"]["mode"])

    return jsonify({
        "counts": counts,
        "mode": mode
    })

@bp.get("/customers/<path:sender_email>/history")
@token_required
def get_customer_history(sender_email):
    """Return a customer ticket history summary."""
    try:
        limit = min(100, max(1, int(request.args.get("limit", 20))))
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be an integer"}), 400
    sender_email = sender_email.strip()
    if not sender_email:
        return jsonify({"error": "Customer email is required"}), 400
    rows = current_app.extensions["services"].agent.db.get_customer_history(
        sender_email, limit
    )
    return jsonify({
        "customer": sender_email,
        "tickets": [dict(row) for row in rows],
        "total": len(rows),
    })


@bp.get("/tickets/stats/today")
@token_required
def get_today_ticket_stats():
    """Return local-date ticket status and intent counts."""
    return jsonify(current_app.extensions["services"].agent.db.get_today_ticket_stats())
