from __future__ import annotations

import base64
import json
import os
import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from openai import OpenAI


class ImageContentType(str, Enum):
    """视觉内容的识别类型。

    这些类型对应：代码截图、公式图片、表格图片、流程图、结构图、曲线图、通用OCR。
    """

    CODE = "code"
    FORMULA = "formula"
    TABLE = "table"
    FLOWCHART = "flowchart"
    STRUCTURE = "structure"
    CURVE = "curve"
    OCR = "ocr"


class VisionOutputConstraintModel:
    """为视觉大模型提供统一输出约束与后处理。

    设计目标：
    1. 输入图片；
    2. 模型输出严格 JSON；
    3. 后处理阶段把 JSON 转成 markdown 或可直接插入文档的结构化结果。

    适用于：代码截图、公式、表格、流程图、结构图、曲线图等教材/文档类内容。
    """

    def __init__(
        self,
        model_path: str = "./models/Qwen3-VL-4B-Instruct",
        base_url: str = "http://127.0.0.1:8000/v1",
        api_key: str = "EMPTY",
        max_tokens: int = 2000,
        timeout: Optional[int] = None,
    ) -> None:
        self.model_path = model_path
        self.client = OpenAI(base_url=base_url, api_key=api_key)
        self.max_tokens = max_tokens
        self.timeout = timeout

    def _read_image_base64(self, image_path: Union[str, Path]) -> str:
        image_path = Path(image_path)
        with image_path.open("rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _normalize_task_type(self, task_type: Union[str, ImageContentType]) -> str:
        if isinstance(task_type, ImageContentType):
            return task_type.value
        return str(task_type).strip().lower()

    def _normalize_md_image_path(self, path: str) -> str:
        """归一化 markdown 图片路径，适配相对路径/Windows 路径。"""
        if not path:
            return ""
        cleaned = path.strip().strip('"\'')
        if cleaned.startswith("data:") or cleaned.startswith("http://") or cleaned.startswith("https://"):
            return cleaned.lower()
        converted = cleaned.replace("\\", "/")
        while "//" in converted:
            converted = converted.replace("//", "/")
        return converted

    def build_system_prompt(self, task_type: Optional[Union[str, ImageContentType]] = None) -> str:
        task_name = self._normalize_task_type(task_type) if task_type is not None else "auto_classify"
        if task_type is None:
            return (
                "You are a strict visual content parser for educational materials. "
                "Your task is to inspect the provided image, classify it into one of: code, formula, table, flowchart, structure, curve, ocr, "
                "and then output the matching JSON schema for that category. "
                "No markdown fences, no explanation, no extra text, no analysis logs. "
                "The JSON must be fully parseable by json.loads(). "
                "If information is uncertain, set the value to null or a reasonable empty structure instead of hallucinating."
            )

        return (
            "You are a strict visual content parser for educational materials. "
            "Your task is to analyze the provided image and return ONLY valid JSON. "
            "No markdown fences, no explanation, no extra text, no analysis logs. "
            "The JSON must be fully parseable by json.loads(). "
            "If information is uncertain, set the value to null or a reasonable empty structure instead of hallucinating. "
            f"The image is of type: {task_name}. "
            "Use the exact JSON schema required by the task type."
        )

    def build_user_prompt(self, task_type: Optional[Union[str, ImageContentType]] = None, custom_instruction: Optional[str] = None) -> str:
        task_name = self._normalize_task_type(task_type) if task_type is not None else "auto"
        schema_map = {
            ImageContentType.CODE.value: (
                "Return a JSON object strictly matching this structure: "
                "{\n"
                "  \"type\": \"code\",\n"
                "  \"language\": \"python\",\n"
                "  \"code\": \"string content of code block including comments and real source code\",\n"
                "  \"ocr_text\": \"raw OCR text if any\",\n"
                "  \"confidence\": 0.0,\n"
                "  \"notes\": \"short note\"\n"
                "}\n"
                "Rules: keep code as plain text, never wrap in Markdown code fences. "
                "This is a code screenshot. Extract only the actual source code and comments that belong to the program. "
                "Comments are mandatory and must be preserved, including `# ...`, `// ...`, `/* ... */`, `-- ...`, `% ...` style comments when present. "
                "Ignore all VS Code / IDE UI elements such as tabs, file explorer, sidebars, terminal panels, breadcrumbs, search results, debug panels, status bar, run/debug buttons, line numbers, and editor chrome. "
                "Do not include irrelevant interface text, window chrome, or prompt text. "
                "If actual code is not fully identifiable, return best-effort OCR content in `ocr_text` and keep `code` as a best-effort cleaned string that still includes comments and source text."
            ),
            ImageContentType.FORMULA.value: (
                "Return a JSON object strictly matching this structure: "
                "{\n"
                "  \"type\": \"formula\",\n"
                "  \"latex\": \"formula in LaTeX\",\n"
                "  \"render_mode\": \"mineru|latex_ocr|vision\",\n"
                "  \"confidence\": 0.0,\n"
                "  \"notes\": \"short note\"\n"
                "}\n"
                "Prefer MinerU result when available. If MinerU fails, output best-effort LaTeX from OCR or vision model."
            ),
            ImageContentType.TABLE.value: (
                "Return a JSON object strictly matching this structure: "
                "{\n"
                "  \"type\": \"table\",\n"
                "  \"header\": [\"col1\", \"col2\"],\n"
                "  \"rows\": [[\"val1\", \"val2\"]],\n"
                "  \"markdown\": \"| col1 | col2 |\\n| --- | --- |\\n| val1 | val2 |\",\n"
                "  \"confidence\": 0.0,\n"
                "  \"notes\": \"short note\"\n"
                "}\n"
                "Convert to Markdown table syntax. Keep values as plain text. Do not output HTML tables."
            ),
            ImageContentType.FLOWCHART.value: (
                "Return a JSON object strictly matching this structure: "
                "{\n"
                "  \"type\": \"flowchart\",\n"
                "  \"description\": \"plain-text explanation of the process, steps, decisions and outcomes\",\n"
                "  \"mermaid\": \"optional; only include Mermaid when confident, otherwise null\",\n"
                "  \"confidence\": 0.0\n"
                "}\n"
                "For small models, DO NOT force a Mermaid graph when uncertain. "
                "Prefer a clear textual description of the process in `description`. "
                "If Mermaid is not reliable, set `mermaid` to null. "
                "A simple text block is more accurate than a fake diagram."
            ),
            ImageContentType.STRUCTURE.value: (
                "Return a JSON object strictly matching this structure: "
                "{\n"
                "  \"type\": \"structure\",\n"
                "  \"description\": \"plain-text explanation of the hierarchy, components, and relationships\",\n"
                "  \"mermaid\": \"optional; only include Mermaid when confident, otherwise null\",\n"
                "  \"confidence\": 0.0\n"
                "}\n"
                "For small models, DO NOT force Mermaid generation. "
                "When uncertain, prefer a reliable textual description of the structure; `mermaid` can be null."
            ),
            ImageContentType.CURVE.value: (
                "Return a JSON object strictly matching this structure: "
                "{\n"
                "  \"type\": \"curve\",\n"
                "  \"title\": \"chart title\",\n"
                "  \"x_axis\": {\"label\": \"x\", \"unit\": \"\"},\n"
                "  \"y_axis\": {\"label\": \"y\", \"unit\": \"\"},\n"
                "  \"series\": [{\"name\": \"series1\", \"points\": [[0, 1], [1, 2]], \"trend\": \"increasing\"}],\n"
                "  \"summary\": \"structured description in text\",\n"
                "  \"confidence\": 0.0\n"
                "}\n"
                "Provide a structured textual description of the curve and its trend."
            ),
            ImageContentType.OCR.value: (
                "Return a JSON object strictly matching this structure: "
                "{\n"
                "  \"type\": \"ocr\",\n"
                "  \"text\": \"recognized text\",\n"
                "  \"confidence\": 0.0\n"
                "}\n"
                "This is a fallback OCR mode. Keep only the extracted text."
            ),
        }

        if task_type is None:
            task_schema = (
                "First classify the image automatically. "
                "Then output one JSON object whose `type` is exactly one of: code, formula, table, flowchart, structure, curve, ocr. "
                "The object must contain the fields required by that detected type. "
                "For code screenshots, the code field must include both source code and comments."
            )
        else:
            task_schema = schema_map.get(task_name, (
                "Return a JSON object with fields: {\n"
                "  \"type\": \"general\",\n"
                "  \"summary\": \"short description\",\n"
                "  \"confidence\": 0.0\n"
                "}\n"
            ))

        extra = f"\nAdditional guidance:\n{custom_instruction}\n" if custom_instruction else ""
        return (
            "Please analyze the image carefully. "
            "Return JSON only, no Markdown, no prose. "
            "Do not include any key outside the schema. "
            f"{task_schema}{extra}"
        )

    def _parse_json_response(self, content: str) -> Dict[str, Any]:
        if content is None:
            raise ValueError("模型返回内容为空")

        text = str(content).strip()

        if not text:
            raise ValueError("模型返回内容为空字符串")

        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
        if fence_match:
            text = fence_match.group(1)

        if text.startswith("json") and "\n" in text:
            text = text.split("\n", 1)[1].strip()

        start_idx = text.find("{")
        end_idx = text.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            text = text[start_idx : end_idx + 1]

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"模型返回的 JSON 无法解析: {text[:500]}") from exc

    def predict(
        self,
        image_path: Union[str, Path],
        task_type: Optional[Union[str, ImageContentType]] = None,
        custom_instruction: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """传入图片路径，自动分类并返回结构化 JSON。

        兼容旧调用：predict(image_path, ImageContentType.CODE, ...)
        但推荐默认不传 task_type，让模型自行判断内容类型。
        """
        task_name = self._normalize_task_type(task_type) if task_type is not None else None
        image_base64 = self._read_image_base64(image_path)

        response = self.client.chat.completions.create(
            model=self.model_path,
            messages=[
                {
                    "role": "system",
                    "content": self.build_system_prompt(task_name),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": self.build_user_prompt(task_name, custom_instruction),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}",
                            },
                        },
                    ],
                },
            ],
            max_tokens=max_tokens or self.max_tokens,
            timeout=self.timeout,
        )

        raw_content = response.choices[0].message.content
        return self._parse_json_response(raw_content)

    def process_image_to_markdown(
        self,
        markdown_path: Union[str, Path],
        image_path: Union[str, Path],
        custom_instruction: Optional[str] = None,
        title: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """完整流程：自动分类 -> 识别 -> 写回指定 md 文档对应图片位置。

        这是更符合你要求的入口：
        - 输入：markdown_path, image_path
        - 做事：模型先分类，再输出对应结构化内容
        - 后处理：将结果替换到 md 文档中的对应图片引用位置
        """
        result = self.predict(image_path, custom_instruction=custom_instruction, max_tokens=max_tokens)
        return self.replace_image_reference_in_markdown(markdown_path, image_path, result, title)

    def to_markdown(self, result: Dict[str, Any]) -> str:
        """将结构化 JSON 转成 markdown 文本，方便嵌入文档。"""
        result_type = str(result.get("type", "general")).lower()

        if result_type == ImageContentType.CODE.value:
            code_text = str(result.get("code") or result.get("ocr_text") or "")
            language = result.get("language") or "python"
            if not code_text:
                return ""
            return f"```{language}\n{code_text}\n```"

        if result_type == ImageContentType.FORMULA.value:
            latex = result.get("latex") or ""
            return f"$$\n{latex}\n$$"

        if result_type == ImageContentType.TABLE.value:
            markdown = result.get("markdown")
            if markdown:
                return markdown
            header = result.get("header") or []
            rows = result.get("rows") or []
            if not header and rows:
                header = [f"col{i + 1}" for i in range(len(rows[0]))]
            lines = ["| " + " | ".join(str(x) for x in header) + " |"]
            lines.append("| " + " | ".join(["---"] * len(header)) + " |")
            for row in rows:
                lines.append("| " + " | ".join(str(x) for x in row) + " |")
            return "\n".join(lines)

        if result_type in (ImageContentType.FLOWCHART.value, ImageContentType.STRUCTURE.value):
            mermaid = result.get("mermaid")
            description = result.get("description") or ""
            if mermaid and isinstance(mermaid, str) and mermaid.strip() and mermaid.strip().lower().startswith("graph "):
                return f"```mermaid\n{mermaid}\n```\n\n{description}".strip()
            if description:
                return f"```text\n{description}\n```".strip()
            if mermaid and isinstance(mermaid, str) and mermaid.strip():
                return f"```text\n{mermaid}\n```".strip()
            return "".strip()

        if result_type == ImageContentType.CURVE.value:
            summary = result.get("summary") or ""
            return summary

        if result_type == ImageContentType.OCR.value:
            text = result.get("text") or ""
            return text

        return json.dumps(result, ensure_ascii=False, indent=2)

    def _match_image_reference(self, markdown_text: str, image_path: Union[str, Path], md_path: Optional[Union[str, Path]] = None) -> Optional[re.Match[str]]:
        """寻找 markdown 文档中与某张图片对应的图片引用。"""
        candidates = set()
        image_abs = Path(image_path).resolve()
        for target in [str(image_path), str(image_abs)]:
            candidates.add(self._normalize_md_image_path(target))
            if md_path is not None:
                md_dir = Path(md_path).resolve().parent
                try:
                    rel = Path(os.path.relpath(str(image_abs), str(md_dir))).as_posix()
                    candidates.add(self._normalize_md_image_path(rel))
                except Exception:
                    pass

        pattern = r"!\[[^\]]*\]\((?P<target>[^)]+)\)"
        for match in re.finditer(pattern, markdown_text):
            target_raw = match.group("target").strip()
            target = target_raw.split()[0] if " " in target_raw else target_raw
            target_norm = self._normalize_md_image_path(target)
            if target_norm in candidates or target_norm.replace("./", "") in candidates:
                return match
        return None

    def replace_image_reference_in_markdown(
        self,
        markdown_path: Union[str, Path],
        image_path: Union[str, Path],
        result: Dict[str, Any],
        title: Optional[str] = None,
    ) -> str:
        """把 markdown 中对应图片引用替换为识别后的 markdown 内容。"""
        md_path = Path(markdown_path)
        image_path = Path(image_path)

        if not md_path.exists():
            raise FileNotFoundError(f"Markdown 文件不存在: {md_path}")
        if not image_path.exists():
            raise FileNotFoundError(f"图片文件不存在: {image_path}")

        markdown_text = md_path.read_text(encoding="utf-8")
        match = self._match_image_reference(markdown_text, image_path, md_path)
        if match is None:
            raise ValueError(
                f"在 Markdown 文件 '{md_path}' 中没有找到对应图片引用 '{image_path}'。 "
                "请确认图片路径在文档中已被插入，且路径相对关系正确。"
            )

        block = self.to_markdown(result)
        if not block.strip():
            raise ValueError("识别结果为空，无法写入 markdown。")
        if title:
            block = f"## {title}\n\n{block}"

        new_text = markdown_text[: match.start()] + block.strip() + markdown_text[match.end() :]
        md_path.write_text(new_text, encoding="utf-8")
        return new_text

    def append_to_markdown(
        self,
        markdown_path: Union[str, Path],
        result: Dict[str, Any],
        image_path: Optional[Union[str, Path]] = None,
        title: Optional[str] = None,
    ) -> str:
        """把识别结果写回指定的 markdown 文件。

        约定：
        - 如果是替换图片引用模式，必须同时传入 `markdown_path` 和 `image_path`
        - 该方法不再接受裸文本模式作为主要用法，避免误用时看不到目标 md 文件
        """
        if image_path is None:
            raise ValueError(
                "请同时传入 markdown_path 和 image_path，执行按图片引用替换的写回逻辑。"
                "例如：append_to_markdown('data/md/xxx.md', result, 'data/images/xxx.jpg')"
            )

        return self.replace_image_reference_in_markdown(markdown_path, image_path, result, title)


if __name__ == "__main__":
    # 示例用法：输入仅为 md 文档路径 + 图片路径，让模型自动分类并输出对应结构化内容
    extractor = VisionOutputConstraintModel()
    md_path = r"data\md\processed_test.md"
    image_path = r"data\images\第7章\code-0007.jpg"
    result = extractor.predict(
        image_path,
        custom_instruction="只提取代码和注释，忽略IDE界面，不要把菜单、标签页、状态栏等无关内容录进去。",
    )
    print(result)
    print("--- 写回 md ---")
    print(extractor.append_to_markdown(md_path, result, image_path=image_path))
