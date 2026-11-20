# 知序工作台 · PPTNative 正式产品接入

## 用户授权与执行方式

用户要求在新对话独立完成正式功能接入和自动测试验收，不询问需求、技术选择或日常卡点，自行决定合理默认值并记录，持续解决报错。不要停在方案或等待用户批准设计。平台强制的权限审批仍须遵守。自动技术验收不等于用户亲自验收。

正式项目根目录为本文件所在的 production。所有新实现放在这里；父目录作为只读代码与证据来源。准备独立的本地 Git 仓库和可发布源码，但不设置远程、不推送；用户验收后另行指定 GitHub 位置。不要把实验目录整体复制进来，不上传密钥或教材素材。

## 前端已定稿，保留设计与交互

原型：E:/MyProjects/PPTNative/workbench.html；原始副本：reference/workbench.html。117512 字节，内联 CSS/原生严格 IIFE、零构建。先读完整文件，再在工作副本上接入；保留原始副本作为视觉对照。不要重设计。

保留暖米白/陶土橙/辅助绿、所有 CSS 变量、字体、留白、圆角阴影、1080/860/560 断点和 reduced-motion。三视图 view-drafts 默认、view-ppt 三步、view-wip 占位不变。保留页数6–40输入/滑块/chip联动；模板上传后禁用风格并显示跟随模板；模板拖拽替换移除；知识库搜索分类最多6份；本地参考多选；大纲标题编辑、排序、删除至少3页、加页、对话改稿；缩略图键盘导航、单页修改、返回大纲、确认下载、草稿恢复、面包屑。

state 尽量保留 step/count/style/template/refs/localRefs/outline/slides/current/generated/kbSelected。同名 slide 数据保留 type/kicker/title/lead/points，另加稳定页ID、previewUrl等真实字段。

## 必须做成真实接口的范围

- KB_DATA → GET /api/knowledge，持久化真实知识库条目，空库显示真实空状态，不种示例伪数据。
- DRAFTS和静态卡片 → GET /api/tasks；GET /api/tasks/:id恢复完整任务与版本。
- 本地参考上传返回真实 fileId，校验类型/大小/内容并安全存储；解析 doc/docx/pdf/md/txt，扫描PDF需有明确真实处理或错误状态。
- pptx/potx模板上传真实解析layoutCount，不再假装18版式；模板决定视觉。无模板时4种风格也必须具备实际可用的制作方案，不能只改标签。
- buildOutline/BASE_POOL/EXTRA_POOL → POST /api/tasks/:id/outline，使用现有模型接口结合教案教材生成真实可编辑大纲，支持保存和要求重做。当前任务明确扩展上游大纲能力，旧文档“只接Markdown”的范围不再限制本任务。
- runOutlineGeneration/runGeneration 的620ms假进度替换成后端真实阶段SSE或轮询，支持失败重试、刷新恢复和长任务。
- 真正生成PPT、逐页PowerPoint PNG预览、真实下载。自绘slideHTML/thumbArt不得冒充实际输出或以模板图冒充失败预览；未产出时诚实显示等待/失败。
- POST /api/tasks/:id/slides/:index/revise 对接版本化真实修改，使用稳定页ID和base_version，不能正则猜词做假修改。仅改标题时严格限定原标题，其他页XML/像素和正文备注不变；支持产品实际允许的其他修改意图并验证。
- 确认、下载、草稿列表、历史版本、取消/继续、冲突和错误提示贯通；失败候选不得晋升或伪装可交付。

## 已修复的制作链必须复用

先读父目录 README.md、HANDOFF.md、docs/PPTNative-网页接管修复记录.md、docs/PPTNative-V1.0用户验收.md、webapp/README.md，审计 pptnative/ 和 webapp/studio/ 的实现与测试。

唯一生产制作入口是 pptnative.pipeline.create/revise/confirm/resume/recheck，原生 split-native-v2、完整原版 PPT Master。不要接 drawing.compile_deck、历史EX/builder、txtpipe/txt2ppt。DeepSeek负责规划与制作、Qwen负责视觉，Agent编排/适配/修bug，不手工改终版幻灯片来过验收。

正式目录整理最小必要源码、依赖和许可证，消除对 experiments 的隐式运行依赖；PPT Master/外部运行时若不可随库分发，提供明确配置、校验、安装文档。保留Windows+桌面PowerPoint约束，不虚称跨平台。不要复制所有实验、任务和环境目录。

现有后端.env含可用的 myai.bupt.edu.cn 接口，模型 deepseek-v4-flash / qwen3.8-27b。只在服务端安全读取，不输出密钥、不提交.env。用户已明确授权原7页大纲、模板和6张教材原图使用现有接口完成回归。复用已有配置，不要求重新提供。发布提供.env.example与.gitignore。

保留已修复：OS级PowerPoint锁和端口实例锁；上传原字节SHA；真实版本预览；全素材/事实合同和空页守卫；严格标题修改作用域；按页可读性；可行几何诊断而非程序降低视觉目标或固定网格；已验收模板目录缓存；按失败页恢复；源页动画限制记录；QC v4视觉重大问题降级必须再次看真实渲染，不能凭XML/COM正常否定像素缺字。模型质检不是唯一验收依据。

## 只读基线和现有证据

原用户已确认基线 experiments/pptnative-native-test12-v2/versions/v001，7页/28事实/6原图/34母版版式部件。整个实验2862文件已核验未改，不允许写入或改状态。

最近真实新上传任务 runs/studio/20261119-093921-89f15d：v001/v002均QC v4为0重大0轻微，当前v002；7张真实PNG；v002只改第7页标题，前6页XML/像素一致，p7正文备注一致。p01/p05源动画关闭已记录，不声称动画全部保留。

另 runs/studio/20261119-093604-58cd9e 的v001是原成品导入副本；可作对照，不可冒充新制作证明。上述新版本尚无用户亲自验收。

旧项目最终99项测试、43项真实HTTP检查；证据 runs/studio-takeover/final-http-acceptance.json、fresh-seven-final-report.json、baseline-integrity.json；这些是历史基线，不得直接当新项目测试结果。自己的测试必须重新执行。

旧服务 http://127.0.0.1:7862/，不要停掉。正式项目使用独立端口、上传/任务/缓存路径；共享PowerPoint排他边界，避免并行COM冲突。

## 自动技术验收与交付

先审计文件、拟定可执行计划并直接实施，不停下来问用户。按需要使用技能，但用户明确要求不提问和自行推进，优先于技能中的常规设计确认。

执行真实UI回归，核对定稿样式和全部交互；验证真实上传、知识库引用、模型大纲生成/保存/重排/修改、生成进度、实际PPTX和PNG、单页反馈新版本、确认下载和草稿重开。覆盖刷新/重启持久化、失败不晋升、旧版冲突、文件路径边界、密钥不入前端、错误恢复与锁。检查完整样例的28事实/6图SHA/7页、母版版式、标题改稿未改页XML像素，实际查看逐页渲染。至少一次从原材料重新制作，不能用导入成品替代。另跑新上游教案教材到大纲再到PPT链。遇错修根因后重验，不能通过删断言、放宽质检、替换更简单样例宣称完成。

整理 README、启动脚本、配置样例、依赖锁定、许可证、API说明、部署边界、可复现验收命令和报告。检查源码/提交候选无密钥、上传资料、大型运行产物及实验依赖。允许初始化本地Git，不设remote不push。交付说明真实结果、证据路径、仍有限制，并明确“自动技术验收”与“用户验收”不同。

## 当前执行进度（2026-11-20）

正式源码已落地；用户已指定提交至GitHub Caspainnn/CourseTransform的feat/stage3-lesson2pptx分支。7863独立服务在运行，7862未停。真实文件解析/知识库、大纲、四风格源、队列恢复、版本PNG与单页反馈均已接入。当前Python回归54 passed/1 skipped；JS语法及延迟保存/上传跨任务隔离检查通过，doctor全通过。17项真实HTTP边界检查通过。

真实任务：
- c6f7d84183f44ced8fee82631027cd14：首次失败证据，保留failed，不当成通过结果。
- 9d4ad43159f6475a8f46e1ba7ea6acaa：原模板、7页大纲、6原图重新上传和制作。v001/v002均QC v4为0重大0轻微；7页/28事实/6原图SHA/34母版版式部件。v002仅改第7页标题，前6页XML与像素一致，第7页其余对象及正文备注保持。实际逐页查看；保持待用户审阅。
- 88535512c61342408c84e635047f70de：真实DOCX教案+教材MD，经明确授权后外发myai.bupt.edu.cn。真实模型生成7页、浏览器编辑/排序/增删/重做，完成v001原生新制作（7页/27事实/3原图/12母版版式部件）。人工发现第2页遗漏均匀采样，真实单页反馈v002已修复，其他6页XML像素一致；p2图位发生重排。仅此技术样例v002已API确认，不代表用户本人验收。

运行时显式配置在被忽略的local-config.json，读取已有.env与外部原版PPT Master/host；密钥、上传、成品、模型调用均不入Git。原用户基线2862文件SHA未变，报告artifacts/baseline-integrity.json。

浏览器限制：扩展缺少file URL权限，自动选择本地文件未通过；真实HTTP上传通过。实际浏览器下载被拒绝，未重试或通过其他方式下载；可独立核验HEAD元数据与本地成品，但不计浏览器下载通过。用户本人验收均待验，技术测试确认不代表个人接受课件。

已完成上游v002核对、单独技术确认、服务重启和23项持久化/PNG/HEAD检查。按用户最新指示完成粗查并提交目标分支；浏览器选文件和下载限制如上。
