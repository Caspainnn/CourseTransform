import base64
from openai import OpenAI
from PIL import Image
import io

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="EMPTY"
)

image_path = r"data\images\test.jpg"

# 加载+缩放图片，减小显存压力
image = Image.open(image_path)
max_edge = 1024
if max(image.size) > max_edge:
    ratio = max_edge / max(image.size)
    image = image.resize((int(image.width*ratio), int(image.height*ratio)))

buf = io.BytesIO()
image.save(buf, format="JPEG", quality=80)
image_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")

try:
    response = client.chat.completions.create(
        model="./models/Qwen3-VL-4B-Instruct", # 改成后端注册的模型名，不要文件路径
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "现在你需要进行ocr识别，仅返回ocr识别内容，其他不要输出"},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
                    }
                ]
            }
        ],
        timeout=120
    )
    print(response.choices[0].message.content)
except Exception as e:
    print(f"请求异常: {e}")
