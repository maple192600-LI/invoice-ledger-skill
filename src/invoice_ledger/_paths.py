"""项目级路径锚点。

所有需要定位项目根的模块都从这里导入 ``PROJECT_ROOT``，
避免在多个文件里各自用 ``Path(__file__).parents[N]`` 数层数——
那种写法在模块移动到子包后会立刻失效。
"""

from __future__ import annotations

from pathlib import Path


def _find_project_root() -> Path:
    """项目根 = 包含 ``SKILL.md`` 的祖先目录。

    用 ``SKILL.md`` 作锚点，模块在包内任意移动时本计算仍然正确，
    对子包重构免疫。
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "SKILL.md").exists():
            return parent
    raise RuntimeError("无法定位项目根：未在任何祖先目录找到 SKILL.md")


PROJECT_ROOT = _find_project_root()
