from __future__ import annotations

import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from code.md_image_processor.MdImageProcessor import MdImageProcessor
from code.md_title_organizer.Organizer import Organizer
from code.vision_output_constraint.VisionOutputConstraint import VisionOutputConstraintModel


class MarkdownPipeline:
    """顶层编排器：串联图片提取、标题整理和视觉内容回写。"""

    def __init__(
        self,
        image_processor: Optional[MdImageProcessor] = None,
        organizer: Optional[Organizer] = None,
        vision_model: Optional[VisionOutputConstraintModel] = None,
        temp_root: Optional[Union[str, Path]] = None,
        chapter: Optional[str] = None,
    ) -> None:
        self.image_processor = image_processor or MdImageProcessor(project_root=PROJECT_ROOT)
        self.organizer = organizer or Organizer()
        self.vision_model = vision_model or VisionOutputConstraintModel()
        self.chapter = chapter
        base_dir = Path(temp_root).expanduser().resolve() if temp_root is not None else PROJECT_ROOT / "temp" / "pipeline_runs"
        self.temp_root = base_dir
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = self.temp_root / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.run_dir / "pipeline.log"

    def _append_log(self, message: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(message + "\n")

    @staticmethod
    def _color_text(text: str, color_code: str) -> str:
        if not sys.stdout.isatty():
            return text
        return f"\033[{color_code}m{text}\033[0m"

    @staticmethod
    def _normalize_chapter_name(chapter: Optional[str], md_path: Union[str, Path]) -> str:
        if chapter and chapter.strip():
            return chapter.strip()
        stem = Path(md_path).expanduser().resolve().stem
        return stem.strip() or "chapter"

    @staticmethod
    def _normalize_steps(steps: Optional[Union[str, List[str], tuple[str, ...]]] = None) -> dict:
        """规范化执行步骤。
        支持：
        - None / 'all' -> 全部执行
        - 'images' / 'image' / 1 -> 图片提取
        - 'titles' / 'title' / 2 -> 标题整理
        - 'visual' / 'visual_content' / 'vision' / 3 -> 视觉识别回写
        """
        all_steps = {
            "images": True,
            "titles": True,
            "visual": True,
        }
        if steps is None:
            return all_steps

        if isinstance(steps, str):
            requested = [steps]
        else:
            requested = list(steps)

        normalized = {"images": False, "titles": False, "visual": False}
        if not requested:
            return all_steps

        for item in requested:
            key = str(item).strip().lower()
            if key in {"all", "全部", "*"}:
                return all_steps
            if key in {"1", "image", "images", "img", "extract"}:
                normalized["images"] = True
            elif key in {"2", "title", "titles", "heading", "headings"}:
                normalized["titles"] = True
            elif key in {"3", "visual", "vision", "visual_content", "content"}:
                normalized["visual"] = True

        if not any(normalized.values()):
            return all_steps
        return normalized

    def _log(
        self,
        stage: str,
        message: str,
        input_path: Optional[Union[str, Path]] = None,
        output_path: Optional[Union[str, Path]] = None,
        console: bool = True,
    ) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] [{stage}] {message}"
        if input_path is not None:
            line += f" | 输入: {Path(input_path)}"
        if output_path is not None:
            line += f" | 输出: {Path(output_path)}"
        self._append_log(line)
        if console:
            print(line)

    def _save_stage_snapshot(self, step_name: str, md_path: Union[str, Path], *, label: Optional[str] = None) -> Path:
        source = Path(md_path).expanduser().resolve()
        target_dir = self.run_dir / "stages"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_name = f"{step_name}.md" if not label else f"{step_name}_{label}.md"
        target = target_dir / target_name
        if source.exists():
            shutil.copy2(source, target)
        self._log("SNAPSHOT", f"已保存中间文档快照: {target}", input_path=source, output_path=target, console=False)
        return target

    def _resolve_output_path(
        self,
        input_path: Union[str, Path],
        output_path: Optional[Union[str, Path]] = None,
        overwrite: bool = False,
    ) -> Path:
        src = Path(input_path).expanduser().resolve()
        if overwrite:
            return src
        if output_path is not None:
            target = Path(output_path).expanduser().resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            return target
        return src.with_name(f"{src.stem}_processed{src.suffix}")

    def _iter_markdown_image_paths(self, md_path: Union[str, Path]) -> List[Path]:
        md_file = Path(md_path).expanduser().resolve()
        if not md_file.exists():
            return []

        text = md_file.read_text(encoding="utf-8")
        pattern = re.compile(r"!\[[^\]]*\]\((?P<target>[^)]+)\)")
        results: List[Path] = []

        for match in pattern.finditer(text):
            target = match.group("target").strip()
            target = target.split()[0] if " " in target else target
            if not target:
                continue
            if target.startswith("http://") or target.startswith("https://") or target.startswith("data:"):
                continue
            image_path = (md_file.parent / target).resolve()
            if image_path.exists() and image_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}:
                results.append(image_path)

        return sorted(set(results), key=lambda p: str(p))

    def _prepare_working_copy(self, src: Path, target_path: Path) -> Path:
        """生成独立工作副本，避免直接污染原始 markdown 源文件。"""
        work_dir = self.run_dir / "working"
        work_dir.mkdir(parents=True, exist_ok=True)
        work_file = work_dir / src.name
        if src.exists():
            shutil.copy2(src, work_file)
        else:
            work_file.write_text("", encoding="utf-8")
        return work_file

    def process_markdown_file(
        self,
        md_path: Union[str, Path],
        output_path: Optional[Union[str, Path]] = None,
        overwrite: bool = False,
        custom_instruction: Optional[str] = None,
        title: Optional[str] = None,
        process_images: bool = True,
        process_titles: bool = True,
        process_visual_content: bool = True,
        chapter: Optional[str] = None,
        steps: Optional[Union[str, List[str], tuple[str, ...]]] = None,
        quiet: bool = False,
    ) -> str:
        """处理单个 md 文件，返回最终 md 文件路径。支持按步骤执行。"""
        resolved_steps = self._normalize_steps(steps)
        process_images = process_images and resolved_steps["images"]
        process_titles = process_titles and resolved_steps["titles"]
        process_visual_content = process_visual_content and resolved_steps["visual"]

        src = Path(md_path).expanduser().resolve()
        if not src.exists():
            raise FileNotFoundError(f"Markdown 文件不存在: {src}")
        if src.suffix.lower() != ".md":
            raise ValueError(f"仅支持 .md 文件: {src}")

        resolved_chapter = self._normalize_chapter_name(chapter or self.chapter, src)
        target_path = self._resolve_output_path(src, output_path=output_path, overwrite=overwrite)
        self._log("START", "开始处理 Markdown 文件", input_path=src, output_path=target_path, console=not quiet)
        self._log("CONFIG", f"章节配置: {resolved_chapter} | 执行步骤: {resolved_steps}", input_path=src, output_path=Path(self.image_processor.project_root) / "data" / "images" / resolved_chapter, console=not quiet)
        self._save_stage_snapshot("00_source", src)

        if process_images:
            self._log("STEP 1/3", "提取 Markdown 中的 Base64 图片", input_path=src, console=not quiet)
            self.image_processor.configure_for_markdown(src, chapter=resolved_chapter)
            result = self.image_processor.process_md_file(src, output_image_dir_name=resolved_chapter)
            self._save_stage_snapshot("01_after_image_extraction", src)
            self._log(
                "STEP 1/3 完成",
                f"已提取 {result.get('count', 0)} 张图片，保存目录: {result.get('image_dir', '')}",
                input_path=src,
                output_path=result.get('image_dir'),
                console=not quiet,
            )
        else:
            self._log("STEP 1/3", "跳过图片提取（按步骤配置）", input_path=src, console=not quiet)

        if process_titles:
            self._log("STEP 2/3", "规范化标题与编号", input_path=src, output_path=target_path, console=not quiet)
            self.organizer.organize_markdown_file(str(src), str(target_path), overwrite=overwrite)
            final_md_path = target_path
            self._save_stage_snapshot("02_after_title_organizer", final_md_path)
            self._log("STEP 2/3 完成", "标题整理已完成", input_path=src, output_path=final_md_path, console=not quiet)
        else:
            final_md_path = src if overwrite else target_path
            if final_md_path != src:
                final_md_path.parent.mkdir(parents=True, exist_ok=True)
                final_md_path.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            self._log("STEP 2/3", "跳过标题整理（按步骤配置）", input_path=src, output_path=final_md_path, console=not quiet)

        if process_visual_content:
            image_paths = self._iter_markdown_image_paths(final_md_path)
            self._log("STEP 3/3", f"开始视觉识别，共 {len(image_paths)} 张图片", input_path=final_md_path, console=not quiet)
            for idx, image_path in enumerate(image_paths, start=1):
                self._log("IMAGE", f"识别图片 {idx}/{len(image_paths)}", input_path=image_path, console=not quiet)
                try:
                    result = self.vision_model.predict(
                        image_path,
                        custom_instruction=custom_instruction or "只提取代码和注释，忽略IDE界面，不要把菜单、标签页、状态栏等无关内容录进去。",
                    )
                    self.vision_model.replace_image_reference_in_markdown(
                        final_md_path,
                        image_path,
                        result,
                        title=title,
                    )
                    self._save_stage_snapshot(f"03_visual_{idx:02d}_{image_path.stem}", final_md_path)
                    self._log("IMAGE 完成", f"图片识别已写回 Markdown: {image_path}", input_path=image_path, output_path=final_md_path, console=not quiet)
                except Exception as exc:
                    self._log("IMAGE 跳过", f"图片识别失败，已跳过: {image_path}，原因: {exc}", input_path=image_path, console=not quiet)
            self._save_stage_snapshot("04_final_result", final_md_path)
            self._log("STEP 3/3 完成", "视觉识别与回写已完成", input_path=final_md_path, output_path=final_md_path, console=not quiet)
        else:
            self._log("STEP 3/3", "跳过视觉识别回写（按步骤配置）", input_path=final_md_path, console=not quiet)

        if overwrite:
            src.write_text(Path(final_md_path).read_text(encoding="utf-8"), encoding="utf-8")
            final_md_path = src

        self._log("DONE", "Markdown 处理完成", input_path=src, output_path=final_md_path, console=not quiet)
        return str(final_md_path)

    def process_folder(
        self,
        folder: Union[str, Path],
        output_dir: Optional[Union[str, Path]] = None,
        recursive: bool = False,
        overwrite: bool = False,
        custom_instruction: Optional[str] = None,
        title: Optional[str] = None,
        chapter: Optional[str] = None,
        steps: Optional[Union[str, List[str], tuple[str, ...]]] = None,
        quiet: bool = True,
    ) -> List[str]:
        """批量处理文件夹中的所有 md 文件。支持按步骤执行。"""
        src_dir = Path(folder).expanduser().resolve()
        if not src_dir.exists():
            raise FileNotFoundError(f"文件夹不存在: {src_dir}")
        if not src_dir.is_dir():
            raise ValueError(f"不是文件夹: {src_dir}")

        files = sorted(p for p in (src_dir.rglob("*.md") if recursive else src_dir.glob("*.md")) if p.is_file())
        self._log("BATCH", f"开始批量处理 {len(files)} 个 Markdown 文件", input_path=src_dir, output_path=output_dir, console=not quiet)
        outputs: List[str] = []

        if output_dir is not None:
            out_dir = Path(output_dir).expanduser().resolve()
            out_dir.mkdir(parents=True, exist_ok=True)
        
        for md_file in files:
            target = None
            if output_dir is not None:
                target = Path(output_dir).expanduser().resolve() / f"{md_file.stem}_processed{md_file.suffix}"
            self._log("BATCH_RUNNING", f"正在处理: {md_file.name}", input_path=md_file, output_path=target, console=not quiet)
            try:
                result = self.process_markdown_file(
                    md_file,
                    output_path=target,
                    overwrite=overwrite,
                    custom_instruction=custom_instruction,
                    title=title,
                    chapter=chapter,
                    steps=steps,
                    quiet=quiet,
                )
                outputs.append(result)
                self._log("BATCH_DONE", f"已处理完成: {md_file.name}", input_path=md_file, output_path=result, console=not quiet)
            except Exception as exc:
                self._log("BATCH_ERROR", f"处理失败: {md_file.name} | {exc}", input_path=md_file, console=not quiet)
                continue

        self._log("BATCH 完成", "批量 Markdown 处理完成", input_path=src_dir, output_path=output_dir, console=not quiet)
        return outputs

    def run(self, md_path: Union[str, Path], **kwargs) -> str:
        """便捷入口。"""
        return self.process_markdown_file(md_path, **kwargs)


if __name__ == "__main__":
    pipeline = MarkdownPipeline()
    chapter="第2章"
    sample_md = PROJECT_ROOT / "data" / "md" / f"{chapter}.md"
    output_path = PROJECT_ROOT / "data" / "output" / f"{chapter}_processed.md"
    result_path = pipeline.process_markdown_file(
        sample_md,
        output_path=output_path,
        overwrite=False,
        chapter=chapter
    )
    print(f"处理完成: {result_path}")
    print(f"中间产物目录: {pipeline.run_dir}")
    
    
    
    # src_dir = PROJECT_ROOT / "data" / "md"
    # out_dir = PROJECT_ROOT / "data" / "output"
    
    # print(f'input: {src_dir}')
    # print(f'output: {out_dir}')
    # files = pipeline.process_folder(
    #     src_dir,
    #     output_dir=out_dir,
    #     recursive=False,
    #     overwrite=False,
    #     quiet=True,
    # )
    # print(f"批量处理完成，共 {len(files)} 个文件")
    # print(f"输出目录: {out_dir}")
    # print(f"日志文件: {pipeline.log_path}")
