把 **整章教材 Markdown + 一份单学时教案模板** 拆成若干学时，每个学时输出一份完整的 Word 教案。

## 1. 安装

```powershell
git clone https://github.com/<your-name>/jiaoan-agent.git
cd jiaoan-agent
python -m pip install -r requirements.txt
copy .env.example .env
```

## 2. 使用大模型 API

### 2.1 配置 `.env`

任何 OpenAI 兼容接口都可以：

```text
JIAOAN_API_KEY=sk-...
JIAOAN_BASE_URL=https://api.deepseek.com/v1
JIAOAN_MODEL=deepseek-chat

# 可选
JIAOAN_TIMEOUT=240
JIAOAN_MAX_CONTEXT=24000
```

学校/自建网关把 `BASE_URL` 换成 `https://your-school.example.com/v1`，模型名按网关实际名称填。

> **Qwen3.8 / Qwen3 / R1 等思考模型**：正文可能全花在 `reasoning_content` 上，返回 `content=null`。请设置：
>
> ```text
> JIAOAN_DISABLE_THINKING=true
> JIAOAN_MAX_TOKENS=8192
> ```

### 2.2 命令行生成

```powershell
python -m jiaoan run `
  --textbook examples\textbook_demo.md `
  --template examples\templates\教案模板_表格式.docx `
  --brief examples\brief_demo.json `
  --out out `
  --chapters ch01
```

不填 `--periods` 时，按本章实验个数自动拆学时；填了则以填的为准。

### 2.3 网页生成（推荐）

```powershell
python -m jiaoan web --port 8765 --open
```

也可以双击 [OPEN_WEB.cmd](OPEN_WEB.cmd)。

页面操作：

1. 上传教材 `.md`（可多章）；
2. 上传教案模板 `.docx`（可多份）；
3. 选择 **本机 Ollama / DeepSeek / 学校或自建 OpenAI 兼容接口**；
4. 填 Base URL、API Key、模型名，点“获取模型”或直接手填；
5. 点“测试连接”，通过后开始生成。

Key 只放在服务端内存中用于本次任务，不写日志、任务文件或下载文件；页面默认只监听 `127.0.0.1`，不要直接暴露到公网。

## 3. 学时怎么拆

先识别本章有几个 `实践 x-y` 实验，再决定学时：

| 本章实验数 | 拆分方式 |
| --- | --- |
| 0 个 | 按基础/实践最多 2 段 |
| 1 个 | 理论 1 学时 + 完整实验 1 学时，不拆上/下 |
| 2 个及以上 | 理论 1 学时 + 每个实验 1 学时 + 习题/总结 1 学时 |

例：

- 第3章有 3 个实验 → 5 份 Word；
- 第4章只有 `实践 4-1` → 2 份 Word；
- 第5章只有 `实践 5-1` → 2 份 Word。

上传模板后会自动读取课程名称、授课对象、单课时时长；模板写了“章内课时数/本章共 X 学时”就直接用，没写就按上面的实验数拆。

**每个学时一份独立 `.docx`，不是把多个学时塞进一个 Word，也不打 ZIP。**

## 4. 模板与版式

支持两类单学时模板，均原位替换，不重建栏目：

- **6 栏正文表格仿宋版**：6 个栏目、5 张表、4 列教学过程表（环节/时间/教师推进/学生任务与检查点）；
- **9 栏模板**：9 个栏目、5 张表、6 列教学过程表。

图片放在“案例教学实施过程”的正文段落里，图注紧随，不塞进表格；最大 10.5×8.0 cm 等比缩放；4 列流程表单元格约 50-60 字；取消模板段前分页，避免整页空白。

实例见 [demo_template/使用说明.md](demo_template/使用说明.md)。

## 5. 输出

每章输出目录包含：

```text
第01学时_xxx_教案.docx
第02学时_xxx_教案.docx
lesson_plan.json
质检报告.md
验收报告_第01学时.md
教材配图筛选说明.md
```

网页生成完成后直接列出“下载第1学时 / 第2学时 …”，点击即可下载 Word。

## 6. 目录

```text
jiaoan/       生成、模板、图片筛选、Web 服务
tests/        23 个单元测试
examples/     示例教材、模板、配图
demo_template/ 第3/4/5章真实拆分示例
docs/         设计文档
scripts/      Windows 启动脚本
```

## 7. 测试

```powershell
python -m unittest discover -s tests
```

要求全部通过（23 个）。
