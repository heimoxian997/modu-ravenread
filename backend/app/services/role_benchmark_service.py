"""Synthetic A/B/C comparison. Reports answers for review, never claims quality scores."""

from __future__ import annotations

import json

from app.schemas.role_preparation import RoleScene, RoleTarget
from app.services.role_preparation_prompt import CHAT_SYSTEM, PROMPT_VERSION
from app.services.role_preparation_service import (
    ModelRunner, PreparationError, actor_context, make_batches, prepare_role, select_scenes,
)


def corpus_scenes(corpus):
    if corpus.get("synthetic") is not True:
        raise ValueError("the stage-one benchmark accepts synthetic fixtures only")
    scenes = []
    for scene in corpus["scenes"]:
        payload = {key: value for key, value in scene.items() if key != "review_note"}
        scenes.append(RoleScene.model_validate({"book_key": corpus["book_key"], **payload}))
    return scenes


def comparison_plan(corpus, character=None, max_batch_chars=8000):
    scenes = corpus_scenes(corpus)
    targets = [t for t in corpus["targets"] if character is None or t["name"] == character]
    if not targets:
        raise ValueError("unknown synthetic character")
    result = []
    for entry in targets:
        target = RoleTarget(book_key=corpus["book_key"], name=entry["name"], aliases=entry["aliases"])
        arms = {"A": {"preparation": "handwritten_legacy_card_baseline", "minimum_prepare_calls": 0}}
        for arm, mode in (("B", "targeted"), ("C", "full")):
            picked, coverage = select_scenes(target, scenes, mode)
            batches = make_batches(picked, max_batch_chars)
            arms[arm] = {"preparation": mode, "coverage": coverage,
                         "batch_count": len(batches),
                         "minimum_prepare_calls": len(batches) + bool(batches)}
        result.append({"character": target.name, "arms": arms, "case_count": len(entry["cases"])})
    return {"fixture_version": corpus["fixture_version"], "synthetic": True,
            "prompt_version": PROMPT_VERSION, "network_requested": False,
            "quality_evaluated": False, "targets": result}


def run_comparison(corpus, model: ModelRunner, character=None, max_batch_chars=8000):
    plan = comparison_plan(corpus, character, max_batch_chars)
    scenes = corpus_scenes(corpus)
    report = {**plan, "network_requested": True, "quality_evaluated": False,
              "review_status": "manual_review_required", "results": []}
    for entry in corpus["targets"]:
        if character is not None and character != entry["name"]:
            continue
        target = RoleTarget(book_key=corpus["book_key"], name=entry["name"], aliases=entry["aliases"])
        # Deliberately independent arms: C must not benefit from B's warm extraction cache.
        for arm, mode in (("A", None), ("B", "targeted"), ("C", "full")):
            start = len(model.metrics)
            item = {"character": target.name, "arm": arm, "answers": []}
            try:
                if mode is None:
                    context = {"character": target.name, "legacy_card": entry["legacy_card"],
                               "limitations": ["压缩资料基线，可能缺少状态变化和上下文。"]}
                else:
                    prepared = prepare_role(target, scenes, model, mode, max_batch_chars)
                    item["preparation"] = prepared.as_dict()
                    context = actor_context(prepared)
                for case in entry["cases"]:
                    # Only the question is sent. Rubrics and held-out scene text stay local.
                    answer = model.call([
                        {"role": "system", "content": CHAT_SYSTEM},
                        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                        {"role": "user", "content": case["question"]},
                    ], "benchmark_chat")
                    item["answers"].append({"case_id": case["case_id"],
                                            "question": case["question"], "answer": answer,
                                            "review_rubric": case["rubric"]})
            except PreparationError as exc:
                item["error"] = exc.code
            item["metrics"] = model.metrics[start:]
            report["results"].append(item)
    report["metrics"] = model.metrics
    report["has_errors"] = any("error" in item for item in report["results"])
    report["heldout_review_scenes"] = [s for s in corpus["scenes"] if s.get("split") == "holdout"]
    return report
