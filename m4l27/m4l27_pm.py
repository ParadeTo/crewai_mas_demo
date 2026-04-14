"""
课程：27｜Human as 甲方
示例文件：m4l27_pm.py

PM Crew：与 L26 结构完全相同。

单一接口约束体现：
  PM 完成设计后只向 manager.json 发 task_done，
  不接触 human.json——由 run.py 在检测到 task_done 后，
  以 manager 身份转发 checkpoint_request 给人类。
"""

from __future__ import annotations

import sys
from pathlib import Path

from crewai import Agent, Crew, Task
from crewai.hooks import LLMCallHookContext, before_llm_call, clear_before_llm_call_hooks
from crewai.project import CrewBase, agent, crew, task

# ── 路径设置 ──────────────────────────────────────────────────────────────────
_M4L27_DIR    = Path(__file__).resolve().parent
_PROJECT_ROOT = _M4L27_DIR.parent
for _p in [str(_M4L27_DIR), str(_PROJECT_ROOT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)
# 确保 _M4L27_DIR 始终排在 _PROJECT_ROOT 前面（优先解析 m4l27/tools/）
if sys.path.index(str(_M4L27_DIR)) > sys.path.index(str(_PROJECT_ROOT)):
    sys.path.remove(str(_M4L27_DIR))
    sys.path.insert(0, str(_M4L27_DIR))

from llm import aliyun_llm                           # noqa: E402
from tools.skill_loader_tool import SkillLoaderTool  # noqa: E402

from m3l20.m3l20_file_memory import (                # noqa: E402
    build_bootstrap_prompt,
    prune_tool_results,
    maybe_compress,
    append_session_raw,
    save_session_ctx,
)
from m4l27_manager import _SessionMixin              # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# 路径常量
# ─────────────────────────────────────────────────────────────────────────────

WORKSPACE_DIR  = _M4L27_DIR / "workspace" / "pm"
SESSIONS_DIR   = WORKSPACE_DIR / "sessions"
SHARED_DIR     = _M4L27_DIR / "workspace" / "shared"
MAILBOXES_DIR  = SHARED_DIR / "mailboxes"

# ─────────────────────────────────────────────────────────────────────────────
# 沙盒挂载描述（PM）
# ─────────────────────────────────────────────────────────────────────────────

M4L27_PM_SANDBOX_MOUNT_DESC = (
    "1. 所有的操作必须在沙盒中执行，不得操作本地文件系统。\n"
    "   当前已挂载的目录：\n"
    "   - ./workspace/pm:/workspace:rw（PM 个人区，可读写）\n"
    "   - ./workspace/shared:/mnt/shared:rw（共享工作区，含邮箱，可读写）\n"
    "   - ../skills:/mnt/skills:ro（共享 skills 目录，只读）\n\n"
    "2. 记忆文件读写规范：\n"
    "   - 个人区读写：/workspace/<filename>\n"
    "   - 需求文档：/mnt/shared/needs/requirements.md（只读）\n"
    "   - 产品文档：/mnt/shared/design/product_spec.md（PM 负责写入）\n"
    "   - 邮箱：/mnt/shared/mailboxes/（通过 mailbox-ops skill 操作）\n\n"
    "3. 参考型 Skill（type: reference）：内容直接注入上下文，无需沙盒\n\n"
    "4. 如遇依赖缺失，先在沙盒中安装再继续\n\n"
    "5. 重要：PM 的邮件只能发给 manager（to: manager），"
    "   不得发给 human——所有与人类的沟通通过 Manager 统一处理。"
)


# ─────────────────────────────────────────────────────────────────────────────
# PM Crew：执行产品设计任务
# ─────────────────────────────────────────────────────────────────────────────

@CrewBase
class PMExecuteCrew(_SessionMixin):
    """
    PM 执行阶段：读取任务邮件 → 读需求 → 写产品文档 → 发完成通知给 Manager

    单一接口约束体现（对应第27课 P2 核心设计原则）：
    - PM 只向 manager.json 发 task_done，不直接写 human.json
    - 沙盒挂载描述中明确说明此约束，帮助 LLM 遵守规范
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._init_session_state(SESSIONS_DIR)

    @agent
    def pm_agent(self) -> Agent:
        backstory = build_bootstrap_prompt(WORKSPACE_DIR)
        return Agent(
            role      = "产品经理（PM）",
            goal      = "读取任务邮件，根据需求文档写出规范的产品设计文档，并通知 Manager 验收",
            backstory = backstory,
            llm       = aliyun_llm.AliyunLLM(model="qwen-max", temperature=0.3),
            tools     = [SkillLoaderTool(
                sandbox_mount_desc=M4L27_PM_SANDBOX_MOUNT_DESC,
                sandbox_mcp_url="http://localhost:8028/mcp",
            )],
            verbose   = True,
            max_iter  = 20,
        )

    @task
    def execute_task(self) -> Task:
        return Task(
            description     = "{user_request}",
            expected_output = (
                "完成以下四步：\n"
                "1. 调用 skill_loader 工具（skill_name='mailbox-ops'），在 task_context 中说明：\n"
                "   读取 PM 邮箱（read_inbox），获取 Manager 的任务分配邮件（type: task_assign）\n"
                "2. 调用 skill_loader 工具（skill_name='memory-save'），在 task_context 中说明：\n"
                "   读取 /mnt/shared/needs/requirements.md 理解功能需求和验收标准\n"
                "3. 根据需求撰写产品规格文档，调用 skill_loader 工具（skill_name='memory-save'），\n"
                "   在 task_context 中说明：\n"
                "   - 将产品规格文档写入 /mnt/shared/design/product_spec.md\n"
                "   - 写入方式：sandbox_file_operations(action=write)\n"
                "   - 文档必须包含：产品概述 + 用户故事 + 功能规格（F-01/F-02/...）+ 验收标准\n"
                "   - task_context 中必须含完整文档内容\n"
                "4. 调用 skill_loader 工具（skill_name='mailbox-ops'），在 task_context 中说明：\n"
                "   向 Manager 发送完成通知（只发给 manager，不发给其他人）：\n"
                "   - to: manager\n"
                "   - from_: pm\n"
                "   - type_: task_done\n"
                "   - subject: 产品文档已完成\n"
                "   - content: 完成说明（包含文档路径引用，不要复制文档全文）\n"
                "   注意：skill_loader 是唯一可用工具，不要尝试直接调用 mailbox-ops 或 memory-save 作为 Action\n"
                "确认邮件发送成功后输出：「产品文档已完成，已通知 Manager 验收」"
            ),
            agent = self.pm_agent(),
        )

    @crew
    def crew(self) -> Crew:
        return Crew(agents=self.agents, tasks=self.tasks, verbose=True)

    @before_llm_call
    def before_llm_hook(self, context: LLMCallHookContext) -> bool | None:
        if not self._session_loaded:
            self._restore_session(context)
            self._session_loaded = True
        self._last_msgs = context.messages
        prune_tool_results(context.messages)
        maybe_compress(context.messages, context)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 公共辅助函数
# ─────────────────────────────────────────────────────────────────────────────

def save_session(crew_instance: PMExecuteCrew, session_id: str) -> None:
    """保存 session 上下文（复用 m3l20 逻辑）"""
    if crew_instance._last_msgs:
        new_msgs = list(crew_instance._last_msgs)[crew_instance._history_len:]
        append_session_raw(session_id, new_msgs, SESSIONS_DIR)
        save_session_ctx(session_id, list(crew_instance._last_msgs), SESSIONS_DIR)
