# 作品 Demo（project/）

一套可直接演示的“教材 PDF/MD → 教案 → 原生 PPT”工作台。演示数据与真实数据隔离在
`project/demo/data/`，不会污染仓库根目录的 `data/`。

## 目录

```
project/
├─ demo/
│  ├─ run_demo.ps1        # 一键：建演示账号+示例任务，启动服务
│  ├─ seed_demo.py        # 通过 Flask 测试客户端写入种子数据（不调用模型）
│  ├─ inputs/             # 示例教材（Markdown / PDF）
│  └─ screenshots/        # 关键界面截图
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

1. 注册/登录、账号数据隔离（换账号看不到对方的任务）。
2. 教材 PDF→MD：上传 `demo/inputs/教材示例.pdf`，查看解析、Markdown 渲染与下载。
3. MD→教案 Word：上传 `demo/inputs/教材示例.md`，生成教案、编辑、下载 DOCX。
4. 教案+教材→PPT：演示任务里已放好参考资料与模板，点“生成大纲 → 生成 PPT”，
   逐页预览、单页修改、确认并下载。
5. 用户信息弹窗退出登录；模型管理里添加自己的推理/视觉模型。

> 第 2–4 步的真实生成依赖模型服务与桌面 PowerPoint；没有配置模型时只能演示到建任务/大纲编辑。
> 截图见 `demo/screenshots/`，可直接用于答辩演示。
