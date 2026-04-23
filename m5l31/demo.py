"""31课 Demo：观测 + 可靠性三策略。

运行方式：
    python3 demo.py                    # 正常运行
    python3 demo.py --budget 0.001     # 低预算，触发成本围栏
    python3 demo.py --loop             # 使用会循环的工具，触发循环检测
"""

from __future__ import annotations

import argparse
import atexit
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_DIR))

from crewai import Agent, Crew, LLM, Task
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from hook_framework import (
    CrewObservabilityAdapter,
    GuardrailDeny,
    HookLoader,
    HookRegistry,
)
from reliability import install_reliability_hooks


class SearchInput(BaseModel):
    query: str = Field(description="搜索关键词")


class KnowledgeSearchTool(BaseTool):
    name: str = "knowledge_search"
    description: str = "搜索知识库，返回关于指定主题的信息"
    args_schema: type[BaseModel] = SearchInput

    def _run(self, query: str) -> str:
        knowledge = {
            "可靠性": "可靠性策略包括重试、循环检测和成本围栏。重试用指数退避+Jitter，Agent场景最多2-3次。",
            "重试": "指数退避+Jitter是标准退避策略。Agent场景下重试次数应控制在2-3次，避免无效消耗。",
            "成本": "Agent成本不可预测，需要实时算账。Token估算+价格表=实时围栏，精确数据走Langfuse。",
            "循环": "Agent可能陷入工具调用循环。状态哈希去重可检测连续重复，及时终止避免浪费。",
            "AI Agent": "AI Agent是能自主感知环境、做出决策并采取行动的智能系统。可靠性是生产部署的关键挑战。",
        }
        results = []
        for key, val in knowledge.items():
            if key.lower() in query.lower() or query.lower() in key.lower():
                results.append(f"[{key}] {val}")
        if not results:
            for key, val in list(knowledge.items())[:2]:
                results.append(f"[{key}] {val}")
        return "\n\n".join(results)


class LoopingTool(BaseTool):
    name: str = "looping_search"
    description: str = "搜索（总是返回相同结果）"
    args_schema: type[BaseModel] = SearchInput

    def _run(self, query: str) -> str:
        return "搜索结果：AI Agent 可靠性是一个重要话题。"


def main():
    parser = argparse.ArgumentParser(description="31课 Demo")
    parser.add_argument("--budget", type=float, default=1.0)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--loop-threshold", type=int, default=3)
    args = parser.parse_args()

    session_id = f"sess_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    registry = HookRegistry()
    loader = HookLoader(registry)
    loader.load_two_layers(
        global_dir=_DIR / "shared_hooks",
        workspace_dir=_DIR / "workspace" / "demo_agent",
    )

    strategies = install_reliability_hooks(registry, config={
        "max_retries": args.max_retries,
        "loop_threshold": args.loop_threshold,
        "budget_usd": args.budget,
    })

    summary = registry.summary()
    total = sum(len(v) for v in summary.values())
    print(f"Session: {session_id}")
    print(f"Budget: ${args.budget:.2f}")
    print(f"Hooks: {total} handlers")
    for event, handlers in summary.items():
        for h in handlers:
            print(f"   {h} -> {event}")
    print()

    adapter = CrewObservabilityAdapter(registry, session_id=session_id)
    adapter.install_global_hooks()
    atexit.register(adapter.cleanup)

    model_name = os.environ.get("AGENT_MODEL", "qwen-plus")
    base_url = os.environ.get(
        "OPENAI_API_BASE",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    llm = LLM(model=model_name, base_url=base_url)

    tool = LoopingTool() if args.loop else KnowledgeSearchTool()
    agent = Agent(
        role="Research Analyst",
        goal="搜索并总结关于 AI Agent 可靠性的信息",
        backstory="你是一位研究分析师。使用工具搜索后总结要点。",
        llm=llm,
        verbose=True,
        tools=[tool],
        max_iter=15,
    )

    task = Task(
        description="搜索「AI Agent 可靠性」相关信息，列出 3 个关键要点。",
        expected_output="3 个关键要点，每点一句话。",
        agent=agent,
    )

    crew = Crew(
        agents=[agent],
        tasks=[task],
        verbose=True,
        step_callback=adapter.make_step_callback(),
        task_callback=adapter.make_task_callback(),
    )

    print(f"Starting crew...\n")
    try:
        result = crew.kickoff()
        print(f"\n{'='*60}")
        print(f"Result:\n{result}")
    except GuardrailDeny as e:
        print(f"\n{'='*60}")
        print(f"Guardrail triggered: {e}")

    adapter.cleanup()

    print(f"\n{'='*60}")
    print("Guardrail Metrics:")
    for name, strategy in strategies.items():
        metrics = strategy.get_metrics()
        print(f"\n  [{name}]")
        for k, v in metrics.items():
            print(f"    {k}: {v}")


if __name__ == "__main__":
    main()
