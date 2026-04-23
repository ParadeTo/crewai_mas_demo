"""F10: 单 Agent Demo —— Hook 框架 + Langfuse 全链路追踪。

运行方式：
    python3 demo.py
    python3 demo.py "你自己选一个AI领域的热门话题"
"""

from __future__ import annotations

import atexit
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_M5L30_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_M5L30_DIR))

from crewai import Agent, Crew, LLM, Task
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from hook_framework import (
    CrewObservabilityAdapter,
    HookLoader,
    HookRegistry,
)


class KnowledgeSearchInput(BaseModel):
    query: str = Field(description="搜索关键词")


class KnowledgeSearchTool(BaseTool):
    name: str = "knowledge_search"
    description: str = "搜索知识库，返回关于指定主题的信息"
    args_schema: type[BaseModel] = KnowledgeSearchInput

    def _run(self, query: str) -> str:
        knowledge = {
            "可观测性": "可观测性（Observability）源自控制论，核心三支柱：Metrics、Logging、Tracing。在 AI Agent 领域，可观测性指对 Agent 推理链路、工具调用、LLM 交互的全链路追踪能力。",
            "AI Agent": "AI Agent 是能自主感知环境、做出决策并采取行动的智能系统。当前主流架构包括 ReAct（推理+行动）、Plan-and-Execute、Multi-Agent 协作等。",
            "Langfuse": "Langfuse 是开源的 LLM 可观测性平台，支持 Trace、Generation、Span 三级追踪。可自托管（Docker）或使用云服务，提供成本分析、质量评估、Prompt 管理等功能。",
            "Hook": "Hook（钩子）是一种事件驱动的拦截机制，允许在特定时间点注入自定义逻辑。在 AI Agent 框架中，Hook 可用于日志记录、追踪、安全拦截、成本控制等。",
            "CrewAI": "CrewAI 是多智能体协作框架，提供 Agent、Task、Crew 三层抽象。支持全局 Hook（@before_llm_call 等）和实例级回调（step_callback、task_callback）。",
        }
        results = []
        for key, value in knowledge.items():
            if key.lower() in query.lower() or query.lower() in key.lower():
                results.append(f"[{key}] {value}")
        if not results:
            for key, value in list(knowledge.items())[:2]:
                results.append(f"[{key}] {value}")
        return "\n\n".join(results)


def main():
    topic = " ".join(sys.argv[1:]).strip() or "AI Agent 可观测性"
    session_id = f"sess_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

    # 1. 初始化 HookRegistry
    registry = HookRegistry()

    # 2. 两层加载 hooks
    loader = HookLoader(registry)
    loader.load_two_layers(
        global_dir=_M5L30_DIR / "shared_hooks",
        workspace_dir=_M5L30_DIR / "workspace" / "demo_agent",
    )

    summary = registry.summary()
    total = sum(len(v) for v in summary.values())
    print(f"🔗 Session: {session_id}")
    print(f"📦 HookRegistry: {total} handlers loaded")
    for event, handlers in summary.items():
        for h in handlers:
            print(f"   {h} → {event}")
    print()

    # 3. CrewAI 适配层
    adapter = CrewObservabilityAdapter(registry, session_id=session_id)
    adapter.install_global_hooks()
    atexit.register(adapter.cleanup)

    # 4. 构建 Crew
    model_name = os.environ.get("AGENT_MODEL", "qwen-plus")
    base_url = os.environ.get(
        "OPENAI_API_BASE",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    llm = LLM(model=model_name, base_url=base_url)

    agent = Agent(
        role="Research Analyst",
        goal=f"搜索并总结关于 {topic} 的最新信息",
        backstory="你是一位高效的研究分析师，擅长快速整理信息并给出简洁的摘要。你每次搜索后都会认真总结要点。",
        llm=llm,
        verbose=True,
        tools=[KnowledgeSearchTool()],
    )

    task = Task(
        description=f"使用 knowledge_search 工具搜索关于「{topic}」的信息，然后列出 3 个关键要点，每个要点用一句话总结。",
        expected_output="包含 3 个关键要点的简洁列表，每点一句话。",
        agent=agent,
    )

    crew = Crew(
        agents=[agent],
        tasks=[task],
        verbose=True,
        step_callback=adapter.make_step_callback(),
        task_callback=adapter.make_task_callback(),
    )

    # 5. 执行
    print(f"🚀 Starting crew for topic: {topic}\n")
    result = crew.kickoff()

    # 6. 清理
    adapter.cleanup()

    print(f"\n{'='*60}")
    print(f"📊 Result:\n{result}")
    print(f"\n🔗 Langfuse: http://localhost:3000")
    audit_file = _M5L30_DIR / "workspace" / "demo_agent" / "audit.log"
    if audit_file.exists():
        print(f"📝 Audit log: {audit_file}")


if __name__ == "__main__":
    main()
