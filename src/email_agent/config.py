"""
配置加载器：从 config.yaml 和环境变量读取配置。
"""
import os
from pathlib import Path
from email_agent.paths import get_project_paths

import yaml
from dotenv import load_dotenv


_REQUIRED_ENV_VARS = {
    "MAIL_ACCOUNT": ("mail", "account"),
    "MAIL_PASSWORD": ("mail", "password"),
    "AI_API_KEY": ("ai", "api_key"),
}


def load_config(config_path: str | Path | None = None) -> dict:
    """加载配置；系统环境变量优先于项目根目录的 .env。"""
    project_dir = get_project_paths().root
    config_file = Path(config_path) if config_path else project_dir / "config.yaml"

    load_dotenv(project_dir / ".env", override=False)

    with config_file.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    missing = []
    for env_name, (section, key) in _REQUIRED_ENV_VARS.items():
        value = os.getenv(env_name, config.get(section, {}).get(key, ""))
        if isinstance(value, str):
            value = value.strip()
        config[section][key] = value
        if not value:
            missing.append(env_name)

    if missing:
        names = ", ".join(missing)
        raise ValueError(
            f"缺少必需配置: {names}。请复制 .env.example 为 .env 后填写真实值。"
        )

    return config
