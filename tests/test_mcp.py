import os
from pathlib import Path

import pytest

from app.infrastructure.setup import MCPRegistry
from app.infrastructure.setup import tool_registry


def test_local_python_mcp_config_uses_project_root_import_path():
    root = Path(__file__).resolve().parents[1]
    registry = MCPRegistry()

    client = registry._build_client_from_config(
        "local-rag",
        {"type": "python", "script_or_package": "app/infrastructure/rag_server.py"},
    )

    assert client.cwd == str(root)
    assert client.env["PYTHONPATH"].split(os.pathsep)[0] == str(root)
    assert Path(client.args[1]) == root / "app" / "infrastructure" / "rag_server.py"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mcp_registry_integration(integration_enabled):
    if not integration_enabled:
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to run external MCP registry test")

    try:
        await tool_registry.initialize()
        tools = await tool_registry.get_all_tools()
        assert isinstance(tools, list)
    finally:
        await tool_registry.cleanup()
