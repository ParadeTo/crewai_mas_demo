"""
课程：27｜Human as 甲方
示例文件：m4l27_run.py

演示：4步任务链 + 2个人工确认节点（步骤1支持多轮需求澄清）
  步骤1（Manager）：需求澄清 → 写 requirements.md（最多 MAX_CLARIFICATION_ROUNDS 轮）
  [人工确认节点1] run.py 以 manager 身份写 human.json:needs_confirm → 等待用户确认
                  用户可输入补充意见触发下一轮修订，直到满意为止
  步骤2（Manager）：读SOP → 向 PM 发送 task_assign
  步骤3（PM）：读邮件 → 写产品文档 → 发 manager.json:task_done
  [人工确认节点2] run.py 以 manager 身份写 human.json:checkpoint_request → 等待用户确认
  步骤4（Manager）：读邮件 → 验收产品文档 → 保存验收结论

核心教学点（对应第27课）：
  - 单一接口原则：human.json 只由 run.py（以 manager 身份）写入，LLM Agent 不直接接触
  - 人工确认节点：run.py 控制时机，不由 LLM 决定何时打扰人
  - wait_for_human()：用 FileLock 读 human.json，模拟异步人机交互
  - 多轮澄清：编排器控制循环，LLM 无状态，Session hook 负责历史恢复
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from filelock import FileLock

# 需求澄清最大轮次，可通过环境变量覆盖
MAX_CLARIFICATION_ROUNDS = int(os.getenv("MAX_CLARIFICATION_ROUNDS", "3"))

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

from crewai.hooks import clear_before_llm_call_hooks  # noqa: E402
from tools.mailbox_ops import send_mail                # noqa: E402

SHARED_DIR    = _M4L27_DIR / "workspace" / "shared"
MAILBOXES_DIR = SHARED_DIR / "mailboxes"
DESIGN_DIR    = SHARED_DIR / "design"


# ─────────────────────────────────────────────────────────────────────────────
# 人工确认返回值
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class HumanDecision:
    """
    wait_for_human() 的返回值，封装用户决策结果。

    confirmed: True 表示用户确认（y），False 表示拒绝（n）
    feedback:  拒绝时收集的补充意见（allow_feedback=True 时才有值）

    实现了 __bool__，允许 `if decision:` 的简洁写法（等价于 `if decision.confirmed:`）。
    """
    confirmed: bool
    feedback: Optional[str] = None

    def __bool__(self) -> bool:
        return self.confirmed


# ─────────────────────────────────────────────────────────────────────────────
# 人工确认核心函数
# ─────────────────────────────────────────────────────────────────────────────

def wait_for_human(
    human_inbox: Path,
    expected_type: str,
    step_label: str,
    allow_feedback: bool = False,
) -> HumanDecision:
    """
    等待人类确认 human.json 中的特定类型消息。

    1. 用 FileLock 读取 human.json，找到未读的 expected_type 消息
    2. 打印消息 subject + content
    3. input("你的决定 (y/n)：")
    4. y → 标记该消息 read=True，返回 HumanDecision(confirmed=True)
    5. n → 标记该消息 read=True + rejected=True；
           若 allow_feedback=True，追加收集补充意见
           返回 HumanDecision(confirmed=False, feedback=...)

    注意：y/n 都会标记消息 read=True，防止多轮场景下旧消息被重复命中。

    Args:
        human_inbox:    human.json 的完整路径
        expected_type:  期望的消息类型（"needs_confirm" | "checkpoint_request"）
        step_label:     打印标签，如"需求确认"
        allow_feedback: 是否在用户拒绝时收集补充意见（步骤1多轮澄清时传 True）

    Returns:
        HumanDecision，confirmed=True 表示用户确认，False 表示拒绝
    """
    lock_path = human_inbox.with_suffix(".lock")

    # ── 步骤1：加锁读取消息（读完立即释放锁）────────────────────────────────
    with FileLock(str(lock_path)):
        if not human_inbox.exists():
            human_inbox.write_text("[]", encoding="utf-8")
        messages = json.loads(human_inbox.read_text(encoding="utf-8"))

    target = next(
        (m for m in messages if m.get("type") == expected_type and not m.get("read", False)),
        None,
    )

    if target is None:
        print(f"\n[debug] ⚠️  human.json 中未找到未读的 '{expected_type}' 消息")
        print(f"[debug] 实际内容：{json.dumps(messages, ensure_ascii=False, indent=2)}")
        return HumanDecision(confirmed=False)

    # ── 步骤2：打印消息，等待用户输入（锁已释放）────────────────────────────
    print(f"\n{'='*60}")
    print(f"  ⏸️  [人工确认节点] {step_label}")
    print(f"  来自：{target.get('from', 'manager')}")
    print(f"  主题：{target.get('subject', '')}")
    print(f"  内容：{target.get('content', '')[:300]}")  # 截断避免LLM原始输出太长
    print(f"{'='*60}")

    decision = input("  你的决定 (y/n)：").strip().lower()
    confirmed = decision == "y"

    # ── 步骤3：收集补充意见（仅 allow_feedback=True 且用户拒绝时）──────────
    feedback: Optional[str] = None
    if not confirmed and allow_feedback:
        print("  请输入你的补充意见（直接回车跳过）：")
        raw_feedback = input("  补充意见：").strip()
        feedback = raw_feedback if raw_feedback else None

    # ── 步骤4：加锁写回已读状态（y/n 都标记，防止多轮命中旧消息）────────────
    with FileLock(str(lock_path)):
        # 重新读取以获取最新状态（TOCTOU 窗口可接受，单进程 demo）
        messages = json.loads(human_inbox.read_text(encoding="utf-8"))
        for m in messages:
            if m.get("id") == target["id"]:
                m["read"] = True
                if not confirmed:
                    m["rejected"] = True          # 审计用，不参与流程路由
                if feedback:
                    m["human_feedback"] = feedback  # 审计用
                break
        human_inbox.write_text(
            json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if confirmed:
        print(f"  ✅ 已确认：{step_label}\n")
    else:
        print(f"  ↩️  已拒绝：{step_label}\n")

    return HumanDecision(confirmed=confirmed, feedback=feedback)


# ─────────────────────────────────────────────────────────────────────────────
# 多轮澄清辅助函数
# ─────────────────────────────────────────────────────────────────────────────

def _build_clarification_inputs(
    initial_request: str,
    feedback_history: list[str],
    round_num: int,
) -> dict:
    """
    构造 RequirementsDiscoveryCrew.kickoff() 的 inputs 字典。

    - 第1轮：只传 user_request，revision_context 为空，行为与改动前完全一致
    - 后续轮：revision_context 包含轮次信息 + 所有历史反馈，引导 LLM 针对性修订
    - 花括号转义：防止用户反馈中含 { / } 时 CrewAI format_map 报错

    Args:
        initial_request:  用户的原始需求描述
        feedback_history: 历轮拒绝时收集的反馈列表（首轮为空）
        round_num:        当前轮次（从1开始）

    Returns:
        可直接传入 crew.kickoff(inputs=...) 的字典
    """
    if round_num == 1 or not feedback_history:
        return {"user_request": initial_request, "revision_context": ""}

    # 对用户反馈转义花括号，防止 CrewAI 的 str.format_map() 出错
    safe_feedbacks = [fb.replace("{", "{{").replace("}", "}}") for fb in feedback_history]
    feedback_text = "\n".join(
        f"  第{i+1}轮反馈：{fb}" for i, fb in enumerate(safe_feedbacks)
    )
    revision_context = (
        f"这是第 {round_num} 轮需求文档修订。\n"
        f"用户对上一版本不满意，反馈如下：\n{feedback_text}\n\n"
        f"请基于以上反馈修订需求文档，重点修改用户指出的问题，未被质疑的部分保持不变。"
    )
    return {"user_request": initial_request, "revision_context": revision_context}


# ─────────────────────────────────────────────────────────────────────────────
# 步骤间结构检查
# ─────────────────────────────────────────────────────────────────────────────

def check_requirements_exists() -> bool:
    return (SHARED_DIR / "needs" / "requirements.md").exists()


def check_pm_inbox_has_task_assign() -> bool:
    pm_inbox = MAILBOXES_DIR / "pm.json"
    if not pm_inbox.exists():
        return False
    messages = json.loads(pm_inbox.read_text(encoding="utf-8"))
    return any(m.get("type") == "task_assign" for m in messages)


def check_product_spec_exists() -> bool:
    return (DESIGN_DIR / "product_spec.md").exists()


def check_manager_inbox_has_task_done() -> bool:
    manager_inbox = MAILBOXES_DIR / "manager.json"
    if not manager_inbox.exists():
        return False
    messages = json.loads(manager_inbox.read_text(encoding="utf-8"))
    return any(m.get("type") == "task_done" for m in messages)


def check_human_confirmed(expected_type: str) -> bool:
    """检查 human.json 中是否有已读的 expected_type 消息（确认标志）"""
    human_inbox = MAILBOXES_DIR / "human.json"
    lock_path   = human_inbox.with_suffix(".lock")
    if not human_inbox.exists():
        return False
    with FileLock(str(lock_path)):
        messages = json.loads(human_inbox.read_text(encoding="utf-8"))
    return any(
        m.get("type") == expected_type and m.get("read", False)
        for m in messages
    )


# ─────────────────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────────────────

def run_demo(initial_request: str = "") -> None:
    from m4l27_manager import (  # 延迟导入，避免在测试中加载 CrewAI
        RequirementsDiscoveryCrew,
        ManagerAssignCrew,
        ManagerReviewCrew,
        save_session as manager_save,
    )
    from m4l27_pm import PMExecuteCrew, save_session as pm_save

    session_id = str(uuid.uuid4())
    if not initial_request:
        initial_request = input("请告诉 Manager 你要做什么：").strip()

    print(f"\n{'='*60}")
    print(f"  M4L27 Human as 甲方演示  |  session: {session_id[:8]}...")
    print(f"{'='*60}\n")

    # ── 步骤1：Manager 多轮需求澄清 ──────────────────────────────────────
    # 控制权在编排器：循环由 run_demo() 驱动，LLM 每轮无状态执行单轮任务
    # Session 复用：相同 session_id 传给每轮，@before_llm_call hook 自动恢复历史
    feedback_history: list[str] = []

    for round_num in range(1, MAX_CLARIFICATION_ROUNDS + 1):
        print(f"【步骤1】需求澄清 第{round_num}/{MAX_CLARIFICATION_ROUNDS}轮...（RequirementsDiscoveryCrew）\n")
        # ⚠️ clear 必须在 RequirementsDiscoveryCrew().__init__ 之前，
        #    hook 注册发生在 crew() 内部，顺序：clear → init → crew()
        clear_before_llm_call_hooks()

        inputs = _build_clarification_inputs(
            initial_request=initial_request,
            feedback_history=feedback_history,
            round_num=round_num,
        )

        req_crew = RequirementsDiscoveryCrew(session_id=session_id)
        result1 = req_crew.crew().kickoff(inputs=inputs)
        manager_save(req_crew, session_id)
        result1_text = getattr(result1, "raw", str(result1))
        print(f"\n步骤1 输出（第{round_num}轮）：{result1_text[:200]}...\n")

        if not check_requirements_exists():
            print("⚠️  步骤1未完成：requirements.md 未生成，终止运行")
            return

        # run.py（编排者）以 manager 身份写 human.json，而非 LLM Agent 直接写
        send_mail(
            MAILBOXES_DIR,
            to="human",
            from_="manager",
            type_="needs_confirm",
            subject=f"需求文档（第{round_num}轮）待确认",
            content=(
                f"需求文档路径：shared/needs/requirements.md\n"
                f"当前轮次：{round_num}/{MAX_CLARIFICATION_ROUNDS}"
            ),
        )

        decision1 = wait_for_human(
            MAILBOXES_DIR / "human.json",
            expected_type="needs_confirm",
            step_label=f"需求文档确认（第{round_num}轮）",
            allow_feedback=True,   # 开启反馈收集，支持多轮修订
        )

        if decision1.confirmed:
            print(f"✅ 用户已确认需求文档（共 {round_num} 轮）\n")
            break

        # 用户拒绝：收集反馈准备下一轮
        fb = decision1.feedback or "用户对文档不满意，请重新审视所有待确认项"
        feedback_history.append(fb)
        print(f"  📝 已记录反馈，准备第 {round_num + 1} 轮修订...\n")

        if round_num == MAX_CLARIFICATION_ROUNDS:
            print(f"⚠️  已达最大轮次（{MAX_CLARIFICATION_ROUNDS}），用户仍未确认，终止运行")
            print("  提示：可通过 MAX_CLARIFICATION_ROUNDS 环境变量增加最大轮次")
            return

    # ── 步骤2：Manager 分配任务 ────────────────────────────────────────────
    print("【步骤2】Manager 按SOP分配任务给PM...（ManagerAssignCrew）\n")
    clear_before_llm_call_hooks()
    assign_crew = ManagerAssignCrew(session_id=session_id)
    result2 = assign_crew.crew().kickoff(inputs={
        "user_request": "请读取需求文档和产品设计SOP，向PM发送产品文档设计任务"
    })
    manager_save(assign_crew, session_id)
    print(f"\n步骤2 输出：{result2}\n")

    if not check_pm_inbox_has_task_assign():
        print("⚠️  步骤2未完成：PM 邮箱中未找到 task_assign，终止运行")
        return
    print("✅ 步骤2检查通过：PM 邮箱已有任务分配邮件\n")

    # ── 步骤3：PM 执行任务 ─────────────────────────────────────────────────
    print("【步骤3】PM 撰写产品文档...（PMExecuteCrew）\n")
    clear_before_llm_call_hooks()
    pm_crew = PMExecuteCrew(session_id=session_id)
    result3 = pm_crew.crew().kickoff(inputs={
        "user_request": "请读取邮箱中的任务，根据需求文档撰写产品规格文档，完成后通知Manager"
    })
    pm_save(pm_crew, session_id)
    print(f"\n步骤3 输出：{result3}\n")

    if not check_product_spec_exists():
        print("⚠️  步骤3未完成：产品文档未生成，终止运行")
        return
    if not check_manager_inbox_has_task_done():
        print("⚠️  步骤3未完成：Manager 邮箱中未找到 task_done，终止运行")
        return
    print("✅ 步骤3检查通过：产品文档已生成，Manager 收到完成通知\n")

    # ── 人工确认节点2：设计文档 Checkpoint ────────────────────────────────
    # 单一接口原则：PM 只发给 Manager，由 run.py（Manager 身份）转告人类
    send_mail(
        MAILBOXES_DIR,
        to="human",
        from_="manager",
        type_="checkpoint_request",
        subject="产品文档已完成，请确认后继续",
        content="产品文档路径：shared/design/product_spec.md（请打开查阅后确认）",
    )

    confirmed2 = wait_for_human(
        MAILBOXES_DIR / "human.json",
        expected_type="checkpoint_request",
        step_label="设计文档 Checkpoint",
    )
    if not confirmed2.confirmed:
        return

    # ── 步骤4：Manager 验收 ────────────────────────────────────────────────
    print("【步骤4】Manager 验收中...（ManagerReviewCrew）\n")
    clear_before_llm_call_hooks()
    review_crew = ManagerReviewCrew(session_id=session_id)
    result4 = review_crew.crew().kickoff(inputs={
        "user_request": "请读取邮箱中的完成通知，验收产品文档并保存验收结论"
    })
    manager_save(review_crew, session_id)
    print(f"\n步骤4 输出：{result4}\n")

    print(f"\n{'='*60}")
    print("  ✅ 演示完成！")
    print(f"  产品文档：workspace/shared/design/product_spec.md")
    print(f"  验收结论：workspace/manager/review_result.md")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    run_demo()
