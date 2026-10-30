"""Exercise the advertised MCP stdio transport, without credentials or renders."""
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def test_stdio_initialization_discovery_read_tools_and_submission_gate(tmp_path):
    root=Path(__file__).resolve().parents[1]
    database=tmp_path/'mcp.sqlite3'
    environment={
        'DATABASE_URL':f'sqlite:///{database.as_posix()}',
        'TENSOR_MASTER_CREDENTIAL_DIR':str(tmp_path/'credentials'),
        'OPENROUTER_SECRET_FILE':str(tmp_path/'no-openrouter-key'),
        'COMFYUI_ROOT':str(tmp_path/'no-comfyui'),
        'COMFYUI_URL':'http://127.0.0.1:1',
        'COMFYUI_INPUT_DIR':str(tmp_path/'input'),
        'MODEL_DIRECTORIES':'[]',
        'COMMANDER_MCP_ALLOW_SUBMIT':'0',
        'COMMANDER_MCP_TRANSPORT':'stdio',
    }
    async def check():
        parameters=StdioServerParameters(command=sys.executable,args=['-m','backend.app.mcp_server'],cwd=root,env=environment)
        with (tmp_path/'mcp-stderr.log').open('w',encoding='utf-8') as errors:
            async with stdio_client(parameters,errlog=errors) as streams:
                async with ClientSession(*streams,read_timeout_seconds=30) as session:
                    initialized=await session.initialize()
                    assert initialized.server_info.name=='Comfy Commander'
                    tools=await session.list_tools()
                    names={tool.name for tool in tools.tools}
                    assert names=={'commander_status','list_profiles','analyze_workflow_json','prepare_character_replacement','get_job_status','submit_workflow'}
                    result=await session.call_tool('commander_status',{})
                    assert not result.is_error
                    status=result.structured_content
                    assert status['submission_enabled'] is False and status['rentals_via_mcp'] is False
                    assert status['queue']['queued']==0
                    profiles=await session.call_tool('list_profiles',{})
                    assert not profiles.is_error and 'ref2v-int8' in profiles.structured_content['profiles']
                    resource=await session.read_resource('commander://profiles')
                    assert 'qwen-image-21' in json.loads(resource.contents[0].text)['profiles']
                    rejected=await session.call_tool('submit_workflow',{'name':'Blocked transport test','workflow':{'1':{'class_type':'SaveImage','inputs':{}}},'confirmation':'QUEUE THIS WORKFLOW'})
                    assert rejected.is_error  # no job can be queued without local opt-in
    asyncio.run(asyncio.wait_for(check(),60))
    with sqlite3.connect(database) as connection:
        assert connection.execute('select count(*) from jobs').fetchone()[0]==0
    assert not (tmp_path/'credentials/api_token').exists()
