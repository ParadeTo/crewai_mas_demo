"""
m4l27/tools/__init__.py

将父包 (crewai_mas_demo/tools/) 的所有内容透传，
同时通过 sys.modules 注入本课专属的 mailbox_ops 模块。

这样 `from tools.skill_loader_tool import ...` 和
`from tools.mailbox_ops import ...` 都能正常工作。
"""

import importlib.util as _util
import sys
from pathlib import Path

_here         = Path(__file__).resolve().parent          # m4l27/tools/
_project_root = _here.parent.parent                      # crewai_mas_demo/
_parent_tools = _project_root / "tools"                  # crewai_mas_demo/tools/


def _register_module(module_name: str, file_path: Path) -> None:
    """将指定文件注册到 sys.modules[module_name]。"""
    spec = _util.spec_from_file_location(module_name, str(file_path))
    if spec is None:
        return
    mod = _util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)


# 1. 注册父包子模块（逐个注册，避免执行父包 __init__.py 中的循环导入）
_parent_subs = [
    "skill_loader_tool",
    "baidu_search",
    "add_image_tool_local",
    "fixed_directory_read_tool",
    "intermediate_tool",
]
for _sub in _parent_subs:
    _key = f"tools.{_sub}"
    if _key not in sys.modules:
        _path = _parent_tools / f"{_sub}.py"
        if _path.exists():
            try:
                _register_module(_key, _path)
            except Exception:
                pass  # 可选依赖，导入失败时静默跳过

# 2. 将父包子模块的公开属性注入本模块（使 from tools import BaiduSearchTool 等可用）
# 同时收集所有父包模块的公开类名（无论是否有 __all__，都扫描大写开头的类）
for _sub in _parent_subs:
    _key = f"tools.{_sub}"
    if _key in sys.modules:
        _sub_mod = sys.modules[_key]
        if hasattr(_sub_mod, "__all__"):
            for _attr in _sub_mod.__all__:
                globals()[_attr] = getattr(_sub_mod, _attr)
        else:
            # 没有 __all__ 时，导出所有大写开头的公共名称（通常是类）
            for _attr, _val in vars(_sub_mod).items():
                if not _attr.startswith("_") and _attr[0].isupper():
                    globals()[_attr] = _val

# 3. 注册本课专属的 mailbox_ops（覆盖父包中不存在的同名模块）
_register_module("tools.mailbox_ops", _here / "mailbox_ops.py")
