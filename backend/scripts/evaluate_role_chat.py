"""Run the bundled synthetic role comparison; no library or saved settings are opened."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.role_benchmark_service import comparison_plan, run_comparison
from app.services.role_preparation_service import ModelRunner, PreparationError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-model", action="store_true", help="明确启用模型请求；默认仅预览")
    parser.add_argument("--character", help="仅运行某个合成人物")
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--api-key-env", default="RAVENREAD_EVAL_API_KEY",
                        help="读取密钥的环境变量名，不接受命令行明文密钥")
    parser.add_argument("--max-calls", type=int, default=64)
    parser.add_argument("--batch-chars", type=int, default=8000)
    parser.add_argument("--output", type=Path, help="新报告文件；不覆盖已有文件")
    args = parser.parse_args(argv)
    corpus_path = BACKEND / "tests" / "fixtures" / "role_chat_synthetic.json"
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    try:
        plan = comparison_plan(corpus, args.character, args.batch_chars)
    except (ValueError, PreparationError) as exc:
        parser.error(str(exc))
    if not args.run_model:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    if not args.base_url or not args.model or not args.output:
        parser.error("--run-model 需要 --base-url、--model 和 --output")
    if args.max_calls < 1:
        parser.error("--max-calls 必须大于零")
    if not args.output.parent.is_dir():
        parser.error("报告父目录不存在")
    # Reserve the path before paying for any requests; never overwrite a previous run.
    try:
        report_file = args.output.open("x", encoding="utf-8")
    except OSError:
        parser.error("无法创建报告文件；路径必须可写且文件不存在")
    from app.services import llm_service
    provider = {"base_url": args.base_url, "model": args.model,
                "api_key": os.environ.get(args.api_key_env), "disable_thinking": True}

    def complete(messages, **kwargs):
        return llm_service.chat_completion(provider, messages, **kwargs)

    runner = ModelRunner(complete, model_key=args.model, max_calls=args.max_calls)
    try:
        report = run_comparison(corpus, runner, args.character, args.batch_chars)
        json.dump(report, report_file, ensure_ascii=False, indent=2)
        report_file.write("\n")
    finally:
        report_file.close()
    print(json.dumps({"report": str(args.output), "model_calls": len(runner.metrics),
                      "has_errors": report["has_errors"], "quality_evaluated": False},
                     ensure_ascii=False))
    return 1 if report["has_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
