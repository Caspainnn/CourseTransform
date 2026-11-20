# 本机API与复现

统一JSON错误：`{error, code}`；无效输入400，记录不存在404，过期/忙碌409。文件通过随机ID关联，不接受客户端任意本机路径。写请求拒绝跨Origin；无跨域开放。

| 方法 | 路径 | 输入/结果 |
|---|---|---|
| GET | /api/health | 不含密钥的运行依赖状态 |
| POST | /api/files | multipart file、kind=reference/template；返回fileId、SHA、真实版式数或文字/图片解析摘要 |
| GET | /api/knowledge | 可选q/type过滤，真实持久化列表；新库为空 |
| POST | /api/knowledge | fileId、type=jiaoan/jiaocai、可选title |
| GET/POST | /api/tasks | 读取列表/创建课题、count=6..40、style、template文件ID、refs知识ID、localRefs文件ID |
| GET/PUT | /api/tasks/:id | 恢复全部状态与版本/保存基础参数 |
| POST | /api/tasks/:id/outline | 可选instruction；真实模型异步大纲或改纲 |
| PUT | /api/tasks/:id/outline | pages、base_revision；保持稳定页面ID，3..40页 |
| POST | /api/tasks/:id/generate | 按已保存大纲原生制作 |
| POST | /api/tasks/:id/slides/:index/revise | 零起始index、pageId、base_version、text；异步版本化修改 |
| POST | /api/tasks/:id/confirm | base_version；当前且质检通过才确认 |
| POST | /api/tasks/:id/resume | 继续中断/失败制作或大纲 |
| POST | /api/tasks/:id/cancel | 仅取消排队任务 |
| GET | /api/tasks/:id?version=vNNN | 查看所选历史版本 |
| GET | /api/tasks/:id/versions/:vid/pages/:n.png | 1起始真实PNG，不回退模板 |
| GET | /api/tasks/:id/versions/:vid/download | 仅可交付版本的实际PPTX |
| HEAD | /api/tasks/:id/versions/:vid/download | 同版本权限检查，返回Content-Length、X-Content-SHA256、X-Version-Status；不传文件正文 |
| POST | /api/tasks/from-approved-outline | multipart template、outline、重复assets；仅上传源材料，不接受导入成品。记录原字节与路径映射，供已确认大纲工作流及回归 |

文件限制30MB/份，请求总量200MB；只接受明确格式并验证内容。DOCX原图按原字节提取；PDF图片经解析后登记输入SHA，制作保留这些输入图片字节。普通MD/TXT仅提取文字，不能通过相对路径读取服务机器文件；若需要图文制作，使用带内嵌图片的DOCX/PDF，或已确认大纲工作流同时上传引用图片。

真实阶段与完成页数随GET任务一起返回 `progress`；进度是阶段位置，不表示精确剩余时间。长任务持续轮询；失败不修改内核current/history。

## 可复现原材料回归

```powershell
python scripts/real_regression.py --source E:\MyProjects\PPTNative --start-only
# 输出新任务ID；不导入任何成品。必须显式提供只读源材料根目录。
python -m pptnative.verify data/tasks/<id>/kernel
```

原7页阅读校准可显式登记已由用户确认的同内容证据，只复制PNG和内容指纹、不创建任务/版本，也不作为新预览：

```powershell
python scripts/register_accepted_reference.py --job <user-confirmed-job> --version v001
```

新教案教材上游（会外发资料至配置的模型服务，须有资料授权）：

```powershell
python scripts/upstream_regression.py --lesson <lesson.docx> --textbook <textbook.md>
```

HTTP回归脚本使用直连本机连接，不读取系统代理；模型服务配置仍只在服务端。

```powershell
node scripts/test_frontend.cjs
python scripts/http_acceptance.py --failed-task <retained-failed-task-id>
python scripts/release_check.py
```

HEAD检查只证明元数据和可交付状态，不代表实际浏览器下载已通过。用户拒绝下载后不得以GET脚本绕过。
