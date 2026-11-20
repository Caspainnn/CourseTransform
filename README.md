# 知序工作台 · 教材转化流水线（feat/pipeline）

统一的本机教学工作台，把三个已验收 Demo 接入同一服务、同一视觉外壳：

1. **教材 PDF 转 MD** —— 文字版 PDF 逐章提取 Markdown + 原图；
2. **教材 MD 转教案 Word** —— 整章教材 + 单学时模板，按实验数拆学时，每学时一份原位套模板 DOCX；
3. **教案 + 教材转 PPT** —— 保留 stage3 的真实大纲、原生 PPT 制作、PowerPoint 逐页 PNG、版本修改与确认下载。

三步可独立使用，也可按 `PDF → MD → 教案 Word → PPT` 顺序交接。阶段产物在用户确认后进入下一阶段，保留原图、原字节 SHA 与来源任务。

> 本仓库只做本机单用户工作台，不是云服务；没有账号、多租户、支付或公网部署。

## 1. 分支与集成边界

| 阶段 | 来源分支 | 本分支处理 |
| --- | --- | --- |
| PDF → MD | `feat/stage1-pdf2md/reference` | stage1 参考实现依赖 MinerU 草稿；本分支用 PyMuPDF 实现文字层提取、切章、标题层级、原图导出，并用本机 RapidOCR 识别扫描页（失败时明确报错，可回退 MinerU 草稿）。 |
| MD → 教案 | `模板-->教案`（jiaoan-agent v0.2.0 提交包） | 教案引擎 `jiaoan/` 原样接入（23 项单测随仓库），新增 Flask 任务适配、持久化、交接与统一页面；不改模板渲染、拆学时、图筛与质检内核。 |
| 教案 → PPT | `feat/stage3-lesson2pptx` | 全量保留 `app.py` / `pptnative/` / `static/` 的基线实现，仅新增两个入口页面与“预选阶段产物”入口；未替换制作引擎，未改原生流程。 |

参考：`feat/stage2-md2lesson` 的 PyMuPDF 提取/切章思路被 PDF 阶段复用；其自研教案实现未作为本次教案内核（本任务点名“模板-->教案”演示）。详见 [docs/INTEGRATION.md](docs/INTEGRATION.md)。

## 2. 环境要求

- Windows 10/11，Python 3.11；
- 桌面 **PowerPoint**（PPT 制作与逐页 PNG 使用 COM）；旧 `.doc` 另需桌面 Word；
- 外部 **原版 PPT Master**（不随仓库分发）与 `PPTNATIVE_RUNTIME_HOST` 运行时；
- 教案/大纲真实生成需要一个 OpenAI 兼容模型接口；离线演示模式不需要 Key；
- 扫描版 PDF 由本机 RapidOCR 识别（`PDF_OCR=1`，默认开启；`PDF_OCR=0` 可关闭）；识别失败会明确报错，不伪造结果。

## 3. 安装

```powershell
python -m pip install -r requirements.lock.txt
Copy-Item .env.example .env                 # 填写服务端配置，不要提交
Copy-Item local-config.example.json local-config.json
python scripts/doctor.py
```

`local-config.json`（不提交）示例：

```json
{
  "PPTNATIVE_ENV_FILE": "C:\\YourProject\\.env",
  "PPTMASTER_SKILL_DIR": "C:\\Tools\\ppt-master\\skills\\ppt-master",
  "PPTNATIVE_RUNTIME_HOST": "C:\\Tools\\pptnative-host"
}
```

`PPTMASTER_SKILL_DIR` 必须指向完整原版发行内容（`scripts/`、`references/`、`workflows/`、`SKILL.md`），只有几个脚本无法工作。`doctor.py` 会逐项检查 Flask、PowerPoint、Word、原版 PPT Master 与模型配置。

`.env` 中与本项目相关的键：

```text
LLM_BASE_URL= / LLM_API_KEY= / LLM_MODEL= / LLM_OUTLINE_JSON_MODEL=   # PPT 阶段
JIAOAN_BASE_URL= / JIAOAN_API_KEY= / JIAOAN_MODEL=                    # 教案阶段
JIAOAN_TIMEOUT=900 / JIAOAN_MAX_TOKENS=8192 / JIAOAN_MAX_CONTEXT=24000
JIAOAN_DISABLE_THINKING=true
```

密钥只在服务端读取；`.env`、`local-config.json`、`data/`、上传教材、模型日志与生成产物均被 `.gitignore` 排除。

## 4. 启动

```powershell
.\start.ps1                 # 默认 http://127.0.0.1:7873/
.\start.ps1 -Port 7880      # 换端口
```

也可 `python run-local.py --port 7873`。服务只监听 `127.0.0.1`，跨 Origin 写请求会被拒绝。

## 5. 本机演示

### 5.1 三个功能独立使用

**① 教材 PDF 转 MD**

1. 打开“教材 PDF 转 MD”，上传 `examples/demo_textbook.pdf`（仓库内合成演示教材，含文字层与 2 张图）；
2. 点击“开始解析”，逐章查看 Markdown 预览、下载 MD 与原图 ZIP；
3. 扫描版 PDF 会自动用本机 RapidOCR 识别并生成 Markdown；识别失败会明确报错（可改用 MinerU 草稿），不会生成空文件冒充成功。

**② 教材 MD 转教案 Word**

1. 在“基础信息填写”里填学科、年级、课时、课型、学时数等简报；
2. 在“教材与模板”里点“从知识库选择”选 PDF→MD 产物或知识库 Markdown 教材，点“本地上传”补教案模板 `.docx` 与配图；
3. 模型固定使用服务端 `.env` 的默认配置，页面不展示接口与密钥；
4. 生成后按学时下载 DOCX、查看中间稿与质检/验收结果。

点击左侧栏底部“本机用户”，先弹出账户浮层（知识库份数、模型状态），再选择“用户信息 / 知识库 / 模型管理”打开对应详情弹窗；模型编辑暂为占位。

**③ 教案 + 教材转 PPT**

1. 从知识库选择教案/教材，或上传本地资料；
2. 填课题、页数、风格，生成并编辑大纲；
3. 原生制作后逐页查看真实 PowerPoint PNG，单页提出修改，确认后下载 PPTX。

### 5.2 贯通流程

1. PDF 页选择一章或“全部送入教案”；
2. 教案页会自动勾选对应教材产物，补模板与简报后生成；
3. 在“生成的教案”中选择一个 **质检通过** 的学时，点击“送入 PPT”；
4. 工作台自动把该 DOCX（含嵌入原图）与教材 MD+原图注册进知识库，并预选 3 份资料、预填课题；
5. 确认页数/风格 → 生成大纲 → 生成 PPT → 逐页审阅 → 确认下载。

失败学时（`quality_failed` / `failed`）可以下载查看，但**禁止**送入 PPT；接口返回 `409 E_QUALITY_FAILED`。

## 6. 产物、持久化与恢复

```text
data/
├── pipeline/pdf/<job>/        原始 PDF、out/*.md、out/images、job.json、日志
├── pipeline/lesson/<job>/     input/（教材、模板、图片）、out/（每学时 DOCX + JSON + 报告）
├── artifacts/<id>/            artifact.json + 不可变成品（md/images/docx）
├── files/<id>/                知识库文件原字节 + file.json（SHA、解析文字/原图）
├── knowledge/<id>/            entry.json（类型、来源 origin）
└── tasks/<id>/                PPT 基线任务与版本
```

- 每个 PDF 章节/教案学时登记为不可变 artifact，记录来源任务、原始 PDF 名称与 SHA、章节、页、字数、图片数、创建时间；
- 交接时复制所需文件，不依赖用户重复下载上传；知识库条目保留 `origin`；
- 刷新页面可用 `?feature=pdf&pdf=<id>` / `?feature=word&lesson=<id>` 恢复查看；
- 服务重启把 `queued/running` 的 PDF/教案任务标为 `interrupted`，输入保留，可点“重试”重新执行；PPT 任务走原有 `resume`；
- 重试生成新任务/新 artifact，不覆盖已有成功版本；失败候选不会被晋升。

## 7. 测试与本次实测

```powershell
python -m pytest -q          # 86 passed, 1 skipped（2026-11-20）
python scripts/doctor.py
node --check static/workbench.js
node --check static/pipeline.js
python scripts/pipeline_smoke.py --pdf examples/demo_textbook.pdf --template examples/templates/教案模板_表格式.docx
```

真实本机实测（2026-11-20，`http://127.0.0.1:7873/`）：

- PDF→MD：`demo_textbook.pdf` 3 页 → 2 章、2 张原图；TOC 不进入正文；产出可下载 MD/图片 ZIP；
- 解析交互：开始解析后展示 5 步进度（检测文字层/OCR/切章/导出原图/生成 MD）、总进度、OCR 页码与实时日志，可转后台；完成后自动进入独立结果页，支持 Markdown 渲染/纯文本切换与章节编辑；编辑保存为新修订（原版本保留），下载与送入教案均使用最新修订；
- 扫描版 PDF→MD：`第6章.pdf` 13 页（无文字层）→ 本机 RapidOCR 13 页，均值置信度 0.966，114 秒 → 1 章 16,139 字符；原失败任务直接重试成功，页面显示 OCR 页数与置信度；
- 教案：真实接口 `deepseek-v4-flash`，2 章自动拆 4 个学时；2 份 `ok_with_warnings`、2 份 `quality_failed`（质检原因：引用不足，未隐藏），成功学时 DOCX 真实可打开；
- 交接：选择质检通过的“第一章”学时 → 知识库得到 1 份教案 DOCX + 2 份教材 MD（各带原图），PPT 页自动预选 3 份资料并预填课题；
- PPT：真实大纲 8 页 → 原生制作 v001（8 页）→ 8 张真实 PNG → 用户确认 → 下载 `PPTNative-v001.pptx`（102,660 字节，8 页，`python-pptx` 可打开）；
- 3 个页面均正常进入，导航/配色/断点沿用 stage3；PPT 原有 pytest 回归全部通过。

可复查的任务目录：`data/tasks/413dc81c848244fb92c851bb6396ca81`（PPT v001）、`data/pipeline/lesson/7271292eeb83`（真实教案）、`data/pipeline/pdf/5e303d75e775`（扫描版 OCR）。这些运行数据不提交，换机器需重新生成。

## 8. 已知限制

- **扫描版 PDF 使用本机 OCR**：RapidOCR 对扫描页逐页识别，13 页约 2 分钟；版面、表格、公式的精度有限，侧栏标签与图内文字可能与正文混排，重要内容需对照原图核对。需要更好版面时可用 MinerU 生成草稿后上传。`PDF_OCR=0` 可关闭本机 OCR。
- **stage1 视觉回写未接入界面**：stage1 参考实现的“逐图视觉识别并回写代码/公式/表格”需要额外的视觉模型服务；本次 PDF 阶段只提取文字与原始图片（保留引用），不自动转写图片内容。需要时可按 stage1 的 `code/vision_output_constraint/` 文档另行配置。
- **PPT 依赖不可分发的原版 PPT Master / host 运行时**：必须按第 2、3 节配置，否则 `/api/health` 的 `nativeRuntime` 为 false，制作会失败。
- **仅 Windows + 桌面 Office**：PowerPoint COM 不可用时无法制作/预览 PNG；不做跨平台承诺。
- **教案质量取决于模型与材料**：短样例可能触发 `quality_failed`（引用/覆盖不足），这是引擎真实质检结果，页面不会洗成成功。
- **本机单用户**：无登录/多租户；不要直接暴露到公网。
- **用户验收**：本次完成的是本机技术演示与自动回归，用户本人对教案/课件的教学验收仍需另行进行。

## 9. 目录

```text
app.py                  统一 Flask 服务（PPT API + 阶段 API 注册）
documents.py            文档解析/上传校验（自 app.py 抽出，供各阶段复用）
pipeline/               阶段实现：pdf2md / lesson_jobs / artifacts / web
jiaoan/                 教案引擎（来自“模板-->教案”提交包，原样保留）
pptnative/ webapp/      PPT 原生制作、版本、锁（来自 stage3）
static/                 工作台 HTML + 原生 JS（workbench.js 基线，pipeline.js 新页面）
examples/               合成演示教材 PDF、教材 md、模板、图
tests/                  86 项测试（含 9 项阶段集成测试）
docs/                   原 stage3 文档 + docs/INTEGRATION.md
scripts/                doctor、真实/冒烟回归、演示 PDF 生成
```

许可证与第三方边界见 [LICENSE.md](LICENSE.md)、[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。