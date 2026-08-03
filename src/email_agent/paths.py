"""统一项目路径。"""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    source: Path
    knowledge: Path
    data: Path
    drafts: Path
    config: Path


def get_project_paths() -> ProjectPaths:
    root = Path(__file__).resolve().parents[2]
    return ProjectPaths(
        root=root,
        source=root / "src",
        knowledge=root / "knowledge",
        data=root / "data",
        drafts=root / "drafts",
        config=root / "config.yaml",
    )


def resolve_from_root(value: str | Path, root: Path | None = None) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (root or get_project_paths().root) / path
