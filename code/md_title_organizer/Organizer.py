from pathlib import Path
from typing import Optional

from .MdTitleOrganizer import MdTitleOrganizer
from .NumberFormatOrganizer import NumberFormatOrganizer


class Organizer:
    """统一调度入口：
    1. 先规范化序号格式（如 1) -> （1））
    2. 再修正标题层级（如第1章 -> # 第1章）
    """

    def __init__(self):
        self.number_organizer = NumberFormatOrganizer()
        self.title_organizer = MdTitleOrganizer()

    def organize_markdown_content(self, markdown_content: str) -> str:
        """处理一段 Markdown 文本。"""
        normalized = self.number_organizer.normalize_document(markdown_content)
        return self.title_organizer.organize_markdown_content(normalized)

    def organize_markdown_file(
        self,
        input_path: str,
        output_path: Optional[str] = None,
        overwrite: bool = False,
    ) -> str:
        """处理单个 Markdown 文件。默认新建，不覆盖原文件。"""
        input_file = Path(input_path)
        if not input_file.exists():
            raise FileNotFoundError(f"输入文件不存在: {input_file}")

        raw_content = input_file.read_text(encoding='utf-8')
        processed = self.organize_markdown_content(raw_content)

        if output_path is not None:
            target = Path(output_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(processed, encoding='utf-8')
            return processed

        if overwrite:
            input_file.write_text(processed, encoding='utf-8')
            return processed

        default_target = input_file.with_name(f"{input_file.stem}_processed{input_file.suffix}")
        default_target.write_text(processed, encoding='utf-8')
        return processed

    def process_document(self, markdown_content: str) -> str:
        return self.organize_markdown_content(markdown_content)

    def process_file(self, input_path: str, output_path: Optional[str] = None, overwrite: bool = False) -> str:
        return self.organize_markdown_file(input_path=input_path, output_path=output_path, overwrite=overwrite)


# 更可读的公开名字：保留 tested 的 organizer 逻辑为主，别名用于调用更清晰
MarkdownTitleProcessor = Organizer
MarkdownTitleOrganizer = Organizer


if __name__ == "__main__":
    sample = """\n1) 第一节 基础概念\n2) 第二节 应用\n3） 第三节 实践\n\n## 第 7 章 安全概述\n"""
    organizer = Organizer()
    print(organizer.organize_markdown_content(sample))
