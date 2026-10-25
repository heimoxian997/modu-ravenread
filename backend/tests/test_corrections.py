"""Source corrections require review; all evidence and people are synthetic."""
import copy
import pytest
from test_reading_memory import memory_book, tool, position
from app.database import SessionLocal
from app.models.chapter_summary import ChapterSummary
from app.models.chapter_extraction import ChapterExtraction
from app.models.note import Issue
from app.schemas.correction import CorrectionDecision
from app.services import correction_service, entity_layer_service


def seed_summary(book):
    with SessionLocal() as db:
        row=ChapterSummary(book_id=book,chapter_idx=0,title="合成章",summary="甲某遗失纸伞",characters=["甲某"],key_events=[],cliffhanger="")
        db.add(row);db.commit();return row.id


def report_summary(client,book,replacement=None):
    source=tool(client,book,"get_chapter_summary",{"chapter_number":1})
    assert source["ok"],source
    args={"source_ref":source["data"]["source_ref"],"reason":"原文是交付，摘要误写为遗失",
        "evidence":[{"chapter_number":1,"quote":"甲某把纸伞交给乙某。"}]}
    if replacement is not None:args["replacement"]=replacement
    report=tool(client,book,"report_correction",args)
    assert report["ok"],report
    return report["data"]["issue_id"],args


def api(client,book,suffix,body=None):
    path=f"/api/books/{book}/corrections{suffix}"
    response=client.get(path) if body is None else client.post(path,json=body)
    assert response.status_code==200,response.text
    return response.json()["data"]


def test_report_does_not_modify_and_user_can_edit_before_accepting(client,memory_book):
    row_id=seed_summary(memory_book)
    issue_id,args=report_summary(client,memory_book,{"summary":"甲某交伞"})
    with SessionLocal() as db:
        assert db.get(ChapterSummary,row_id).summary=="甲某遗失纸伞"
    listed=api(client,memory_book,"")["data"]["items"]
    assert listed[0]["before"]["summary"]=="甲某遗失纸伞"
    result=api(client,memory_book,f"/{issue_id}/approve",{"replacement":{"summary":"甲某把纸伞交给乙某"},"rebuild":False})
    assert result["ok"]
    assert result["data"]["index_state"]=="pending"
    with SessionLocal() as db:
        assert db.get(ChapterSummary,row_id).summary=="甲某把纸伞交给乙某"
    assert tool(client,memory_book,"search_summaries",{"query":"纸伞"})["error"]=="index_updating"
    exact=tool(client,memory_book,"get_chapter_summary",{"chapter_number":1})
    assert exact["ok"] and exact["data"]["summary"]=="甲某把纸伞交给乙某"
    assert not api(client,memory_book,f"/{issue_id}/approve",{"rebuild":False})["ok"]


def test_source_hash_prevents_overwriting_other_window(client,memory_book):
    row_id=seed_summary(memory_book)
    issue_id,_=report_summary(client,memory_book,{"summary":"甲某交伞"})
    with SessionLocal() as db:
        db.get(ChapterSummary,row_id).summary="用户已经核对并改过";db.commit()
    result=api(client,memory_book,f"/{issue_id}/approve",{"rebuild":False})
    assert result["error"]=="source_changed"
    with SessionLocal() as db:
        assert db.get(ChapterSummary,row_id).summary=="用户已经核对并改过"


def test_rejection_duplicate_and_invalid_patch(client,memory_book):
    seed_summary(memory_book)
    issue_id,args=report_summary(client,memory_book)
    repeated=tool(client,memory_book,"report_correction",args)
    assert repeated["deduplicated"]
    assert repeated["data"]["issue_id"]==issue_id
    invalid={**args,"replacement":{"unknown":"value"}}
    assert tool(client,memory_book,"report_correction",invalid)["error"]=="invalid_patch"
    assert api(client,memory_book,f"/{issue_id}/reject",{})["ok"]
    with SessionLocal() as db:
        assert not correction_service.blocked_layers(db,memory_book)


def test_later_evidence_cannot_backdate_and_reports_hide_after_rewind(client,memory_book):
    seed_summary(memory_book)
    position(memory_book,4)
    issue_id,args=report_summary(client,memory_book)
    args["evidence"]=[{"chapter_number":4,"quote":"丙某揭开密信，甲某得知真相。"}]
    assert tool(client,memory_book,"report_correction",args)["error"]=="later_knowledge"
    position(memory_book,1)
    assert api(client,memory_book,"")["data"]["items"]==[]
    assert api(client,memory_book,f"/{issue_id}/approve",{"rebuild":False})["error"]=="not_found"


def test_rebuild_failure_retry_does_not_reapply_source(client,memory_book,monkeypatch):
    row_id=seed_summary(memory_book)
    issue_id,_=report_summary(client,memory_book,{"summary":"甲某交伞"})
    assert api(client,memory_book,f"/{issue_id}/approve",{"rebuild":False})["ok"]
    def fail(*args,**kwargs):raise RuntimeError("synthetic index failure")
    monkeypatch.setattr("app.services.summary_index_service.build_summary_index",fail)
    correction_service.rebuild_issue(memory_book,issue_id)
    assert api(client,memory_book,"")["data"]["items"][0]["index_state"]=="failed"
    with SessionLocal() as db:
        assert db.get(ChapterSummary,row_id).summary=="甲某交伞"
        assert correction_service.blocked_layers(db,memory_book)=={"summary"}
    calls=[]
    monkeypatch.setattr("app.services.summary_index_service.build_summary_index",lambda *a,**kw:calls.append(kw))
    correction_service.rebuild_issue(memory_book,issue_id)
    assert len(calls)==1 and calls[0]["allow_partial"]
    assert api(client,memory_book,"")["data"]["items"][0]["index_state"]=="done"
    with SessionLocal() as db:
        assert not correction_service.blocked_layers(db,memory_book)


def test_character_fix_updates_extraction_before_rebuilding_entity_layer(client,memory_book,monkeypatch):
    parsed={"summary":{"summary":"交伞","characters":["甲某"],"key_events":[],"cliffhanger":""},
        "characters":{"mentions":[{"name":"甲某","aliases":[],"role":""}],"new_aliases":[],
            "card_deltas":[{"character":"甲某","identity":"持信人","traits":[],"relations":[],"known_facts":[],"voice_sample":"","arc":"","reason":""}]},"lore":[]}
    with SessionLocal() as db:
        extraction=ChapterExtraction(book_id=memory_book,chapter_idx=0,title="合成章",status="success",parsed=parsed)
        db.add(extraction);db.commit()
        entity_layer_service.build_entity_layer(db,memory_book)
    timeline=tool(client,memory_book,"get_character_timeline",{"name":"甲某"})
    assert timeline["ok"],timeline
    ref=timeline["data"]["items"][0]["source_ref"]
    report=tool(client,memory_book,"report_correction",{"source_ref":ref,"reason":"身份抽取有误",
        "evidence":[{"chapter_number":1,"quote":"甲某把纸伞交给乙某。"}],"replacement":{"identity":"持伞人"}})
    assert report["ok"],report
    issue_id=report["data"]["issue_id"]
    assert api(client,memory_book,f"/{issue_id}/approve",{"rebuild":False})["ok"]
    with SessionLocal() as db:
        source=db.query(ChapterExtraction).filter(ChapterExtraction.book_id==memory_book).one()
        assert source.parsed["characters"]["card_deltas"][0]["identity"]=="持伞人"
    assert tool(client,memory_book,"get_character",{"name":"甲某"})["error"]=="index_updating"
    monkeypatch.setattr("app.services.entity_index_service.build_entity_indexes",lambda *a,**kw:{})
    correction_service.rebuild_issue(memory_book,issue_id)
    after=tool(client,memory_book,"get_character",{"name":"甲某"})
    assert after["ok"],after
    assert "持伞人" in json_string(after)


def json_string(value):
    import json
    return json.dumps(value,ensure_ascii=False)
