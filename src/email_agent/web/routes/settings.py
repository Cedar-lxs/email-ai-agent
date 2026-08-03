"""运行设置路由。"""
from flask import Blueprint, current_app, flash, redirect, request, url_for

bp = Blueprint("settings", __name__)


@bp.post("/mode")
def set_mode():
    services = current_app.extensions["services"]
    try:
        services.csrf.verify()
        mode = request.form.get("mode", "")
        if mode not in {"semi_auto", "full_auto"}:
            raise ValueError("无效的发送模式")
        services.agent.db.set_setting("workflow_mode", mode)
        services.agent.mode = mode
        flash("发送模式已更新")
    except Exception as exc:
        flash(str(exc), "error")
    return redirect(request.referrer or url_for("mail.index"))
