import argparse
import os
import sys
import time
from pathlib import Path

from src.config import load_config
from src.llm_client import LLMClient, LLMError, MockLLMClient
from src.pdf_parser import pdf_to_markdown
from src.export_docx import export
from src.validator import generate_with_validation
from src.utils import ensure_dir, read_text, setup_logging

logger = setup_logging()


def check_llm_env(config: dict) -> None:
    llm_config = config.get("llm", {})
    api_key_env = llm_config.get("api_key_env", "LLM_API_KEY")
    base_url = llm_config.get("base_url", "")
    if not os.environ.get(api_key_env):
        if "localhost" in base_url or "127.0.0.1" in base_url:
            logger.info(f"未设置环境变量 {api_key_env}，本地服务将使用占位 key")
        else:
            logger.warning(f"环境变量 {api_key_env} 未设置，使用云服务请先 export {api_key_env}=你的Key")


def run_stage1(args, config: dict) -> list[Path]:
    start = time.perf_counter()
    written = pdf_to_markdown(args.pdf, args.out, config)
    elapsed = time.perf_counter() - start
    logger.info(f"stage1 完成：生成 {len(written)} 个 Markdown 文件，耗时 {elapsed:.1f}s")
    return written


def run_stage2(args, config: dict) -> Path:
    """stage2 一键流程：解析模板 → 逐块生成 → AI 验证回环 → 导出 docx。"""
    start = time.perf_counter()
    if getattr(args, "mock", False):
        llm = MockLLMClient(config.get("llm", {}))
        logger.info("已启用离线演示模式（MockLLMClient），不访问网络")
    else:
        check_llm_env(config)
        llm = LLMClient(config["llm"])
    if getattr(args, "skip_validation", False):
        config["stage2"]["max_validation_rounds"] = 0
    knowledge_md = read_text(args.knowledge)
    ensure_dir(args.out)
    md_path, reports = generate_with_validation(
        knowledge_md, args.template, args.out, llm, config
    )
    docx_path = export(md_path, args.template, Path(args.out) / "教案.docx", config)
    elapsed = time.perf_counter() - start
    final_state = "PASS" if reports[-1]["passed"] else "FAIL"
    logger.info(
        f"stage2 完成：验证 {len(reports)} 轮（最终 {final_state}），耗时 {elapsed:.1f}s"
    )
    return docx_path


def run_pipeline(args, config: dict) -> Path:
    """stage1 + stage2 串联：教材 PDF → 章节 md → 教案 docx。"""
    md_files = run_stage1(args, config)
    if not md_files:
        raise RuntimeError("stage1 未产出任何 Markdown 文件")
    target = None
    if args.chapter:
        for path in md_files:
            if args.chapter in path.name:
                target = path
                break
        if target is None:
            raise RuntimeError(f"未找到文件名含 {args.chapter} 的章节 Markdown")
    else:
        target = md_files[0]
    logger.info(f"pipeline：选用 {target.name} 进入 stage2")
    args.knowledge = str(target)
    return run_stage2(args, config)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="course-transform",
        description="CourseTransform：教材 PDF → Markdown → 教案 docx",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p1 = subparsers.add_parser("stage1", help="教材 PDF 转 Markdown（每章一个文件）")
    p1.add_argument("--pdf", required=True, help="教材 PDF 路径")
    p1.add_argument("--out", required=True, help="输出目录")
    p1.add_argument("--config", default=None, help="配置文件路径")

    p2 = subparsers.add_parser("stage2", help="教材 Markdown + 模板 docx → 教案 docx")
    p2.add_argument("--knowledge", required=True, help="教材章节 Markdown 路径")
    p2.add_argument("--template", required=True, help="教学设计模板 docx 路径")
    p2.add_argument("--out", required=True, help="输出目录")
    p2.add_argument("--config", default=None, help="配置文件路径")
    p2.add_argument("--skip-validation", action="store_true", help="跳过 AI 验证回环")
    p2.add_argument("--mock", action="store_true", help="离线演示模式：使用确定性假模型，不访问网络")

    p3 = subparsers.add_parser("pipeline", help="stage1 + stage2 一键串联")
    p3.add_argument("--pdf", required=True, help="教材 PDF 路径")
    p3.add_argument("--template", required=True, help="教学设计模板 docx 路径")
    p3.add_argument("--out", required=True, help="输出目录")
    p3.add_argument("--config", default=None, help="配置文件路径")
    p3.add_argument("--chapter", default=None, help="章节文件名关键字（默认取第一份）")
    p3.add_argument("--skip-validation", action="store_true", help="跳过 AI 验证回环")
    p3.add_argument("--mock", action="store_true", help="离线演示模式：使用确定性假模型")

    args = parser.parse_args(argv)
    config = load_config(args.config)
    try:
        if args.command == "stage1":
            run_stage1(args, config)
        elif args.command == "stage2":
            run_stage2(args, config)
        elif args.command == "pipeline":
            run_pipeline(args, config)
    except LLMError as exc:
        logger.error(f"模型调用失败：{exc}")
        logger.error("请检查 config.yaml 中 llm.base_url/model 配置与 API Key 环境变量")
        sys.exit(1)


if __name__ == "__main__":
    main()
