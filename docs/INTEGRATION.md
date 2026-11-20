# 三阶段集成计划与边界（feat/pipeline）

本文件记录实施前的代码审计结论与实际集成方式。以代码为准，不以分支名猜测能力。

## 1. 审计结论

| 阶段 | 实际源码 | 真实能力 | 输入 → 输出 | 外部依赖 / 限制 |
| --- | --- | --- | --- | --- |
| PDF → MD | `feat/stage1-pdf2md/reference`（`code/pipeline/MarkdownPipeline.py` 等） | 对 **MinerU 产出的 Markdown 草稿** 做 Base64 图片落地、标题层级整理、视觉模型回写；本身不解析 PDF | MinerU 草稿 md → 整理后 md + images | MinerU CLI、视觉模型服务；扫描版必须先用 MinerU |
| 教材/模板 → 教案 | `模板-->教案`（jiaoan-agent v0.2.0，`jiaoan/`） | 整章教材 md + 单学时模板 docx → 按实验数拆学时，每学时一份原位套模板 DOCX；含图片筛选/OCR、质检、验收、离线模式 | md + docx(+images) → N 份 DOCX + lesson_plan.json + 质检/验收报告 | python-docx、OpenCV、RapidOCR；真实模型需 OpenAI 兼容接口 |
| 教案 → PPT | `feat/stage3-lesson2pptx`（`app.py` + `pptnative/`） | 大纲/素材解析、原生 PPT 制作、PowerPoint 逐页 PNG、版本修改、确认下载 | 教案/教材资料 +（可选）PPT 模板 → PPTX + 逐页 PNG | Windows + 桌面 PowerPoint + 外部 PPT Master（`PPTMASTER_SKILL_DIR`）+ host 运行时 + 模型服务 |

补充：`feat/stage2-md2lesson` 是 CourseTransform 自研的第二阶段实现（PyMuPDF + 9 板块模板），其 PDF 解析代码被本次 PDF 阶段参考（文本层提取、切章、图片导出、标题规范化）。本任务点名的“模板-->教案”演示以其提交包（jiaoan-agent）为准，故教案内核采用 jiaoan。

## 2. 集成方式

- 基线：`feat/stage3-lesson2pptx` 全量保留（工作台外壳、PPT 内核、版本/预览/确认/锁），不做重写。
- 新增：
  - `pipeline/pdf2md.py`：真实 PDF 文本层解析（PyMuPDF）+ 切章 + 标题规范化 + 图片导出；扫描页优先调用本机 MinerU（`mineru_adapter.py`，按 `# 第N章` 拆分 Markdown 并归一化插图），未安装或失败时回退 RapidOCR。OCR 不可用/失败/超过 200 页时明确报错（`E_OCR_UNAVAILABLE`/`E_OCR_FAILED`/`E_OCR_TOO_LARGE`），绝不伪造文字，可回退 MinerU 草稿。
  - `jiaoan/`：教案内核原样接入，23 项单测随仓库。
  - `pipeline/lesson_jobs.py`：把 jiaoan 的 Web 任务模型适配到统一 Flask 服务；密钥只进内存，任务快照不落盘密钥。
  - `pipeline/artifacts.py` + `pipeline/handoff.py`：阶段产物登记与交接（md/图片/原始 PDF/DOCX 的字节、SHA、来源任务、章节、时间）。
  - 两个新页面 `#view-pdf`、`#view-word`（沿用工作台设计令牌与组件）；PPT 页面仅增加“从参数预选资料”的入口，不改变原有流程。
- 阶段交接：
  - PDF → 教案：服务端登记 artifact（md + images + 原始 PDF 身份/SHA），教案页按 artifact 复制教材 md 与图片到任务目录，图片文件名与 md 引用保持一致。
  - 教案 → PPT：把该学时 DOCX（含真实嵌入图片）与教材原始 PDF 注册为工作台知识库条目（保留原字节、SHA、来源），返回知识 ID，PPT 页预选后仍由用户确认大纲、制作、逐页审阅与确认。
- 持久化：PDF/教案任务写 `data/pipeline/**/job.json`；重启把未完成任务标为 `interrupted`，可重试（重新执行），不把失败候选当成功。
- 并发：PDF/教案任务使用独立工作目录与线程；PPT 继续使用原有队列与 OS 级 PowerPoint 排他锁。

## 3. 不改变的部分

- PPT 的制作入口、原生 split-native-v2 流程、真实 PNG 预览、版本/标题作用域、质检、确认下载、失败恢复与排他锁不改。
- 不使用网页自绘预览冒充 PPT；不把历史验收结果当作本次结果。

## 4. 实施结果（2026-11-20）

- `app.py` 保持 PPT 路由与队列不变，在 `create_app()` 末尾注册 `pipeline/web.py` 蓝图；`documents.py` 仅把原 `parse_document/checked_zip/decode/Problem` 抽为共享模块，函数与错误码未改。
- `pipeline/pdf2md.py`：PyMuPDF 文字层提取、目录行剔除、章内页首截断、x.y/x.y.z 标题层级、图片导出与题注插入；扫描页 RapidOCR 识别（跳过错位页面图片），重复页眉不拆章。
- `pipeline/lesson_jobs.py`：复用 `jiaoan.web._run_job`，保留 jiaoan 的模型配置、拆学时、质检与报告；任务快照写入 `job.json`（排除 Key），重启恢复为 `interrupted`，重试复制输入后新建任务。
- `pipeline/mineru_adapter.py`：检测 `mineru-kit` / `mineru` / `magic-pdf` 或 `MINERU_COMMAND`，执行解析、挑选 Markdown、复制并重写图片引用；`PDF_OCR_ENGINE=auto|mineru|rapidocr` 控制严格/回退模式，严格模式返回 `E_MINERU_UNAVAILABLE` / `E_MINERU_FAILED`。
- 交付：`GET /api/pipeline/pdf/jobs/<id>/bundle` 打包 Markdown、全部原图与 `manifest.json`（引擎、页数、章节与 SHA）；结果页“下载 ZIP”直接交付（工具栏单按钮，无需顶部重复入口），用户可自行保存，服务端不做长期资料库承诺。
- 教案版式：九段式模板按原 DOCX 克隆样式/表格/页边距；教材图不足 2 张时自动生成教学流程与知识结构示意图，并把生成图写入 `plan.figures` 以满足质检。
- 教案交互：生成中展示 5 步进度动画（解析教材/拆分学时/生成内容/质检验收/打包 Word），可转后台；`GET .../docx/<index>/html` 输出带表格/图片/块 ID 的 Word 渲染页；结果页直接 contenteditable 改字，`POST .../docx/<index>/edit` 保存为 `_rN.docx` 修订。
- 交互：解析中展示 5 步进度、总进度与 OCR 页码，可转后台继续；结果页支持 Markdown 渲染/纯文本切换。`POST /api/pipeline/pdf/jobs/<id>/chapters/<index>` 把编辑保存为新的 pdf2md artifact 修订（原 artifact 不变），`GET .../images/<index>/<name>` 提供渲染所需原图；预览、下载与送入教案自动使用最新修订。
- `pipeline/artifacts.py` + `pipeline/web.py`：PDF 章节/教案学时登记不可变 artifact；交接把学时 DOCX 与教材 MD+原图写入知识库，保留 `origin` 与 SHA；质检未通过学时禁止交接（409）。
- `static/index.html` + `static/pipeline.js`：新增 `#view-pdf`、`#view-word`，沿用 stage3 设计令牌；`workbench.js` 仅增加视图隐藏、`showPptWithRefs` 与深链接入口，不改变 PPT 三步流程。
- 教案页按反馈调整：模型 API 不再展示，固定使用服务端 `.env`；基础信息前置；教材/模板改为“参考资料”卡（知识库选择 + 本地上传 + 图注），知识库教材以 Markdown 复用，交接时保留原知识条目。左侧“本机用户”先打开账户浮层，三个入口再分别打开用户信息、知识库、模型管理详情弹窗（模型编辑为占位）。
- 测试：`91 passed, 1 skipped`（含 14 项新增阶段集成测试；stage3 原 54 项与 jiaoan 23 项全部保留）。
- 真实运行：PDF 3 页 → 2 章；真实模型教案 4 学时（2 通过、2 质检失败且不隐藏）；交接 3 份资料；PPT 8 页 v001 真实生成、8 张 PNG、确认下载 102,660 字节。
- 已知限制与用户验收边界见 README 第 8 节。