"""Exercise crash recovery in child processes, never touch the real data directory."""
import subprocess
import sys
from pathlib import Path
import pytest
from resource_manager import TestResources


def test_slots_do_not_reuse_active_run_and_are_bounded(tmp_path):
    first=TestResources(tmp_path/"runs",slots=2)
    second=TestResources(tmp_path/"runs",slots=2)
    assert first.path!=second.path
    try:
        with pytest.raises(RuntimeError):TestResources(tmp_path/"runs",slots=2)
    finally:
        second.close();first.close()
    assert list((tmp_path/"runs").glob("slot-*.lock"))
    assert not any(p.is_dir() for p in (tmp_path/"runs").iterdir())


def test_hard_exit_reclaims_same_slot_and_does_not_accumulate(tmp_path):
    root=tmp_path/"runs"
    code="""import os,sys
from pathlib import Path
from resource_manager import TestResources
r=TestResources(Path(sys.argv[1]),slots=1)
(r.path/'orphan.txt').write_text('synthetic')
os._exit(0)
"""
    subprocess.run([sys.executable,"-c",code,str(root)],cwd=Path(__file__).parent,check=True,timeout=15)
    assert (root/"slot-0"/"orphan.txt").exists()
    resource=TestResources(root,slots=1)
    try:
        assert not (resource.path/"orphan.txt").exists()
    finally:
        resource.close()
    for _ in range(3):
        r=TestResources(root,slots=1);r.close()
    assert len(list(root.iterdir()))==1
    assert (root/"slot-0.lock").stat().st_size==1


def test_cleanup_failure_stops_allocating_new_slots(tmp_path,monkeypatch):
    root=tmp_path/"runs";root.mkdir();(root/"slot-0").mkdir()
    def denied(path):raise PermissionError("synthetic denial")
    monkeypatch.setattr("resource_manager.shutil.rmtree",denied)
    with pytest.raises(RuntimeError,match="停止创建"):
        TestResources(root)
    assert not (root/"slot-1").exists()


def test_default_database_is_memory_without_disk_journals():
    from app.database import engine
    from app.config import DATA_DIR
    assert "mode=memory" in str(engine.url)
    assert not (DATA_DIR/"app.db").exists()
    assert not list(DATA_DIR.glob("*journal*"))
