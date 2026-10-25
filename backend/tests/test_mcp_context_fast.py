import json
import subprocess
import sys
import httpx
import pytest
from test_reading_memory import memory_book, tool


def test_mcp_import_does_not_load_database_or_index_services(mcp_server):
    code="import app.mcp_server,sys,json; print(json.dumps([n for n in sys.modules if n in ['app.database','app.services.model_tool_service','app.services.text_index_service']]))"
    result=subprocess.run([sys.executable,"-c",code],capture_output=True,text=True,check=True,timeout=15)
    assert json.loads(result.stdout.strip())==[]


def test_context_question_is_hint_not_slow_search(monkeypatch, mcp_server):
    monkeypatch.setattr(mcp_server,"_context",lambda book:{"ok":True,"data":{"book_id":3,"available":True,"chapter_idx":4,"safe_chapter":4,"char_offset":99}})
    def forbidden(*args,**kwargs):raise AssertionError("context should not query notes")
    monkeypatch.setattr(mcp_server,"_tool",forbidden)
    result=mcp_server.get_reading_context(question="甲某发生了什么")
    assert result["data"]["safe_chapter"]==5
    assert "char_offset" not in result["data"]
    assert result["data"]["next_action"]["tool"]=="note_query"


def test_context_short_timeout_and_structured_failure(monkeypatch, mcp_server):
    options=[]
    class Client:
        def __init__(self,**kwargs):options.append(kwargs)
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def request(self,*args,**kwargs):raise httpx.ReadTimeout("synthetic")
    monkeypatch.setattr(mcp_server.httpx,"Client",Client)
    result=mcp_server._request("GET","/api/reading-context")
    assert result["error"]=="backend_timeout"
    assert options[0]["timeout"].read==4
    assert options[0]["trust_env"] is False
    assert result["request_id"]


def test_explicit_book_does_not_repeat_context_request(monkeypatch, mcp_server):
    def forbidden(*args):raise AssertionError("redundant progress lookup")
    monkeypatch.setattr(mcp_server,"_context",forbidden)
    paths=[]
    def request(method,path,**kwargs):
        paths.append(path);return {"ok":True,"data":{"ok":True,"data":{}}}
    monkeypatch.setattr(mcp_server,"_request",request)
    assert mcp_server._tool(3,"list_storylines",{})["ok"]
    assert paths==["/api/books/3/agent/model-tool"]


def test_note_navigation_does_not_probe_vector_indexes(client,memory_book,monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError("unnecessary vector capability probe")
    monkeypatch.setattr("app.services.agent_capability_service.detect",forbidden)
    assert tool(client,memory_book,"note_query",{"query":"甲某"})["ok"]
    assert tool(client,memory_book,"get_toc",{})["ok"]
    assert tool(client,memory_book,"read_chapter",{"chapter_number":1})["ok"]
