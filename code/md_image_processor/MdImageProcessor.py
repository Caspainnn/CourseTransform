from __future__ import annotations

import base64
import re
import shutil
from pathlib import Path
from typing import Optional, Union


class MdImageProcessor:
    """处理 markdown 中的图片链接，提取并保存为真实文件。

    适用场景：
    - Markdown 中嵌入了 data:image/...;base64 的图片
    - 需要把这些图片保存到真实目录中，并将文档中的引用改为相对路径
    """

    def __init__(
        self,
        project_root: Optional[Union[str, Path]] = None,
        md_path: Optional[Union[str, Path]] = None,
        output_image_dir_name: Optional[str] = None,
    ) -> None:
        self.project_root = Path(project_root).resolve() if project_root else Path(__file__).resolve().parents[2]
        self.md_path = Path(md_path).resolve() if md_path else self.project_root / "data" / "md" / "第7章.md"
        self.output_image_dir_name = output_image_dir_name or self.md_path.stem
        self.output_image_dir = self.project_root / "data" / "images" / self.output_image_dir_name

    def configure_for_markdown(
        self,
        md_path: Optional[Union[str, Path]] = None,
        *,
        chapter: Optional[str] = None,
        output_image_dir_name: Optional[str] = None,
    ) -> None:
        """根据 md 文件路径统一重置章节名和输出目录，避免不同章节混用同一目录。"""
        if md_path is not None:
            self.md_path = Path(md_path).expanduser().resolve()

        selected_chapter = chapter or output_image_dir_name or self.md_path.stem
        normalized = selected_chapter.strip()
        if not normalized:
            normalized = self.md_path.stem

        self.output_image_dir_name = normalized
        self.output_image_dir = self.project_root / "data" / "images" / normalized

    def _ensure_directories(self) -> None:
        self.output_image_dir.mkdir(parents=True, exist_ok=True)

    def _extract_base64_images(self, text: str) -> list[tuple[str, str, str]]:
        pattern = re.compile(r'!\[([^\]]*)\]\(data:image/([^;]+);base64,([^\)]+)\)')
        matches = []
        for match in pattern.finditer(text):
            alt = match.group(1)
            ext = match.group(2).replace("jpeg", "jpg")
            data = match.group(3)
            matches.append((alt, ext, data))
        return matches

    def remove_data_image_links(
        self,
        md_path: Optional[Union[str, Path]] = None,
        backup: bool = True,
        replace_with: Optional[str] = None,
        dry_run: bool = False,
    ) -> int:
        """删除 markdown 中的 data: base64 图片链接。

        参数：
        - md_path: 目标 md 文件
        - backup: 是否备份原文件到 .bak
        - replace_with: 用指定文本替换图片链接，若为 None 则直接删除
        - dry_run: 仅统计，不写回文件

        返回：删除/替换的数量。
        """
        target_md = Path(md_path).resolve() if md_path is not None else self.md_path
        if not target_md.exists():
            raise FileNotFoundError(f"Markdown 文件不存在: {target_md}")

        pattern = re.compile(r"!\[[^\]]*\]\(data:[^)]+\)")
        text = target_md.read_text(encoding="utf-8")
        new_text, n = pattern.subn(replace_with or "", text)

        if n == 0:
            return 0
        if not dry_run:
            if backup:
                bak = target_md.with_suffix(target_md.suffix + ".bak")
                shutil.copy2(target_md, bak)
            target_md.write_text(new_text, encoding="utf-8")
        return n

    def process_md_file(
        self,
        md_path: Optional[Union[str, Path]] = None,
        output_image_dir_name: Optional[str] = None,
    ) -> dict:
        """处理指定 md 文件中的内嵌 base64 图片。

        返回：{
            "md_path": ..., 
            "image_dir": ..., 
            "count": N,
            "saved_files": [...] 
        }
        """
        if md_path is not None:
            self.configure_for_markdown(md_path, output_image_dir_name=output_image_dir_name)
        elif output_image_dir_name is not None:
            self.configure_for_markdown(self.md_path, output_image_dir_name=output_image_dir_name)

        if not self.md_path.exists():
            raise FileNotFoundError(f"Markdown 文件不存在: {self.md_path}")

        self._ensure_directories()

        text = self.md_path.read_text(encoding="utf-8")
        matches = self._extract_base64_images(text)
        if not matches:
            return {
                "md_path": str(self.md_path),
                "image_dir": str(self.output_image_dir),
                "count": 0,
                "saved_files": [],
            }

        saved_files = []
        counter = 0

        def replace(match: re.Match[str]) -> str:
            nonlocal counter
            alt = match.group(1)
            ext = match.group(2).replace("jpeg", "jpg")
            data = match.group(3)

            counter += 1
            filename = f"image-{counter:04d}.{ext}"
            output_path = self.output_image_dir / filename
            output_path.write_bytes(base64.b64decode(data))
            saved_files.append(str(output_path))

            relative_image_path = f"../images/{self.output_image_dir_name}/{filename}"
            return f"![{alt}]({relative_image_path})"

        new_text = re.sub(
            r'!\[([^\]]*)\]\(data:image/([^;]+);base64,([^\)]+)\)',
            replace,
            text,
        )
        self.md_path.write_text(new_text, encoding="utf-8")

        return {
            "md_path": str(self.md_path),
            "image_dir": str(self.output_image_dir),
            "count": counter,
            "saved_files": saved_files,
        }


if __name__ == "__main__":
    md_file = Path(__file__).resolve().parents[2] / "data" / "md" / "第7章.md"
    processor = MdImageProcessor(md_path=md_file)
    result = processor.process_md_file()
    print(result)
