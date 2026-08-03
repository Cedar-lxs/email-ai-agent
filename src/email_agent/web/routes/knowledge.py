"""知识库管理路由。"""
from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

bp = Blueprint("knowledge", __name__, url_prefix="/knowledge")


def services():
    return current_app.extensions["services"]


@bp.get("")
@bp.get("/")
def index():
    service = services().knowledge
    files = [{"name": path.name, "suffix": path.suffix.upper().lstrip("."),
              "size": f"{path.stat().st_size / 1024:.1f} KB"}
             for path in service.list_files()]
    try:
        stats = service.retriever.get_stats()
    except Exception as exc:
        stats = {"entries": 0, "mode": "degraded", "vector": {},
                 "errors": [f"索引状态读取失败：{exc}"]}
    return render_template("knowledge.html", page="knowledge", status="", files=files,
                           index_stats=stats)


@bp.post("/upload")
def upload():
    try:
        services().csrf.verify()
        count = services().knowledge.upload(request.files.getlist("files"))
        stats = services().knowledge.retriever.get_stats()
        vector = stats.get("vector", {})
        message = f"成功上传 {count} 个知识文件，当前共 {stats.get('entries', 0)} 个知识片段"
        if vector:
            message += f"、{vector.get('vectors', 0)} 条向量"
        flash(message)
    except Exception as exc:
        flash(str(exc), "error")
    return redirect(url_for("knowledge.index"))


@bp.post("/delete")
def delete():
    try:
        services().csrf.verify()
        count = services().knowledge.delete(request.form.getlist("names"))
        flash(f"已删除 {count} 个知识文件")
    except Exception as exc:
        flash(str(exc), "error")
    return redirect(url_for("knowledge.index"))
