from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .llm import LLMClient, LLMError, settings_from_env
from .pipeline import ChapterResult, LessonPipeline, merge_brief, resolve_template
from .template import describe_template, parse_template
from .textbook import Textbook, estimate_periods, parse_textbook
from .utils import Console, now_iso, read_text, safe_name, write_text


def _reconfigure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jiaoan",
        description="教案工坊：教材 md + 教案模板 -> 成品教案（DOCX/MD/JSON）。",
    )
    parser.add_argument("--version", action="version", version=f"jiaoan {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="生成教案")
    run.add_argument("--textbook", required=True, help="教材全本 Markdown 路径")
    run.add_argument("--template", help="教案模板 .docx/.md，或按章节存放模板的目录")
    run.add_argument("--images", help="教材配图目录（png/jpg/webp，文件名/教材引用用于匹配章节与小节）")
    run.add_argument("--no-image-filter", action="store_true", help="关闭教材图判别器，全部图片都尝试放进教案")
    run.add_argument("--keep-all-images", action="store_true", help="保留判别结果但不过滤（调试用）")
    run.add_argument("--brief", help="教学简报 JSON 路径")
    run.add_argument("--manifest", help="章节清单 JSON（可选；用于指定行号范围/章节 id）")
    run.add_argument("--out", default="out", help="输出目录（默认 out）")
    run.add_argument("--chapters", help="章节选择：all、ch01、序号 1、标题关键词，逗号分隔")
    run.add_argument("--chapter", action="append", default=[], help="同 --chapters，可重复")
    run.add_argument("--limit", type=int, help="只生成前 N 章")
    run.add_argument("--subject", help="覆盖学科")
    run.add_argument("--grade", help="覆盖年级")
    run.add_argument("--duration", type=int, help="覆盖单课时分钟数（默认 45）")
    run.add_argument("--periods", type=int, help="覆盖每章课时数")
    run.add_argument("--lesson-type", help="覆盖课型")
    run.add_argument("--offline", action="store_true", help="不调用大模型，生成规则骨架（用于联调/演示）")
    run.add_argument("--dry-run", action="store_true", help="只输出 Prompt 与教材上下文，不调用模型")
    run.add_argument("--review", dest="review", action="store_true", default=True, help="启用大模型教研复核（默认开）")
    run.add_argument("--no-review", dest="review", action="store_false", help="关闭大模型教研复核")
    run.add_argument("--strict-coverage", action="store_true", help="教材小节未覆盖时视为错误")
    run.add_argument("--model", help="模型名（默认读 JIAOAN_MODEL）")
    run.add_argument("--base-url", help="OpenAI 兼容接口 base URL（默认读 JIAOAN_BASE_URL）")
    run.add_argument("--max-repair", type=int, default=None, help="自动修复轮数（默认 2）")
    run.add_argument("--max-context", type=int, default=None, help="教材上下文字符预算（默认 24000）")
    run.add_argument("--no-cache", action="store_true", help="禁用响应缓存")
    run.add_argument("--verbose", action="store_true", help="输出调试细节")
    run.add_argument("--json", action="store_true", help="把汇总 manifest 以 JSON 打到 stdout")

    chapters = sub.add_parser("chapters", help="查看教材章节结构")
    chapters.add_argument("--textbook", required=True)
    chapters.add_argument("--manifest")
    chapters.add_argument("--json", action="store_true")

    inspect = sub.add_parser("inspect-template", help="检查模板字段识别结果")
    inspect.add_argument("--template", required=True)
    inspect.add_argument("--json", action="store_true")

    doctor = sub.add_parser("doctor", help="检查运行环境与配置")
    doctor.add_argument("--ping", action="store_true", help="实际调用一次模型接口")
    doctor.add_argument("--offline", action="store_true", help="只检查离线环境")

    web = sub.add_parser("web", help="启动本地教师上传页")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--out", default="web_jobs", help="任务与输出目录（默认 web_jobs）")
    web.add_argument("--open", action="store_true", help="启动后自动打开浏览器")

    return parser


def main(argv: list[str] | None = None) -> int:
    _reconfigure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return cmd_run(args)
    if args.command == "chapters":
        return cmd_chapters(args)
    if args.command == "inspect-template":
        return cmd_inspect_template(args)
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "web":
        return cmd_web(args)
    parser.print_help()
    return 2


def _load_json(path: str | None, label: str) -> dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"{label}不存在：{p}")
    try:
        data = json.loads(read_text(p))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{label}不是合法 JSON：{exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"{label}顶层必须是 JSON 对象。")
    return data


def _select_chapters(textbook: Textbook, args: argparse.Namespace) -> list[Any]:
    selectors: list[str] = []
    if args.chapters:
        selectors.extend([x.strip() for x in str(args.chapters).split(",") if x.strip()])
    for item in args.chapter or []:
        selectors.extend([x.strip() for x in str(item).split(",") if x.strip()])
    if selectors:
        out = []
        for sel in selectors:
            try:
                out.append(textbook.find_chapter(sel))
            except KeyError as exc:
                raise SystemExit(str(exc))
        seen = set()
        out = [c for c in out if not (c.id in seen or seen.add(c.id))]
    else:
        out = list(textbook.chapters)
    if args.limit is not None:
        out = out[: max(0, args.limit)]
    return out


def cmd_run(args: argparse.Namespace) -> int:
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    console = Console(out_root / "run.log", verbose=args.verbose, stream=(sys.stderr if args.json else sys.stdout))
    console.info(f"教案工坊 v{__version__} | {now_iso()}")
    brief = _load_json(args.brief, "教学简报")
    manifest = _load_json(args.manifest, "章节清单") if args.manifest else None

    try:
        textbook = parse_textbook(args.textbook, manifest=manifest)
    except Exception as exc:
        console.error(f"教材解析失败：{exc}")
        console.close()
        return 2
    console.info(f"教材：{textbook.title}（{len(textbook.chapters)} 章，{textbook.chars} 字）")
    chapters = _select_chapters(textbook, args)
    if not chapters:
        console.error("没有选中任何章节。")
        console.close()
        return 2

    overrides = {
        "subject": args.subject,
        "grade": args.grade,
        "duration_minutes": args.duration,
        "periods": args.periods,
        "lesson_type": args.lesson_type,
    }
    if args.periods:
        overrides["periods_explicit"] = True
    settings = settings_from_env(
        {
            "model": args.model,
            "base_url": args.base_url,
            "offline": args.offline,
            "max_repair": args.max_repair,
            "max_context_chars": args.max_context,
            "no_cache": args.no_cache,
        }
    )
    if not settings.api_key and not args.offline and not args.dry_run:
        console.error("未配置大模型 API Key。")
        console.info("设置方法（PowerShell）：$env:JIAOAN_API_KEY=\"sk-...\"；也可用 DEEPSEEK_API_KEY / OPENAI_API_KEY。")
        console.info("只想先跑通流程：加 --offline；只想看 Prompt：加 --dry-run。")
        console.close()
        return 2

    client = LLMClient(settings, console)
    pipeline = LessonPipeline(settings, console, client)
    results: list[ChapterResult] = []

    for i, chapter in enumerate(chapters, start=1):
        console.step(i, len(chapters), f"处理 {chapter.id}：{chapter.title}")
        chapter_brief = merge_brief(brief, chapter, overrides)
        try:
            template_path = resolve_template(chapter, args.template)
        except Exception as exc:
            result = ChapterResult(
                chapter_id=chapter.id,
                title=chapter.title,
                status="failed",
                error=f"模板选择失败：{exc}",
            )
            console.error(result.error)
            results.append(result)
            continue
        result = pipeline.run_chapter(
            chapter,
            chapter_brief,
            template_path,
            out_root,
            offline=args.offline,
            dry_run=args.dry_run,
            enable_review=args.review,
            strict_coverage=args.strict_coverage,
            image_dir=args.images,
            include_unassigned_images=(i == 1 or len(chapters) == 1),
            filter_images=not args.no_image_filter,
            keep_all_images=args.keep_all_images,
        )
        results.append(result)
        console.info("")
        if args.json:
            continue

    summary = {
        "total": len(results),
        "ok": sum(1 for r in results if r.ok),
        "quality_failed": sum(1 for r in results if r.status == "quality_failed"),
        "failed": sum(1 for r in results if r.status == "failed"),
        "dry_run": sum(1 for r in results if r.status == "dry_run"),
    }
    manifest_data = {
        "generated_at": now_iso(),
        "tool": f"jiaoan {__version__}",
        "textbook": {"path": str(Path(args.textbook)), "title": textbook.title, "chapters": len(textbook.chapters)},
        "model": "offline" if args.offline else settings.model,
        "offline": bool(args.offline),
        "dry_run": bool(args.dry_run),
        "summary": summary,
        "tokens": client.stats_summary(),
        "chapters": [r.to_dict() for r in results],
    }
    manifest_path = out_root / "manifest.json"
    write_text(manifest_path, json.dumps(manifest_data, ensure_ascii=False, indent=2))

    if args.json:
        print(json.dumps(manifest_data, ensure_ascii=False, indent=2))
    else:
        console.info("")
        console.info(f"汇总：成功 {summary['ok']} / 质检未通过 {summary['quality_failed']} / 失败 {summary['failed']} / 预览 {summary['dry_run']}")
        console.info(f"清单：{manifest_path}")
        for r in results:
            if r.files.get("docx"):
                console.info(f"  - {r.chapter_id}：{r.files['docx']}")
            elif r.files.get("markdown"):
                console.info(f"  - {r.chapter_id}：{r.files['markdown']}")
            if r.status == "quality_failed":
                console.warn(f"  - {r.chapter_id} 质检未通过，请查看 {r.output_dir}/质检报告.md")
            if r.error:
                console.warn(f"  - {r.chapter_id}：{r.error}")

    console.close()
    if summary["failed"]:
        return 1
    if summary["quality_failed"]:
        return 3
    return 0


def cmd_chapters(args: argparse.Namespace) -> int:
    manifest = _load_json(args.manifest, "章节清单") if args.manifest else None
    try:
        textbook = parse_textbook(args.textbook, manifest=manifest)
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 2
    data = textbook.to_dict()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print(f"教材：{textbook.title}（{textbook.chars} 字）")
    print(f"{'ID':<8}{'行号':<14}{'字数':<8}{'节数':<6}{'建议课时':<9}标题")
    for c in textbook.chapters:
        print(f"{c.id:<8}{str(c.start_line) + '-' + str(c.end_line):<14}{c.chars:<8}{len(c.sections):<6}{estimate_periods(c):<9}{c.title}")
    return 0


def cmd_inspect_template(args: argparse.Namespace) -> int:
    try:
        spec = parse_template(args.template)
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 2
    if args.json:
        print(json.dumps(spec.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(describe_template(spec))
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    from .web import serve

    try:
        return serve(host=args.host, port=args.port, out_root=args.out, open_browser=args.open)
    except OSError as exc:
        print(f"[ERROR] 启动 Web 服务失败：{exc}")
        return 2


def cmd_doctor(args: argparse.Namespace) -> int:
    console = Console(verbose=True)
    console.info(f"Python：{sys.version.split()[0]}（{sys.executable}）")
    try:
        import docx

        console.info(f"python-docx：{getattr(docx, '__version__', '已安装')}")
    except Exception as exc:
        console.error(f"python-docx 不可用：{exc}")
    console.info(f"工作目录：{Path.cwd()}")
    settings = settings_from_env({"offline": args.offline})
    key = settings.api_key
    masked = (key[:4] + "…" + key[-4:]) if len(key) >= 10 else ("已设置" if key else "未设置")
    console.info(f"API Key：{masked}")
    console.info(f"Base URL：{settings.base_url}")
    console.info(f"模型：{settings.model}")
    console.info(f"超时：{settings.timeout}s｜max_tokens：{settings.max_tokens}｜上下文预算：{settings.max_context_chars} 字")
    console.info(f"缓存目录：{Path(settings.cache_dir).resolve()}")
    if args.ping and not args.offline:
        if not settings.api_key:
            console.error("未配置 API Key，无法 ping。")
            return 2
        try:
            client = LLMClient(settings, console)
            resp = client.chat(
                [
                    {"role": "system", "content": "你是接口连通性测试助手。"},
                    {"role": "user", "content": "只回复两个字：正常"},
                ],
                purpose="ping",
                max_tokens=16,
                json_mode=False,
            )
            console.ok(f"接口连通：{resp.text.strip()[:40]}")
        except LLMError as exc:
            console.error(f"接口不通：{exc}")
            return 1
    console.ok("环境检查完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
