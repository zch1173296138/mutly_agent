import asyncio
import logging

from langchain_core.runnables import RunnableConfig

from app.graph.state import AgentState
from app.llm.prompt_manager import render
from app.llm.wrapper import call_llm_stream

logger = logging.getLogger(__name__)


def _build_failure_summary(tasks: dict) -> str:
    info_failures: list[str] = []
    other_failures: list[str] = []
    completed_results: list[tuple[str, str]] = []

    for task_id, task in tasks.items():
        if task.status == "failed":
            error = task.error or "unknown error"
            display_error = error.replace("[需补充信息] ", "", 1)
            entry = f"- **{task_id}** ({task.description})\n  - {display_error}"
            if "[需补充信息]" in error:
                info_failures.append(entry)
            else:
                other_failures.append(entry)
        elif task.status == "completed" and task.result:
            completed_results.append((task.description, task.result))

    lines = ["## 任务执行未完成\n"]

    if info_failures:
        lines.append(f"以下 {len(info_failures)} 个子任务需要补充信息才能继续：\n")
        lines.extend(info_failures)
        lines.append("\n请在下一条消息中提供上述缺失信息，我会继续执行。")

    if other_failures:
        if info_failures:
            lines.append("")
        lines.append(f"以下 {len(other_failures)} 个子任务因配置或执行问题无法完成：\n")
        lines.extend(other_failures)
        lines.append("\n请检查相关配置或工具状态后重试。")

    if completed_results:
        lines.append("\n---\n\n## 已完成任务的分析结果\n")
        for description, result in completed_results:
            lines.append(f"### {description}\n\n{result}\n")

    return "\n".join(lines)


async def _stream_text(queue, content: str) -> None:
    if not queue:
        return
    chunk_size = 80
    for index in range(0, len(content), chunk_size):
        queue.put_nowait({"type": "content_token", "delta": content[index : index + chunk_size]})
        await asyncio.sleep(0)


async def reviewer_node(state: AgentState, config: RunnableConfig) -> dict:
    queue = config.get("configurable", {}).get("stream_queue")
    tasks: dict = state.get("tasks") or {}

    if state.get("planner_failure"):
        error = state.get("planner_error") or "planner_failure"
        content = f"## Planner failure\n\n{error}"
        await _stream_text(queue, content)
        return {
            "final_report": content,
            "messages": [{"role": "assistant", "content": content}],
            "planner_error": error,
            "planner_failure": True,
        }

    failed_tasks = {task_id: task for task_id, task in tasks.items() if task.status == "failed"}
    if failed_tasks:
        logger.warning("[Reviewer] detected %s failed task(s)", len(failed_tasks))
        content = _build_failure_summary(tasks)
        await _stream_text(queue, content)
        return {
            "final_report": content,
            "messages": [{"role": "assistant", "content": content}],
        }

    task_results = state.get("task_results") or {}
    if task_results:
        results = [value for value in task_results.values() if value]
    else:
        results = [task.result for task in tasks.values() if task.result]

    logger.info("[Reviewer] summarizing %s task result(s)", len(results))

    if not results:
        return {"final_report": "", "messages": []}

    if len(results) == 1:
        content = results[0]
        await _stream_text(queue, content)
    else:
        user_input = state.get("user_input") or ""
        combined = "\n\n---\n\n".join(results)
        user_message = (
            f"Original user request:\n{user_input}\n\n"
            f"Subtask results:\n{combined}"
        )
        full_content = ""
        async for chunk in call_llm_stream(
            messages=[{"role": "user", "content": user_message}],
            system=render("reviewer"),
            temperature=0.3,
            role="reviewer",
        ):
            if chunk.get("done"):
                break
            content_delta = chunk.get("content", "")
            if content_delta:
                full_content += content_delta
                if queue:
                    queue.put_nowait({"type": "content_token", "delta": content_delta})
        content = full_content or combined

    return {
        "final_report": content,
        "messages": [{"role": "assistant", "content": content}],
    }
