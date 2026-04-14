"""
课程：27｜Human as 甲方
示例文件：m4l27_manager.py

Manager 三个 Crew：
  RequirementsDiscoveryCrew  新增：需求澄清，用 requirements-discovery skill 发问，写 requirements.md
  ManagerAssignCrew          复用 L26：读 SOP，向 PM 发送 task_assign
  ManagerReviewCrew          复用 L26：读 PM 回邮，验收产品文档

与 m4l26_manager.py 的复用关系：
  - build_bootstrap_prompt / load_session_ctx / save_session_ctx / append_session_raw：完全复用
  - SkillLoaderTool / prune_tool_results / maybe_compress：完全复用
  - ManagerAssignCrew / ManagerReviewCrew：结构完全复用，沙盒描述更新为 m4l27 路径

第27课新增：
  - RequirementsDiscoveryCrew：使用 requirements-discovery skill，写 requirements.md
  - 沙盒挂载新增 /mnt/shared/sop 目录（Manager 读取 SOP 文件）
  - 单一接口约束：Manager 不直接写 human.json；由 run.py 编排
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
    load_session_ctx,
    save_session_ctx,
    append_session_raw,
    prune_tool_results,
    maybe_compress,
)

# ─────────────────────────────────────────────────────────────────────────────
# 路径常量
# ─────────────────────────────────────────────────────────────────────────────

WORKSPACE_DIR  = _M4L27_DIR / "workspace" / "manager"
SESSIONS_DIR   = WORKSPACE_DIR / "sessions"
SHARED_DIR     = _M4L27_DIR / "workspace" / "shared"
MAILBOXES_DIR  = SHARED_DIR / "mailboxes"

# ─────────────────────────────────────────────────────────────────────────────
# 沙盒挂载描述（Manager）
# ─────────────────────────────────────────────────────────────────────────────

M4L27_MANAGER_SANDBOX_MOUNT_DESC = (
    "1. 所有的操作必须在沙盒中执行，不得操作本地文件系统。\n"
    "   当前已挂载的目录：\n"
    "   - ./workspace/manager:/workspace:rw（Manager 个人区，可读写）\n"
    "   - ./workspace/shared:/mnt/shared:rw（共享工作区，含邮箱，可读写）\n"
    "   - ../skills:/mnt/skills:ro（共享 skills 目录，只读）\n\n"
    "2. 记忆文件读写规范：\n"
    "   - 个人区读写：/workspace/<filename>\n"
    "   - 需求文档：/mnt/shared/needs/requirements.md（Manager 可写）\n"
    "   - 产品文档：/mnt/shared/design/product_spec.md（只读，PM 负责写入）\n"
    "   - SOP 目录：/mnt/shared/sop/（只读，Manager 读取 SOP 文件）\n"
    "   - 邮箱：/mnt/shared/mailboxes/（通过 mailbox-ops skill 操作）\n\n"
    "3. 参考型 Skill（type: reference）：内容直接注入上下文，无需沙盒\n\n"
    "4. 如遇依赖缺失，先在沙盒中安装再继续"
)


# ─────────────────────────────────────────────────────────────────────────────
# 公共 Mixin：session 管理（所有 Manager Crew 共用）
# ─────────────────────────────────────────────────────────────────────────────

class _SessionMixin:
    """为各 Crew 提供统一的 session 保存/恢复逻辑（Manager 和 PM 均可复用）。"""

    session_id: str
    _sessions_dir: Path
    _session_loaded: bool
    _last_msgs: list[dict]
    _history_len: int

    def _init_session_state(self, sessions_dir: Path) -> None:
        self._sessions_dir   = sessions_dir
        self._session_loaded = False
        self._last_msgs      = []
        self._history_len    = 0

    def _restore_session(self, context: LLMCallHookContext) -> None:
        history = load_session_ctx(self.session_id, self._sessions_dir)
        self._history_len = len(history)
        if not history:
            return
        current_user_msg = next(
            (m for m in reversed(context.messages) if m.get("role") == "user"), None
        )
        context.messages.clear()
        context.messages.extend(history)
        if current_user_msg is not None:
            context.messages.append(current_user_msg)

    def _build_agent(self, role: str, goal: str) -> Agent:
        return Agent(
            role      = role,
            goal      = goal,
            backstory = build_bootstrap_prompt(WORKSPACE_DIR),
            llm       = aliyun_llm.AliyunLLM(model="qwen-max", temperature=0.3),
            tools     = [SkillLoaderTool(
                sandbox_mount_desc=M4L27_MANAGER_SANDBOX_MOUNT_DESC,
                sandbox_mcp_url="http://localhost:8027/mcp",
            )],
            verbose   = True,
            max_iter  = 20,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Crew 1：需求澄清（新增）
# ─────────────────────────────────────────────────────────────────────────────

@CrewBase
class RequirementsDiscoveryCrew(_SessionMixin):
    """
    Manager 需求澄清阶段：
    - 使用 requirements-discovery skill 按四维发问框架（目标/边界/约束/风险）澄清需求
    - 将澄清结果整理成结构化需求文档，写入 /mnt/shared/needs/requirements.md
    - 完成后由 run.py 以 manager 身份写 human.json:needs_confirm（单一接口原则）

    核心教学点（对应第27课 P3）：
    - Manager 是唯一的需求接口，主动发问，而不是被动接收
    - 需求落文档才算数：未写入 requirements.md 的澄清无效
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._init_session_state(SESSIONS_DIR)

    @agent
    def manager_agent(self) -> Agent:
        return self._build_agent(
            role = "项目经理（Manager）",
            goal = "理解用户初步需求，用结构化方式澄清关键问题，整理成完整需求文档",
        )

    @task
    def discover_requirements_task(self) -> Task:
        return Task(
            description     = (
                "{user_request}\n\n"
                "{revision_context}"
                # 首轮：revision_context 为空字符串，LLM 只看到原始需求
                # 后续轮：revision_context 包含用户反馈，LLM 基于反馈修订上一版文档
                # （上一轮的完整推导过程由 Session hook 自动恢复到 context 中）
            ),
            expected_output = (
                "完成以下步骤：\n"
                "1. 阅读用户的初步需求\n"
                "2. 若 revision_context 不为空，优先针对用户反馈修改上一版文档；\n"
                "   否则调用 skill_loader 工具（skill_name='requirements-discovery'，task_context 留空）\n"
                "   获取四维框架（目标/边界/约束/风险）指南，按框架全新梳理需求\n"
                "   （如需澄清但无法实时追问，记录为「待确认」）\n"
                "3. 调用 skill_loader 工具（skill_name='memory-save'），在 task_context 中说明：\n"
                "   - 将整理后的需求文档写入路径：/mnt/shared/needs/requirements.md\n"
                "   - 写入方式：sandbox_file_operations(action=write)\n"
                "   - 文档必须包含以下章节：\n"
                "     ## 目标\n"
                "     ## 边界（本次做什么 / 不做什么）\n"
                "     ## 约束\n"
                "     ## 风险与待确认项\n"
                "     ## 验收标准\n"
                "   - task_context 中必须包含完整的文档内容\n"
                "   注意：skill_loader 是唯一可用工具，不要尝试直接调用 memory-save 或 requirements-discovery 作为 Action\n"
                "确认文档写入后输出：「需求文档已完成，路径：/mnt/shared/needs/requirements.md」"
            ),
            agent = self.manager_agent(),
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
# Crew 2：任务分配（复用 L26 结构）
# ─────────────────────────────────────────────────────────────────────────────

@CrewBase
class ManagerAssignCrew(_SessionMixin):
    """
    Manager 任务分配阶段：读 SOP → 分配任务给 PM

    核心教学点（对应第27课 P4）：
    - 任务执行必须遵循 SOP，Manager 先读 SOP 再分配
    - 邮件只传路径引用，不传文档全文（Design with File 范式）
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._init_session_state(SESSIONS_DIR)

    @agent
    def manager_agent(self) -> Agent:
        return self._build_agent(
            role = "项目经理（Manager）",
            goal = "读取 SOP 和需求文档，通过邮箱向 PM 分配产品文档设计任务",
        )

    @task
    def assign_task(self) -> Task:
        return Task(
            description     = "{user_request}",
            expected_output = (
                "必须严格按以下步骤完成，不得跳过任何一步：\n"
                "1. 【读SOP】调用 skill_loader 工具，参数：skill_name='memory-save'，\n"
                "   task_context 中包含：\n"
                "   {\n"
                "     \"path\": \"/mnt/shared/sop/product_design_sop.md\",\n"
                "     \"action\": \"read\"\n"
                "   }\n"
                "   等待 SOP 读取结果\n"
                "2. 【读需求】调用 skill_loader 工具，参数：skill_name='memory-save'，\n"
                "   task_context 中包含：\n"
                "   {\n"
                "     \"path\": \"/mnt/shared/needs/requirements.md\",\n"
                "     \"action\": \"read\"\n"
                "   }\n"
                "   等待需求文档读取结果\n"
                "3. 【发邮件】调用 skill_loader 工具，参数：skill_name='mailbox-ops'，\n"
                "   task_context 必须包含完整的发邮件指令：\n"
                "   {\n"
                "     \"action\": \"send_mail\",\n"
                "     \"to\": \"pm\",\n"
                "     \"from_\": \"manager\",\n"
                "     \"type_\": \"task_assign\",\n"
                "     \"subject\": \"产品文档设计任务\",\n"
                "     \"content\": \"请根据需求文档（/mnt/shared/needs/requirements.md）按照产品设计SOP撰写产品规格文档，写入/mnt/shared/design/product_spec.md，完成后发邮件通知我验收\",\n"
                "     \"expected_output\": {\"errcode\": 0, \"errmsg\": \"success\", \"msg_id\": \"任意UUID\"}\n"
                "   }\n"
                "   ⚠️ 注意：第3步必须在第1、2步之后执行，且 skill_name 必须是 'mailbox-ops'\n"
                "   skill_loader 是唯一可用工具，不要尝试直接调用 mailbox-ops 作为 Action\n"
                "输出：「任务已分配给 PM，mailbox-ops 返回 errcode=0」"
            ),
            agent = self.manager_agent(),
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
# Crew 3：验收（复用 L26 结构）
# ─────────────────────────────────────────────────────────────────────────────

@CrewBase
class ManagerReviewCrew(_SessionMixin):
    """
    Manager 验收阶段：读 PM 回邮 → 验收产品文档 → 保存验收结论

    核心教学点（对应第27课 P4/P5）：
    - Checkpoint 之外的验收由 Manager 自主完成，不再打扰人
    - 验收结论写入个人区（/workspace/review_result.md），形成审计记录
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._init_session_state(SESSIONS_DIR)

    @agent
    def manager_agent(self) -> Agent:
        return self._build_agent(
            role = "项目经理（Manager）",
            goal = "读取 PM 完成通知，验收产品文档，保存验收结论",
        )

    @task
    def review_task(self) -> Task:
        return Task(
            description     = "{user_request}",
            expected_output = (
                "完成以下三步：\n"
                "1. 调用 skill_loader 工具（skill_name='mailbox-ops'），在 task_context 中说明：\n"
                "   读取 Manager 邮箱（read_inbox），获取 PM 的完成通知（type: task_done）\n"
                "2. 调用 skill_loader 工具（skill_name='memory-save'），在 task_context 中说明：\n"
                "   读取产品文档 /mnt/shared/design/product_spec.md\n"
                "3. 根据需求的验收标准逐项检查，调用 skill_loader 工具（skill_name='memory-save'），\n"
                "   在 task_context 中说明：\n"
                "   - 将验收结论保存至 /workspace/review_result.md\n"
                "   - 写入方式：sandbox_file_operations(action=write)\n"
                "   - 格式：验收结论（通过/需返工）+ 检查项逐条说明\n"
                "   注意：skill_loader 是唯一可用工具，不要尝试直接调用 mailbox-ops 或 memory-save 作为 Action\n"
                "输出验收结论摘要"
            ),
            agent = self.manager_agent(),
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

def save_session(
    crew_instance: RequirementsDiscoveryCrew | ManagerAssignCrew | ManagerReviewCrew,
    session_id: str,
) -> None:
    """保存 session 上下文（复用 m3l20 逻辑）"""
    if crew_instance._last_msgs:
        new_msgs = list(crew_instance._last_msgs)[crew_instance._history_len:]
        append_session_raw(session_id, new_msgs, SESSIONS_DIR)
        save_session_ctx(session_id, list(crew_instance._last_msgs), SESSIONS_DIR)
