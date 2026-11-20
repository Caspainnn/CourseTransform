"""Create the demo account, upload sample material and draft a PPT task.

Run through the Flask test client so no browser or model call is needed.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

DEMO_USER = "demo"
DEMO_PASS = "Demo12345!"


def main():
    data = Path(os.environ.get("PPTNATIVE_DATA_DIR") or HERE / "data").resolve()
    data.mkdir(parents=True, exist_ok=True)
    from app import create_app, style_template
    app = create_app(data)
    client = app.test_client()

    registered = client.post("/api/auth/register", json={"username": DEMO_USER, "password": DEMO_PASS})
    if registered.status_code != 201:
        login = client.post("/api/auth/login", json={"username": DEMO_USER, "password": DEMO_PASS})
        if login.status_code != 200:
            raise SystemExit(f"演示账号创建失败：{registered.get_json()}")
    print(f"演示账号：{DEMO_USER} / {DEMO_PASS}")

    if client.get("/api/tasks").get_json():
        print("演示数据已存在，跳过种子数据创建")
        return

    template = HERE / "data" / "演示模板.pptx"
    template.parent.mkdir(parents=True, exist_ok=True)
    if not template.exists():
        style_template("academic", template)
    with template.open("rb") as handle:
        up = client.post("/api/files", data={"kind": "template", "file": (handle, "演示模板.pptx")},
                         content_type="multipart/form-data")
    template_id = up.get_json()["fileId"] if up.status_code == 201 else None

    ref_ids = []
    for name, kind in (("教材示例.md", "md"), ("教材示例.pdf", "pdf")):
        path = HERE / "inputs" / name
        with path.open("rb") as handle:
            response = client.post("/api/files", data={"file": (handle, name)},
                                   content_type="multipart/form-data")
        if response.status_code == 201:
            ref_ids.append(response.get_json()["fileId"])

    body = {"topic": "走进人工智能", "teacher": "演示教师", "audience": "高一", "duration": "45 分钟",
            "count": 8, "style": "academic", "localRefs": ref_ids}
    if template_id:
        body["template"] = template_id
    task = client.post("/api/tasks", json=body)
    print("演示任务已创建：", task.get_json().get("id") if task.status_code == 201 else task.get_json())


if __name__ == "__main__":
    main()
