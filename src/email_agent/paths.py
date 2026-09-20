"""统一项目路径。"""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    runtime_root: Path
    source: Path
    knowledge: Path
    data: Path
    drafts: Path
    config: Path


def get_project_paths() -> ProjectPaths:
    root = Path(__file__).resolve().parents[2]
    runtime_value = os.getenv("EMAIL_AGENT_RUNTIME_ROOT", "").strip()
    runtime_root = Path(runtime_value).expanduser() if runtime_value else root
    if not runtime_root.is_absolute():
        runtime_root = root / runtime_root
    runtime_root = runtime_root.resolve()
    return ProjectPaths(
        root=root,
        runtime_root=runtime_root,
        source=root / "src",
        knowledge=root / "knowledge",
        data=runtime_root / "data",
        drafts=runtime_root / "drafts",
        config=root / "config.yaml",
    )


def resolve_from_root(value: str | Path, root: Path | None = None) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (root or get_project_paths().root) / path
