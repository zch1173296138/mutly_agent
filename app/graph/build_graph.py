import logging

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.graph.nodes.controller import controller_node
from app.graph.nodes.planner import planner_node
from app.graph.nodes.reviewer import reviewer_node
from app.graph.nodes.simple_chat import simple_chat_node
from app.graph.nodes.worker import worker_node
from app.graph.state import AgentState

logger = logging.getLogger(__name__)


def router_after_controller(state: AgentState) -> str:
    action = state.get("next_action")
    if action == "complex_research":
        return "planner"
    if action == "resume_research":
        return "resumer"
    return "simple_chat"


def resumer_node(state: AgentState) -> dict:
    tasks = state.get("tasks") or {}

    running_tasks = [task_id for task_id, task in tasks.items() if getattr(task, "status", "") == "running"]
    pending_tasks = [task_id for task_id, task in tasks.items() if getattr(task, "status", "") == "pending"]
    completed = [task_id for task_id, task in tasks.items() if getattr(task, "status", "") == "completed"]

    logger.info(
        "[Resumer] resume interrupted tasks: %s running -> pending, %s pending, %s completed",
        len(running_tasks),
        len(pending_tasks),
        len(completed),
    )

    for task_id in running_tasks:
        tasks[task_id].status = "pending"

    ready_tasks = []
    for task_id in running_tasks + pending_tasks:
        task = tasks.get(task_id)
        if not task:
            continue
        dependencies = getattr(task, "dependencies", None) or []
        if all(dep in completed for dep in dependencies):
            ready_tasks.append(task_id)

    return {
        "tasks": tasks,
        "ready_tasks": ready_tasks,
    }


def distribute_tasks(state: AgentState):
    tasks = state.get("tasks") or {}
    ready_tasks = state.get("ready_tasks") or []

    if not tasks:
        if state.get("next_action") == "complex_research" or state.get("planner_failure"):
            logger.error("[Distributor] complex_research has empty tasks; routing to reviewer as planner_failure")
            return "reviewer"
        logger.warning("[Distributor] tasks empty; ending execution")
        return END

    completed = sum(1 for task in tasks.values() if task.status == "completed")
    failed = sum(1 for task in tasks.values() if task.status == "failed")
    suspended = sum(1 for task in tasks.values() if task.status == "suspended")
    running = sum(1 for task in tasks.values() if task.status == "running")
    total = len(tasks)

    if suspended > 0 and running == 0:
        logger.info("[Distributor] detected %s suspended task(s); waiting for user input", suspended)
        return END

    if failed > 0:
        logger.error("[Distributor] detected %s failed task(s); routing to reviewer", failed)
        return "reviewer"

    if completed >= total:
        logger.info("[Distributor] all %s task(s) completed; routing to reviewer", total)
        return "reviewer"

    pending_ready = [
        task_id
        for task_id in ready_tasks
        if tasks.get(task_id) and tasks[task_id].status == "pending"
    ]

    if not pending_ready:
        if running > 0:
            logger.info("[Distributor] no new ready tasks; waiting for %s running task(s)", running)
            return []
        logger.warning("[Distributor] no ready tasks and no running tasks; ending execution")
        return END

    logger.info("[Distributor] dispatching %s ready task(s): %s", len(pending_ready), pending_ready)
    return [
        Send("worker", {"current_task_id": task_id, "tasks": tasks})
        for task_id in pending_ready
    ]


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("controller", controller_node)
    graph.add_node("simple_chat", simple_chat_node)
    graph.add_node("planner", planner_node)
    graph.add_node("worker", worker_node)
    graph.add_node("reviewer", reviewer_node)
    graph.add_node("resumer", resumer_node)
    graph.set_entry_point("controller")

    graph.add_conditional_edges(
        "controller",
        router_after_controller,
        {
            "simple_chat": "simple_chat",
            "planner": "planner",
            "resumer": "resumer",
        },
    )

    graph.add_conditional_edges(
        "resumer",
        distribute_tasks,
        ["worker", "reviewer", END],
    )

    graph.add_conditional_edges(
        "planner",
        distribute_tasks,
        ["worker", "reviewer", END],
    )

    graph.add_conditional_edges(
        "worker",
        distribute_tasks,
        ["worker", "reviewer", END],
    )

    graph.add_edge("reviewer", END)
    graph.add_edge("simple_chat", END)

    return graph
