import asyncio

import pytest

from backend.app import mcp_server


def test_mcp_exposes_narrow_tool_surface():
    names = {tool.name for tool in asyncio.run(mcp_server.server.list_tools())}
    assert names == {
        "commander_status", "list_profiles", "analyze_workflow_json",
        "prepare_character_replacement", "get_job_status", "submit_workflow",
    }
    assert not any("rent" in name or "credential" in name or "install" in name for name in names)


def test_mcp_analyzes_but_does_not_execute_workflow():
    result = mcp_server.analyze_workflow_json({
        "1": {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": "mine.safetensors"}}
    })
    assert result["node_types"] == ["LoraLoaderModelOnly"]
    assert result["profile_draft"]["models"][0]["source"] == "unresolved"


def test_mcp_submission_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("COMMANDER_MCP_ALLOW_SUBMIT", raising=False)
    with pytest.raises(PermissionError, match="disabled"):
        mcp_server.submit_workflow("demo", {"1": {"class_type": "SaveImage", "inputs": {}}},
                                   mcp_server.CONFIRMATION)


def test_mcp_rejects_oversized_workflow():
    workflow = {str(index): {"class_type": "Node", "inputs": {}} for index in range(5001)}
    with pytest.raises(ValueError, match="5,000"):
        mcp_server.analyze_workflow_json(workflow)
