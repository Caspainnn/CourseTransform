from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

try:
    from PIL import Image
except ImportError as exc:  # pragma: no cover
    raise SystemExit("请先安装 Pillow：pip install pillow") from exc

try:
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]
IMAGE_ROOT = PROJECT_ROOT / "data" / "images"
MD_ROOT = PROJECT_ROOT / "data" / "md"
SUPPORTED_IMAGE_TYPES = {"qr", "code", "formula", "table", "flow", "structure", "curve", "other"}


class ChapterImageProcessor:
    """统一处理章节图片分类、命名和 Markdown 重写。

    设计目标：
    1. 先做 OCR 实测，确保代码截图能被识别成代码文本
    2. 在 Markdown 中把原图片位置替换成代码块 + 新图片链接
    3. 以后继续扩展二维码、公式、表格等类型时，可以在这里继续添加判定逻辑
    4. 允许按类型选择处理，默认全部处理，并跳过已处理过的图片
    """

    def __init__(
        self,
        chapter_name: str | None = None,
        project_root: Path | None = None,
        ocr_language: str = "chi_sim+eng",
    ):
        self.project_root = Path(project_root) if project_root else PROJECT_ROOT
        self.image_root = self.project_root / "data" / "images"
        self.md_root = self.project_root / "data" / "md"
        self.chapter_name = chapter_name or self.auto_detect_chapter()
        self.image_dir = self.image_root / self.chapter_name
        self.md_path = self.md_root / f"{self.chapter_name}.md"
        self.ocr_language = ocr_language

    @staticmethod
    def normalize_image_types(raw_types: list[str] | None) -> set[str]:
        """把命令行参数规范化为通用类型集合。

        允许输入：
        - all
        - qr
        - qr code
        - qr,code
        - qr code formula
        """
        if not raw_types:
            return set(SUPPORTED_IMAGE_TYPES)

        normalized: set[str] = set()
        for item in raw_types:
            for part in item.replace(",", " ").split():
                value = part.strip().lower()
                if not value:
                    continue
                if value == "all":
                    return set(SUPPORTED_IMAGE_TYPES)
                if value in SUPPORTED_IMAGE_TYPES:
                    normalized.add(value)
                else:
                    raise ValueError(f"不支持的图片类型: {value}，允许值：{sorted(SUPPORTED_IMAGE_TYPES)}")
        return normalized or set(SUPPORTED_IMAGE_TYPES)

    def should_skip_processed_image(self, image_path: Path, image_type: str) -> bool:
        """跳过已经处理过的图片，避免重复重命名/重复改写 Markdown。

        例如：
        - qr-0001.jpg 已处理过二维码
        - code-0001.jpg 已处理过代码截图
        """
        name = image_path.name.lower()
        prefixes = {
            "qr": ("qr-",),
            "code": ("code-",),
            "formula": ("formula-",),
            "table": ("table-",),
            "flow": ("flow-",),
            "structure": ("structure-",),
            "curve": ("curve-",),
            "other": ("other-",),
        }

        for prefix in prefixes.get(image_type, ()):  # 只跳过当前类型的已处理文件
            if name.startswith(prefix):
                return True
        return False

    # ------------------------------------------------------------
    # 基础路径/章节识别
    # ------------------------------------------------------------

    def auto_detect_chapter(self) -> str:
        """自动检测当前可处理的章节名。"""
        chapter_dirs = sorted(self.image_root.glob("第*章"))
        if chapter_dirs:
            return chapter_dirs[0].name

        md_files = sorted(self.md_root.glob("*.md"))
        if md_files:
            return md_files[0].stem

        raise FileNotFoundError("未找到 data/images/第x章 或 data/md/第x章.md")

    # ------------------------------------------------------------
    # 图片分类：当前先实现二维码 + 代码截图
    # ------------------------------------------------------------

    def is_qr_code(self, image_path: Path) -> bool:
        """使用图片尺寸判断二维码，范围建议为 100x100 ~ 200x200。"""
        try:
            with Image.open(image_path) as img:
                width, height = img.size
            return 100 <= width <= 200 and 100 <= height <= 200
        except Exception:
            return False

    def classify_image(self, image_path: Path) -> str:
        """根据图片尺寸和 OCR 结果进行分类。

        当前策略：
        1. 若尺寸落在二维码范围，则判定为二维码
        2. 若不是二维码，则按代码截图分支处理（后续可以扩展公式、表格等）
        """
        if self.should_skip_processed_image(image_path, "qr") and self.should_skip_processed_image(image_path, "code"):
            return "已处理"
        if self.is_qr_code(image_path):
            return "二维码"
        return "代码截图"

    def _ensure_tesseract_available(self) -> None:
        """检查 Tesseract 可执行文件是否可用，并在常见 Windows 安装位置自动设置。"""
        if pytesseract is None:
            raise RuntimeError("未检测到 pytesseract。请先安装：pip install pytesseract")

        env_path = shutil.which("tesseract")
        if env_path:
            pytesseract.pytesseract.tesseract_cmd = env_path
            return

        common_paths = [
            Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
            Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
            Path(r"D:\Tesseract-OCR\tesseract.exe"),
        ]

        for candidate in common_paths:
            if candidate.exists():
                pytesseract.pytesseract.tesseract_cmd = str(candidate)
                return

        raise RuntimeError(
            "未检测到 Tesseract-OCR。请安装并确保 tesseract.exe 在 PATH 中，或设置：\n"
            "1) 下载 Tesseract OCR（Windows）\n"
            "2) 安装到默认目录：C:\\Program Files\\Tesseract-OCR\\\n"
            "3) 重新运行脚本。"
        )

    # ------------------------------------------------------------
    # OCR 与清洗
    # ------------------------------------------------------------

    def ocr_image_to_text(self, image_path: Path) -> str:
        """使用 Tesseract 做 OCR，返回文本内容。"""
        self._ensure_tesseract_available()

        try:
            with Image.open(image_path) as img:
                text = pytesseract.image_to_string(img, lang=self.ocr_language)
            return text.strip()
        except Exception as exc:
            raise RuntimeError(f"OCR 识别失败：{image_path} -> {exc}") from exc

    def clean_code_ocr_text(self, text: str) -> str:
        """对 OCR 文本做基础规则清洗，适用于代码截图。

        不是最终的复杂修正方案，只是先把最影响效果的噪声去掉，
        让 OCR 输出更接近代码体裁。
        """
        lines = text.splitlines()
        cleaned: list[str] = []

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            # 去掉明显页面噪声：页眉/页脚/图示/提示语等
            if re.fullmatch(r"[-_=#*]{3,}", line):
                continue
            if re.match(r"^(图|表|流程|结构|二维码|截图|说明|备注|Page|页|第\s*\d+\s*页)", line, flags=re.I):
                continue
            if len(line) < 2 and not re.search(r"[A-Za-z0-9_]", line):
                continue

            cleaned.append(line)

        # 过滤极短碎片，避免 OCR 后出现大量无意义的单字/符号行
        final_lines = []
        for line in cleaned:
            if len(line) <= 1 and not re.search(r"[A-Za-z0-9_]", line):
                continue
            final_lines.append(line)

        # 如果清洗后为空，则保留原始文本，避免直接丢失
        if not final_lines:
            return text.strip()

        return "\n".join(final_lines).strip()

    # ------------------------------------------------------------
    # Markdown 更新
    # ------------------------------------------------------------

    def replace_markdown_image_reference(
        self,
        md_text: str,
        old_name: str,
        new_name: str,
        code_text: str,
        new_path: str,
    ) -> str:
        """把原图片引用位置改成：
        1) 代码块（OCR 识别结果）
        2) 新一行图片链接
        """
        pattern = re.compile(r"!\[(?P<alt>[^\]]*)\]\((?P<path>[^)]+)\)")

        def repl(match: re.Match[str]) -> str:
            path = match.group("path")
            if old_name not in path:
                return match.group(0)

            code_block = f"```python\n{code_text}\n```\n\n![]({new_path})"
            return code_block

        return pattern.sub(repl, md_text)

    # ------------------------------------------------------------
    # 图片重命名
    # ------------------------------------------------------------

    def rename_qr_code_image(self, image_path: Path, index: int) -> Path:
        """二维码命名，如：qr-0001.jpg。若目标文件已存在，则自动递增到下一个可用编号。"""
        suffix = image_path.suffix.lower() or ".jpg"
        new_name = f"qr-{index:04d}{suffix}"
        new_path = image_path.with_name(new_name)

        while new_path.exists():
            index += 1
            new_name = f"qr-{index:04d}{suffix}"
            new_path = image_path.with_name(new_name)

        if image_path != new_path:
            image_path.rename(new_path)
        return new_path

    def rename_code_image(self, image_path: Path, index: int) -> Path:
        """代码截图命名，如：code-0001.jpg。若目标文件已存在，则自动递增到下一个可用编号。"""
        suffix = image_path.suffix.lower() or ".jpg"
        new_name = f"code-{index:04d}{suffix}"
        new_path = image_path.with_name(new_name)

        while new_path.exists():
            index += 1
            new_name = f"code-{index:04d}{suffix}"
            new_path = image_path.with_name(new_name)

        if image_path != new_path:
            image_path.rename(new_path)
        return new_path

    # ------------------------------------------------------------
    # 主处理入口
    # ------------------------------------------------------------

    def process_qr_code_images(self) -> int:
        """处理二维码：重命名图片并更新引用路径。"""
        if not self.image_dir.exists():
            raise FileNotFoundError(f"图片目录不存在: {self.image_dir}")
        if not self.md_path.exists():
            raise FileNotFoundError(f"Markdown 文件不存在: {self.md_path}")

        qr_index = 0
        md_text = self.md_path.read_text(encoding="utf-8")

        for image_path in sorted(self.image_dir.iterdir()):
            if not image_path.is_file():
                continue
            if self.should_skip_processed_image(image_path, "qr"):
                continue
            if self.classify_image(image_path) != "二维码":
                continue

            qr_index += 1
            old_name = image_path.name
            new_path = self.rename_qr_code_image(image_path, qr_index)
            new_name = new_path.name
            new_relative_path = f"../images/{self.chapter_name}/{new_name}"

            md_text = md_text.replace(old_name, new_name)
            md_text = md_text.replace(f"![二维码]({new_relative_path})", f"![二维码]({new_relative_path})")

        self.md_path.write_text(md_text, encoding="utf-8")
        print(f"已处理二维码：{self.chapter_name}，共重命名 {qr_index} 张图片")
        return qr_index

    def process_code_screenshots(self) -> int:
        """处理代码截图：OCR 提取内容，并替换 Markdown 中图片位置为代码块 + 新图链接。"""
        if not self.image_dir.exists():
            raise FileNotFoundError(f"图片目录不存在: {self.image_dir}")
        if not self.md_path.exists():
            raise FileNotFoundError(f"Markdown 文件不存在: {self.md_path}")

        code_index = 0
        md_text = self.md_path.read_text(encoding="utf-8")

        for image_path in sorted(self.image_dir.iterdir()):
            if not image_path.is_file():
                continue
            if self.should_skip_processed_image(image_path, "code"):
                continue
            if self.classify_image(image_path) != "代码截图":
                continue

            code_index += 1
            old_name = image_path.name
            new_path = self.rename_code_image(image_path, code_index)
            new_name = new_path.name

            # 识别代码截图中的内容
            ocr_text = self.ocr_image_to_text(new_path)
            cleaned_code = self.clean_code_ocr_text(ocr_text)
            if not cleaned_code:
                cleaned_code = "# OCR 识别为空，请人工检查该图片"

            new_relative_image_path = f"../images/{self.chapter_name}/{new_name}"
            md_text = self.replace_markdown_image_reference(
                md_text,
                old_name,
                new_name,
                cleaned_code,
                new_relative_image_path,
            )

        self.md_path.write_text(md_text, encoding="utf-8")
        print(f"已处理代码截图：{self.chapter_name}，共重命名 {code_index} 张图片")
        return code_index

    def process_chapter(self, image_types: list[str] | None = None) -> dict[str, int]:
        """处理一个章节中的指定图片类型。

        默认全部处理；也支持只处理某一种/几种类型。
        例如：
        - ["qr"]
        - ["code"]
        - ["qr", "code"]
        - ["all"]
        """
        selected = self.normalize_image_types(image_types)
        stats: dict[str, int] = {}

        if "qr" in selected:
            stats["二维码"] = self.process_qr_code_images()
        if "code" in selected:
            stats["代码截图"] = self.process_code_screenshots()

        return stats


# ------------------------------------------------------------
# CLI 入口：供脚本直接运行
# ------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="教材章节图片重命名与 Markdown OCR 处理脚本")
    parser.add_argument("--chapter", default=None, help="例如：第7章")
    parser.add_argument(
        "--project-root",
        default=str(PROJECT_ROOT),
        help="项目根目录，默认自动识别",
    )
    parser.add_argument(
        "--ocr-language",
        default="chi_sim+eng",
        help="Tesseract 语言包，例如：chi_sim+eng、eng、chi_sim",
    )
    parser.add_argument(
        "--types",
        nargs="+",
        default=["all"],
        help="可选：all / qr / code；也支持多个，如：--types qr code 或 --types qr,code",
    )
    args = parser.parse_args()

    processor = ChapterImageProcessor(
        chapter_name=args.chapter,
        project_root=Path(args.project_root),
        ocr_language=args.ocr_language,
    )

    normalized_types = processor.normalize_image_types(args.types)
    stats = processor.process_chapter(list(normalized_types))
    print("处理结果：", stats)


if __name__ == "__main__":
    main()
