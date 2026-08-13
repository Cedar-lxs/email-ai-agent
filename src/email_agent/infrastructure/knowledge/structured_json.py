"""将产品与术语源 JSON 标准化为可向量化的知识条目。"""
import argparse
import json
from pathlib import Path
from typing import Any


def _value(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value).strip()


def _fields(record: dict[str, Any], ignored: set[str]) -> list[str]:
    return [f"{key}: {_value(value)}" for key, value in record.items()
            if key not in ignored and _value(value)]


def glossary_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    entries = []
    for category, records in data.items():
        if category.startswith("_") or not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict) or not _value(record.get("en")):
                continue
            en, zh = _value(record["en"]), _value(record.get("zh"))
            fields = _fields(record, {"en", "zh"})
            answer = "\n".join(filter(None, [
                f"英文术语: {en}", f"中文术语: {zh}", *fields,
            ]))
            entries.append({
                "question": f"术语对照：{en} / {zh}",
                "answer": answer,
                "section": f"术语 > {category} > {en}",
                "keywords": [category, en, zh, *[_value(record.get(key)) for key in ("full", "context")]],
                "metadata": {"kind": "glossary", "category": category, "term_en": en, "term_zh": zh},
            })
    return entries


def _product_entry(record: dict[str, Any], category: str, family: str, product_type: str) -> dict[str, Any] | None:
    model = _value(record.get("model"))
    if not model:
        return None
    fields = _fields(record, {"model", "alias"})
    alias = _value(record.get("alias"))
    answer = "\n".join(filter(None, [
        f"产品型号: {model}", f"产品类型: {product_type}", f"分类: {category}",
        f"产品线: {family}" if family else "", f"别名: {alias}" if alias else "", *fields,
    ]))
    return {
        "question": f"产品参数：{model}" + (f" / {alias}" if alias else ""),
        "answer": answer,
        "section": f"产品 > {product_type} > {category}" + (f" > {family}" if family else "") + f" > {model}",
        "keywords": [model, alias, product_type, category, family, *[_value(value) for value in record.values()]],
        "metadata": {"kind": "product", "product_type": product_type, "category": category, "family": family, "model": model},
    }


def product_entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    entries = []
    for product_type, groups in data.items():
        if product_type.startswith("_"):
            continue
        if isinstance(groups, list):
            groups = {product_type: groups}
        if not isinstance(groups, dict):
            continue
        for category, records in groups.items():
            if isinstance(records, dict):
                for family, items in records.items():
                    for record in items if isinstance(items, list) else []:
                        entry = _product_entry(record, category, family, product_type)
                        if entry:
                            entries.append(entry)
            elif isinstance(records, list):
                for record in records:
                    entry = _product_entry(record, category, "", product_type)
                    if entry:
                        entries.append(entry)
    return entries


def convert(source: Path, destination: Path, transformer) -> int:
    data = json.loads(source.read_text(encoding="utf-8-sig"))
    entries = transformer(data)
    destination.write_text(
        json.dumps({"schema_version": 1, "entries": entries}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return len(entries)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成可向量化的产品和术语知识 JSON")
    parser.add_argument("knowledge_dir", type=Path, nargs="?", default=Path("knowledge"))
    args = parser.parse_args()
    glossary_count = convert(args.knowledge_dir / "glossary.json", args.knowledge_dir / "glossary.vector.json", glossary_entries)
    product_count = convert(args.knowledge_dir / "products.json", args.knowledge_dir / "products.vector.json", product_entries)
    print(f"Generated {glossary_count} glossary entries and {product_count} product entries")


if __name__ == "__main__":
    main()
