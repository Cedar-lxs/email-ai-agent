"""邮件审核路由。"""
from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

bp = Blueprint("mail", __name__)


def services():
    return current_app.extensions["services"]


@bp.get("/mail")
def index():
    agent = services().agent
    status = request.args.get("status", "draft_ready")
    if status not in {*current_app.config["STATUS_LABELS"], "all"}:
        status = "draft_ready"
    query = request.args.get("q", "").strip()[:200]
    return render_template("mail_list.html", page="mail", status=status, query=query,
                           rows=agent.db.get_emails(status, query))


@bp.get("/mail/<path:message_id>")
def detail(message_id):
    agent, review = services().agent, services().review
    item = agent.db.get_email(message_id)
    if not item:
        return "未找到邮件", 404
    return render_template(
        "mail_detail.html", page="mail", status=item["status"], item=item,
        draft_body=review.draft_body(item), retrieval=agent.db.parse_retrieval_trace(item),
        reply_subject=agent.sender.build_reply_subject(item["subject"]),
    )


@bp.post("/mail/<path:message_id>/save")
def save(message_id):
    try:
        services().csrf.verify()
        services().review.edit(message_id, request.form.get("body", ""))
        flash("草稿已保存")
    except Exception as exc:
        flash(str(exc), "error")
    return redirect(url_for("mail.detail", message_id=message_id))


@bp.post("/mail/<path:message_id>/approve")
def approve(message_id):
    try:
        services().csrf.verify()
        services().review.approve(message_id)
        flash("邮件已成功发送")
    except Exception as exc:
        flash(str(exc), "error")
    return redirect(url_for("mail.detail", message_id=message_id))


@bp.post("/mail/<path:message_id>/reject")
def reject(message_id):
    try:
        services().csrf.verify()
        reason = request.form.get("reason", "").strip()[:500]
        if not reason:
            raise ValueError("请填写拒绝原因")
        services().review.reject(message_id, reason)
        flash("草稿已拒绝，不会发送")
    except Exception as exc:
        flash(str(exc), "error")
    return redirect(url_for("mail.detail", message_id=message_id))


@bp.post("/mail/delete")
def delete():
    try:
        services().csrf.verify()
        deleted = services().review.delete(request.form.getlist("message_ids"))
        flash(f"已删除 {len(deleted)} 封无效邮件及对应草稿")
    except Exception as exc:
        flash(str(exc), "error")
    return redirect(url_for("mail.index", status="all"))
