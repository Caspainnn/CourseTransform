# CourseTransform · stage2（教材 Markdown → 教案 docx）

> 仓库：`Caspainnn/CourseTransform`，分支 `feat/stage2-md2lesson`
> 上游：stage1（`feat/stage1-pdf2md/reference`）产出"每章一个 md"；下游：stage3（`feat/stage3-lesson2pptx`）。

## 任务

输入两份材料：
1. **教材 Markdown**（stage1 产出，如"第2章.md"，含 `![](../images/第N章/image-00XX.jpg)` 图片引用与"图2-1"题注）；
2. **教学设计模板 docx**（九个板块：课程基本信息(表格)、教学目标、重点难点(表格)、学情分析、教学准备、教学过程(表格)、板书设计(表格)、作业与思考题、教学反思(表格)）。

用大模型把教材内容填入模板，产出完整教案 docx。AI 生成结果经"AI 验证回环"检查（板块齐全/时间合计45分钟/内容有教材依据/中文与术语一致），不合格自动带意见重生成。

## 目录结构

```
├── src/
│   ├── utils.py            # 日志/目录/文本读写
│   ├── config.py           # 默认配置 + YAML 深合并加载
│   ├── llm_client.py       # LLMClient(OpenAI兼容) + MockLLMClient(离线演示)
│   ├── template_parser.py  # docx 模板 → Block 列表（按 body 实际顺序遍历段落与表格）
│   ├── intermediate.py     # 教案 markdown 中间稿（CTBLOCK 标记）序列化/解析
│   ├── lesson_generator.py # system/block prompt 构造 + 逐块填充（整表生成）
│   ├── validator.py        # AI 裁判验证回环（生成→验证→refine，最多 N 轮）
│   ├── export_docx.py      # 中间稿回填模板 docx（保留字体/表格样式，w:eastAsia）
│   ├── pdf_parser.py       # stage1：PDF → 每章 md + images/
│   ├── vision_client.py    # stage1 视觉模型客户端（图片描述）
│   └── main.py             # argparse 一键流程（stage1/stage2/pipeline）
├── tests/                  # pytest 单测（FakeLLM，无需网络）
├── scripts/make_fixture_materials.py  # 仿真材料生成器
├── demo_materials/         # 仿真材料（《人工智能安全》第2章 GAN md + GAN 九板块模板 docx）
└── config.yaml             # 模型/流程/验证全部可配置
```

## 一行命令运行

```bash
# stage2：教材 md + 模板 docx → 教案 docx（真实模型，需先 export LLM_API_KEY=...）
python -m src.main stage2 --knowledge "教材md版/md/第2章.md" \
  --template "第03学时_GAN生成sin曲线实践_教学设计_模板版.docx" --out output/stage2

# 离线演示模式（无需 API Key/网络，使用确定性假模型，便于流程验证与排错演示）
python -m src.main stage2 --knowledge demo_materials/教材md版/md/第2章.md \
  --template demo_materials/第03学时_GAN生成sin曲线实践_教学设计_模板版.docx \
  --out output/stage2_mock --mock

# stage1：教材 PDF → 每章 md
python -m src.main stage1 --pdf 教材.pdf --out output/stage1

# pipeline：stage1 + stage2 一键串联
python -m src.main pipeline --pdf 教材.pdf --template 模板.docx --out output
```

## 技术方案要点

1. **模板解析**：遍历 `document.element.body` 的 `CT_P`/`CT_Tbl` 交错子节点，块顺序与表格结构不丢失。
2. **表格填充——整表一次生成**：教学过程表的行列有强关联（时间合计=45分钟），整表输出 Markdown 表格 + 代码侧行列校验，不符则带纠错信息重试一次，仍不符按原结构对齐。
3. **中间稿解耦**：每块加 `<!-- CTBLOCK ... -->` 标记，生成与 docx 回填解耦，中间结果可人工审查。
4. **模板回填**：以模板为底稿逐块替换（段落换 run 文本、表格逐单元格填值），保留仿宋字体与表格边框；东亚字体经 `w:eastAsia` 显式设置。
5. **AI 验证回环**：裁判模型使用独立 system prompt、要求引用教材原文证据；fail 则带意见 refine，块标记损坏的 refine 结果直接丢弃；最多 N 轮（`stage2.max_validation_rounds`）。

## 测试

```bash
python -m pytest tests -q
```

## 仿真材料说明

`demo_materials/` 由 `scripts/make_fixture_materials.py` 生成，用于在真实材料（教材 PDF 与正式模板 docx）到位前跑通全流程：
- `教材md版/md/第2章.md`：模仿《人工智能安全：原理与实践》第2章 GAN 内容的样例 md（含图片引用与"图2-1"题注）；
- `第03学时_GAN生成sin曲线实践_教学设计_模板版.docx`：九板块教学设计模板（含待填横线"教师：____"）。

真实材料到位后直接替换路径即可，无需改代码（所有路径/模型配置都在 `config.yaml`）。
