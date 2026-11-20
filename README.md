# 知序工作台 · PPTNative

本机教案教材课件工作台：真实资料解析与知识库、模型大纲、原生可编辑PPT制作、PowerPoint逐页PNG、版本修改、确认与下载。界面基于已经定稿的 `reference/workbench.html`，保留原布局与断点。

## 环境与启动

仅支持 Windows + 桌面 PowerPoint + Python 3.11。旧 `.doc` 另需桌面 Word；扫描PDF当前明确返回 `E_OCR_REQUIRED`，不冒充已识别。所有接口仅监听本机，不是互联网多用户托管产品。

```powershell
python -m pip install -r requirements.lock.txt
Copy-Item .env.example .env
# 填写服务端模型配置；不要提交.env
python scripts/doctor.py
.\start.ps1                     # http://127.0.0.1:7863/
```

完整原版 PPT Master 必须单独合法获取，配置 `PPTMASTER_SKILL_DIR`。需要其 `scripts/`、`references/`、`workflows/`、SKILL.md 与归属检查所需的完整发行内容，不能只复制几个脚本。通过 `PPTNATIVE_RUNTIME_HOST` 指定兼容 Python 依赖所在 host，其 `python/` 用于上游子进程。新制作使用 split-native-v2 API编排，不要求Codex参与。历史工具host兼容路径如使用OpenCode，另设 `PPTNATIVE_OPENCODE`。

也可建立不提交的 `local-config.json`，仅列 `PPTNATIVE_ENV_FILE`、`PPTMASTER_SKILL_DIR`、`PPTNATIVE_RUNTIME_HOST`、可选 `PPTNATIVE_OPENCODE` 的显式绝对路径。当前机器已配置读取父项目已有.env与原版外部发行包；源码没有实验目录默认路径。换机器须重新配置，不会自动搜索历史实验。

## 核心目录

```
app.py                 任务/文件/知识库API、持久化与工作队列
static/                正式工作台HTML/原生JS
pptnative/             原生制作内核、严格局部修改与QC v4
webapp/runtime_lock.py 与旧服务共享的OS级PowerPoint排他锁
reference/             冻结前端原始参考
scripts/               环境校验、真实回归与前端转换记录
tests/                 本项目回归检查
docs/                  计划、API、验收与问题记录
data/                  任务、上传与知识库（忽略提交）
.pptnative-runtime/    本机模板/已验收阅读证据缓存（忽略提交）
artifacts/             本机验收报告（忽略提交）
```

## 工作与恢复

新建任务后表单与大纲保存到磁盘；模型与制作通过任务队列运行，页面轮询真实后端状态与阶段。刷新可恢复任务。服务重启把未完任务标为中断，继续按钮调用原生恢复入口，不把失败候选升为当前版。已经开始的PowerPoint任务不支持强制取消，排队任务可取消。修改大纲后重新生成会建立独立制作任务，保留原版本历史。

版本操作携带 `base_version` 与稳定页ID；标题修改走原有严格原框替换，其他单页修改由模型执行并保留事实/原图合同。预览仅来自所选版真实PowerPoint PNG，缺失显示等待；失败版禁止交付。确认是用户操作，自动质检通过不会代替用户确认。

所有新任务产物保存在本项目data中。旧7862服务保持独立；两个项目共享操作系统级PowerPoint锁，避免COM冲突。密钥只在后端读取，上传材料、调用记录、成品和环境文件不进入Git。

## 验证与发布

```powershell
python -m pytest -q
node --check static/workbench.js
python scripts/doctor.py
```

真实模型回归命令见 [API与验收说明](docs/API.md)；当前结果见 [技术验收](docs/技术验收.md)、[问题记录](docs/问题与解决方案.md)和 [HANDOFF](HANDOFF.md)。真实制作有模型随机性，失败证据和恢复历史均保留，不能把历史基线测试当本项目结果。

本轮源码按用户指定提交至 CourseTransform 的 feat/stage3-lesson2pptx 分支。用户验收仍独立记录。依赖与发行边界见 [第三方说明](THIRD_PARTY_NOTICES.md)。第一方源码尚未由所有者指定开放源码许可证，见 LICENSE.md。
