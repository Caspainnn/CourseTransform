import re
from pathlib import Path
from typing import Optional


class NumberFormatOrganizer:
    """统一规范 Markdown 文档中的序号括号格式。

    目标：
    - 1) -> 1）
    - (1) -> （1）
    - 1） -> 1）
    - （1） -> （1）

    只做统一，不做额外语义改写。
    """

    @staticmethod
    def normalize_text(text: str) -> str:
        """规范化一段文本中的序号括号格式。"""
        if not text:
            return text

        normalized = text

        # (1) -> （1）
        normalized = re.sub(r'\((\d+)\)', r'（\1）', normalized)

        # 1) -> 1）
        # 只转换英文右括号，保留“第 7 章”这类结构不做处理
        normalized = re.sub(r'(?<![（])(?<![\)])(\d+)\)', r'\1）', normalized)

        return normalized

    def normalize_document(self, markdown_content: str) -> str:
        """规范化整个 Markdown 文档。"""
        return self.normalize_text(markdown_content)

    def normalize_file(self, input_path: str, output_path: Optional[str] = None, overwrite: bool = False) -> str:
        """规范化单个 Markdown 文件。"""
        input_file = Path(input_path)
        if not input_file.exists():
            raise FileNotFoundError(f"输入文件不存在: {input_file}")

        text = input_file.read_text(encoding='utf-8')
        normalized = self.normalize_document(text)

        if output_path is not None:
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(normalized, encoding='utf-8')
            return normalized

        if overwrite:
            input_file.write_text(normalized, encoding='utf-8')
            return normalized

        return normalized


if __name__ == "__main__":
    samples = [
        "1) 这是第一项",
        "2) 这是第二项",
        "3） 这是第三项",
        "(1) 这是第一项",
        "第 7 章 安全与隐私",
        "这是普通文本 1) 不应被误改",
    ]
    organizer = NumberFormatOrganizer()
    for s in samples:
        print(f"输入: {s!r}")
        print(f"输出: {organizer.normalize_text(s)!r}")
        print('-' * 60)
