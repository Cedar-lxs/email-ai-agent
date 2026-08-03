"""知识文件管理应用服务。"""
import base64
import json
import os
import re
import tempfile
from pathlib import Path

from email_agent.infrastructure.knowledge.loaders import KnowledgeBase


class KnowledgeService:
    DEFAULT_MAX_FILE_BYTES = 20 * 1024 * 1024
    ARTICLE_MARKER = "<!-- email-ai-agent:structured-knowledge:v1 "
    ARTICLE_CATEGORIES = {
        "技术参数咨询", "安装配置", "使用指导", "故障排查", "网络连接", "软件固件", "兼容性问题"
    }
    ARTICLE_REQUIRED = {
        "question_id": "问题编号", "title": "问题标题", "category": "问题分类",
        "product_type": "适用产品类型", "standard_question": "中文标准问题",
        "keywords": "检索关键词", "risk": "操作风险", "english_reply": "标准英文回复参考",
    }
    ARTICLE_LIST_FIELDS = (
        "applicable_models", "excluded_models", "english_expressions", "chinese_expressions",
        "conditions", "causes", "general_steps", "required_information",
        "escalation_conditions", "reply_restrictions",
    )

    def __init__(self, knowledge_dir: Path, retriever,
                 max_file_bytes: int = DEFAULT_MAX_FILE_BYTES):
        self.knowledge_dir = Path(knowledge_dir)
        self.retriever = retriever
        self.max_file_bytes = int(max_file_bytes)

    def list_files(self):
        return sorted(
            [path for path in self.knowledge_dir.iterdir()
             if path.is_file() and path.suffix.lower() in KnowledgeBase.SUPPORTED_SUFFIXES],
            key=lambda path: path.name.lower(),
        )

    @staticmethod
    def safe_name(filename: str) -> str:
        raw = Path(filename or "").name.strip()
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw).rstrip(". ")

    def upload(self, uploads) -> int:
        uploads = [upload for upload in uploads
                   if self.safe_name(getattr(upload, "filename", ""))]
        if not uploads:
            raise ValueError("请选择至少一个知识文件")

        prepared, batch_names = [], set()
        for upload in uploads:
            name = self.safe_name(upload.filename)
            if Path(name).suffix.lower() not in KnowledgeBase.SUPPORTED_SUFFIXES:
                raise ValueError(f"不支持的知识文件：{upload.filename}")
            normalized = name.casefold()
            if normalized in batch_names:
                raise ValueError(f"本次选择中存在重名文件：{name}")
            batch_names.add(normalized)
            target = self.knowledge_dir / name
            if target.exists():
                raise ValueError(f"文件已存在：{name}")
            prepared.append((upload, target))

        saved = []
        try:
            for upload, target in prepared:
                upload.save(target)
                saved.append(target)
                size = target.stat().st_size
                if size <= 0:
                    raise ValueError(f"文件内容为空：{target.name}")
                if size > self.max_file_bytes:
                    raise ValueError(f"单个文件不能超过 20MB：{target.name}")
            stats = self.retriever.rebuild()
            if stats.errors:
                raise ValueError("文档解析失败：" + "；".join(stats.errors))
            indexed_sources = {entry["source"] for entry in self.retriever.entries}
            missing_sources = [target.name for _, target in prepared
                               if target.name not in indexed_sources]
            if missing_sources:
                raise ValueError("以下文件没有可索引的有效内容：" + "、".join(missing_sources))
            return len(saved)
        except Exception:
            for path in saved:
                path.unlink(missing_ok=True)
            self.retriever.rebuild()
            raise

    def is_editable_article(self, path: Path) -> bool:
        if Path(path).suffix.lower() != ".md":
            return False
        try:
            first_line = Path(path).read_text(encoding="utf-8-sig").splitlines()[0]
            return first_line.startswith(self.ARTICLE_MARKER) and first_line.endswith(" -->")
        except (OSError, IndexError, UnicodeError):
            return False

    @staticmethod
    def _clean_text(value, limit=10000) -> str:
        text = str(value or "").replace("\x00", "").strip()
        if len(text) > limit:
            raise ValueError(f"单个字段不能超过 {limit} 个字符")
        return text

    def normalize_article(self, data) -> dict:
        if not isinstance(data, dict):
            raise ValueError("知识内容必须是对象")
        article = {key: self._clean_text(data.get(key)) for key in self.ARTICLE_REQUIRED}
        missing = [label for key, label in self.ARTICLE_REQUIRED.items() if not article[key]]
        if missing:
            raise ValueError("请填写必填项：" + "、".join(missing))
        if article["category"] not in self.ARTICLE_CATEGORIES:
            raise ValueError("问题分类不在允许范围内")
        article["question_id"] = re.sub(r"\s+", "-", article["question_id"])
        if not re.fullmatch(r"[A-Za-z0-9._-]{2,40}", article["question_id"]):
            raise ValueError("问题编号只能包含字母、数字、点、下划线和连字符，长度为 2–40")
        for field in self.ARTICLE_LIST_FIELDS:
            value = data.get(field, [])
            if value is None:
                value = []
            if not isinstance(value, list):
                raise ValueError(f"字段 {field} 必须是列表")
            article[field] = [text for item in value if (text := self._clean_text(item, 2000))]
            if len(article[field]) > 50:
                raise ValueError(f"字段 {field} 最多填写 50 项")
        variants = data.get("model_solutions", []) or []
        if not isinstance(variants, list) or len(variants) > 20:
            raise ValueError("分型号解决方案格式无效或数量过多")
        article["model_solutions"] = []
        for item in variants:
            if not isinstance(item, dict):
                raise ValueError("分型号解决方案必须是对象列表")
            model = self._clean_text(item.get("model"), 500)
            steps = item.get("steps", []) or []
            if not isinstance(steps, list):
                raise ValueError("分型号解决步骤必须是列表")
            steps = [text for step in steps if (text := self._clean_text(step, 2000))]
            if model and steps:
                article["model_solutions"].append({"model": model, "steps": steps[:50]})
        source = data.get("source", {}) or {}
        if not isinstance(source, dict):
            raise ValueError("知识来源格式无效")
        article["source"] = {key: self._clean_text(source.get(key), 500)
                             for key in ("name", "version", "maintainer")}
        return article

    def article_filename(self, article: dict) -> str:
        title = re.sub(r"\s+", "_", article["title"])
        title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip("._ ")
        title = title[:80].rstrip("._ ") or "知识条目"
        return self.safe_name(f'{article["question_id"]}_{title}.md')

    @staticmethod
    def _section(lines: list[str], title: str, content: str):
        if content:
            lines.extend([f"## {title}", "", content, ""])

    @staticmethod
    def _list(items: list[str], ordered=False) -> str:
        return "\n".join(f'{index + 1}. {item}' if ordered else f'- {item}'
                         for index, item in enumerate(items))

    def render_article(self, data) -> tuple[dict, str, str]:
        article = self.normalize_article(data)
        metadata = base64.urlsafe_b64encode(
            json.dumps(article, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        lines = [f"{self.ARTICLE_MARKER}{metadata} -->", "",
                 f'# {article["question_id"]} {article["title"]}', ""]
        scalar_sections = (
            ("问题编号", article["question_id"]), ("问题标题", article["title"]),
            ("问题分类", article["category"]), ("适用产品类型", article["product_type"]),
        )
        for title, content in scalar_sections:
            self._section(lines, title, content)
        list_sections = (
            ("适用型号", "applicable_models", False), ("不适用型号", "excluded_models", False),
            ("客户常见英文表达", "english_expressions", False),
            ("客户常见中文表达", "chinese_expressions", False),
        )
        for title, key, ordered in list_sections:
            self._section(lines, title, self._list(article[key], ordered))
        self._section(lines, "中文标准问题", article["standard_question"])
        self._section(lines, "检索关键词", article["keywords"])
        for title, key in (("故障判断条件", "conditions"), ("已确认的可能原因", "causes")):
            self._section(lines, title, self._list(article[key]))
        self._section(lines, "通用解决方案", self._list(article["general_steps"], True))
        if article["model_solutions"]:
            lines.extend(["## 分型号解决方案", ""])
            for solution in article["model_solutions"]:
                lines.extend([f'### {solution["model"]}', "", self._list(solution["steps"], True), ""])
        self._section(lines, "操作风险", article["risk"])
        for title, key in (("需要客户补充的信息", "required_information"),
                           ("转人工条件", "escalation_conditions"),
                           ("禁止回复或限制条件", "reply_restrictions")):
            self._section(lines, title, self._list(article[key]))
        reply = re.sub(r"\n\s*Technical Support\s*$", "", article["english_reply"], flags=re.I).strip()
        self._section(lines, "标准英文回复参考", f"{reply}\n\nTechnical Support")
        source_lines = []
        for label, key in (("来源名称", "name"), ("版本或日期", "version"), ("维护人", "maintainer")):
            if article["source"][key]:
                source_lines.append(f'- {label}：{article["source"][key]}')
        self._section(lines, "知识来源", "\n".join(source_lines))
        markdown = "\n".join(lines).strip() + "\n"
        if len(markdown.encode("utf-8")) > self.max_file_bytes:
            raise ValueError("生成的知识文档不能超过 20MB")
        return article, self.article_filename(article), markdown

    def read_article(self, filename: str) -> dict:
        path = self._article_path(filename)
        if not path.is_file():
            raise FileNotFoundError("知识文件不存在")
        if not self.is_editable_article(path):
            raise ValueError("该文件不是由知识表单生成，不能在表单中编辑")
        marker = path.read_text(encoding="utf-8-sig").splitlines()[0]
        encoded = marker[len(self.ARTICLE_MARKER):-4]
        try:
            data = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8"))
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("知识表单元数据损坏") from exc
        return {"filename": path.name, "article": self.normalize_article(data)}

    def _article_path(self, filename: str) -> Path:
        name = self.safe_name(filename)
        if not name or name != filename or Path(name).suffix.lower() != ".md":
            raise ValueError("知识文件名无效")
        path = self.knowledge_dir / name
        if path.parent.resolve() != self.knowledge_dir.resolve():
            raise ValueError("知识文件路径无效")
        return path

    def save_article(self, data, original_filename: str = None) -> dict:
        article, filename, markdown = self.render_article(data)
        target = self._article_path(filename)
        original = self._article_path(original_filename) if original_filename else None
        if original:
            if not original.is_file():
                raise FileNotFoundError("知识文件不存在")
            if not self.is_editable_article(original):
                raise ValueError("该文件不是由知识表单生成，不能覆盖")
            if target != original and target.exists():
                raise ValueError(f"文件已存在：{target.name}")
        elif target.exists():
            raise ValueError(f"文件已存在：{target.name}")

        old_content = original.read_bytes() if original else None
        temporary = None
        try:
            self.knowledge_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n",
                                             dir=self.knowledge_dir, suffix=".tmp", delete=False) as handle:
                handle.write(markdown)
                temporary = Path(handle.name)
            os.replace(temporary, target)
            if original and original != target:
                original.unlink()
            result = self.retriever.rebuild()
            if result.errors:
                raise ValueError("文档解析失败：" + "；".join(result.errors))
            if target.name not in {entry["source"] for entry in self.retriever.entries}:
                raise ValueError("生成的文档没有可索引的有效内容")
            return {"filename": target.name, "article": article, "markdown": markdown,
                    "index": self.retriever.get_stats()}
        except Exception:
            if temporary:
                temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            if original and old_content is not None:
                original.write_bytes(old_content)
            self.retriever.rebuild()
            raise

    def delete(self, names: list[str]) -> int:
        deleted = 0
        root = self.knowledge_dir.resolve()
        for name in names:
            path = self.knowledge_dir / Path(name).name
            if (path.is_file() and path.parent.resolve() == root
                    and path.suffix.lower() in KnowledgeBase.SUPPORTED_SUFFIXES):
                path.unlink()
                deleted += 1
        self.retriever.rebuild()
        return deleted
