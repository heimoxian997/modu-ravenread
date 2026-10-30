"""Core companion imports without optional GPU/MCP packages. Author: heimoxian."""
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_core_import_without_torch_or_mcp():
    code = """
import sys
sys.modules['torch'] = None
sys.modules['mcp'] = None
from app.services import conversation_memory_service, companion_read_service, agent_service
from app.api import conversation_memory, companion_read
assert conversation_memory_service.MEMORY_CHARS > 0
print('optional-dependencies-absent: ok')
"""
    # Run from backend/ so `app` resolves whatever directory pytest was started in.
    result = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "optional-dependencies-absent: ok" in result.stdout
