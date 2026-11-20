# 作品 Demo

一套可本地演示的“教材 PDF/MD → 教案 Word → 原生 PPT”工作台。演示数据隔离在
`project/demo/data/`，不会污染仓库根目录的 `data/`。

## 目录

```
project/
├─ demo/
│  ├─ run_demo.ps1        # 一键：建演示账号 + 示例任务，启动服务
│  ├─ seed_demo.py        # 用 Flask 测试客户端写入种子数据（不调用模型）
│  ├─ inputs/             # 示例教材（Markdown / PDF）
│  └─ screenshots/        # 最新界面截图
└─ README.md
```

## 启动

前置：Windows 10/11、桌面版 PowerPoint、Python 3.11、已配置 `PPTMASTER_SKILL_DIR` 与模型 `.env`
（见 `readme/01-环境依赖与安装.md`）。

```powershell
powershell -ExecutionPolicy Bypass -File project/demo/run_demo.ps1
```

打开 `http://127.0.0.1:7873/`，账号 `demo / Demo12345!`。

## 可演示的完整链路

1. **账号体系**：注册 / 登录 / 退出；两个账号互相看不到对方的任务、文件与知识库。
2. **模型管理**：在“模型管理”中添加自己的 OpenAI 兼容推理/视觉模型，测试连接、按用途启用；
   未配置时使用服务端默认模型。
3. **教材 PDF→MD**：上传 `demo/inputs/教材示例.pdf`，查看解析结果、Markdown 渲染与 ZIP 下载。
4. **MD→教案 Word**：上传 `demo/inputs/教材示例.md`，生成教案、页内改字、下载 DOCX。
5. **教案+教材→PPT**：种子任务已放好参考资料与模板。生成大纲 → 生成 PPT → 逐页预览 →
   单页修改 → 需要时“取消任务” → 确认并下载。
6. **异常处理**：生成失败会保留候选并显示“继续制作”；取消后已生成页仍作为候选保留。

## 最新界面截图

| 截图 | 对应能力 |
|---|---|
| `screenshots/01-登录注册.png` | 用户名 + 密码登录 / 注册 |
| `screenshots/02-账户与数据隔离.png` | 登录后工作台与账号信息 |
| `screenshots/03-用户信息与退出.png` | 用户信息弹窗内直接退出登录 |
| `screenshots/04-模型管理.png` | 自定义推理 / 视觉模型表单 |
| `screenshots/05-单页修改加载态.png` | 单页修改时预览置灰 + 加载圈 |

> 第 2–5 步的真实生成依赖模型服务与桌面 PowerPoint；没有配置模型时只能演示到建任务/大纲编辑。
