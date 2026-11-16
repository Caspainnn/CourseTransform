import base64
from pathlib import Path

from src.llm_client import LLMClient
from src.utils import setup_logging


class VisionClient(LLMClient):
    """视觉模型客户端（stage1 图片描述用）【等教程后按实际服务信息调整】。"""

    def describe_image(self, image_path: str | Path, prompt: str | None = None) -> str:
        image_path = Path(image_path)
        suffix = image_path.suffix.lstrip(".").lower() or "jpg"
        mime = "image/jpeg" if suffix in ("jpg", "jpeg") else f"image/{suffix}"
        data = base64.b64encode(image_path.read_bytes()).decode("utf-8")
        prompt = prompt or self.config.get("prompt", "请用中文描述这张图片的内容。")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}},
                ],
            }
        ]
        try:
            return self.chat(messages=messages)
        except Exception as exc:
            self.logger.warning(f"图片描述失败 {image_path.name}: {exc}")
            return ""
