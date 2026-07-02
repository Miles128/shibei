"""Profile 管理器 — 多场景配置文件切换"""

import shutil
from pathlib import Path

PROFILES_DIR = Path.home() / ".shibei" / "profiles"


def ensure_profiles_dir():
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)


def list_profiles() -> list[str]:
    """列出所有 profile 名称"""
    ensure_profiles_dir()
    return sorted(
        [f.stem for f in PROFILES_DIR.glob("*.yaml") if f.is_file()]
    )


def get_profile_path(name: str) -> Path:
    """获取 profile 配置文件路径"""
    return PROFILES_DIR / f"{name}.yaml"


def profile_exists(name: str) -> bool:
    return get_profile_path(name).exists()


def create_profile(name: str, template: 'str | None' = None) -> Path:
    """创建新 profile，可选从已有 profile 或 example 模板复制"""
    ensure_profiles_dir()
    target = get_profile_path(name)

    if target.exists():
        raise FileExistsError(f"Profile '{name}' already exists")

    if template and profile_exists(template):
        shutil.copy(get_profile_path(template), target)
    elif template:
        raise FileNotFoundError(f"Template profile '{template}' not found")
    else:
        # 从 config.example.yaml 复制
        example = Path("config.example.yaml")
        if example.exists():
            shutil.copy(example, target)
        else:
            target.write_text(
                "# Shibei profile: {}\n".format(name)
                + "storage:\n  path: ~/.shibei/db\n"
                + "sources:\n  - ~/Documents/notes\n"
                + "tagging:\n  rules:\n    - pattern: \".*\"\n      tag: misc\n"
            )

    return target


def delete_profile(name: str):
    path = get_profile_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Profile '{name}' not found")
    path.unlink()


def resolve_config_path(config: 'str | None' = None, profile: 'str | None' = None) -> str:
    """解析配置路径。

    优先级：--config > --profile > 默认 config.yaml
    """
    if config:
        return config
    if profile:
        p = get_profile_path(profile)
        if p.exists():
            return str(p)
        raise FileNotFoundError(f"Profile '{profile}' not found. Run 'shibei profile create {profile}' first.")
    return "config.yaml"
