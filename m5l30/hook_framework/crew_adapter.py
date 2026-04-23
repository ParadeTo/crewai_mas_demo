"""F5: CrewAI 机制 → HookRegistry 事件映射。

映射关系：
┌──────────────────────────┬───────────────────────────┐
│ @before_llm_call         │ BEFORE_TURN（首次）       │
│                          │ BEFORE_LLM（每次）        │
│ @before_tool_call        │ BEFORE_TOOL_CALL          │
│ @after_tool_call         │ AFTER_TOOL_CALL           │
│ step_callback            │ AFTER_TURN                │
│ task_callback            │ TASK_COMPLETE             │
└──────────────────────────┴───────────────────────────┘
"""

from typing import Callable

from crewai.hooks import (
    after_tool_call,
    before_llm_call,
    before_tool_call,
    clear_after_tool_call_hooks,
    clear_before_llm_call_hooks,
    clear_before_tool_call_hooks,
)

from .registry import EventType, HookContext, HookRegistry


class CrewObservabilityAdapter:
    def __init__(self, registry: HookRegistry, session_id: str = ""):
        self._registry = registry
        self._session_id = session_id
        self._turn_count = 0
        self._current_turn_has_llm = False
        self._cleaned = False

    def install_global_hooks(self):
        registry = self._registry
        sid = self._session_id

        @before_llm_call
        def _before_llm(context):
            agent_id = getattr(getattr(context, "agent", None), "role", "")
            if not self._current_turn_has_llm:
                self._turn_count += 1
                self._current_turn_has_llm = True
                registry.dispatch(
                    EventType.BEFORE_TURN,
                    HookContext(
                        event_type=EventType.BEFORE_TURN,
                        agent_id=agent_id,
                        session_id=sid,
                        turn_number=self._turn_count,
                    ),
                )
            registry.dispatch(
                EventType.BEFORE_LLM,
                HookContext(
                    event_type=EventType.BEFORE_LLM,
                    agent_id=agent_id,
                    session_id=sid,
                    turn_number=self._turn_count,
                ),
            )
            return None

        @before_tool_call
        def _before_tool(context):
            registry.dispatch(
                EventType.BEFORE_TOOL_CALL,
                HookContext(
                    event_type=EventType.BEFORE_TOOL_CALL,
                    tool_name=context.tool_name,
                    tool_input=dict(context.tool_input),
                    session_id=sid,
                    turn_number=self._turn_count,
                ),
            )
            return None

        @after_tool_call
        def _after_tool(context):
            registry.dispatch(
                EventType.AFTER_TOOL_CALL,
                HookContext(
                    event_type=EventType.AFTER_TOOL_CALL,
                    tool_name=context.tool_name,
                    session_id=sid,
                    turn_number=self._turn_count,
                ),
            )

    def make_step_callback(self) -> Callable:
        registry = self._registry
        sid = self._session_id

        def callback(step):
            from crewai.agents.parser import AgentAction

            registry.dispatch(
                EventType.AFTER_TURN,
                HookContext(
                    event_type=EventType.AFTER_TURN,
                    session_id=sid,
                    turn_number=self._turn_count,
                    tool_name=step.tool if isinstance(step, AgentAction) else "",
                    metadata={"output": str(getattr(step, "output", ""))[:500]},
                ),
            )
            self._current_turn_has_llm = False

        return callback

    def make_task_callback(self) -> Callable:
        registry = self._registry
        sid = self._session_id

        def callback(task_output):
            registry.dispatch(
                EventType.TASK_COMPLETE,
                HookContext(
                    event_type=EventType.TASK_COMPLETE,
                    session_id=sid,
                    metadata={"raw_output": str(task_output)[:500]},
                ),
            )

        return callback

    def cleanup(self):
        if self._cleaned:
            return
        self._cleaned = True
        self._registry.dispatch(
            EventType.SESSION_END,
            HookContext(
                event_type=EventType.SESSION_END,
                session_id=self._session_id,
            ),
        )
        clear_before_llm_call_hooks()
        clear_before_tool_call_hooks()
        clear_after_tool_call_hooks()
