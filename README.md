# 个人数字工作室 · 浏览器首页

把「个人数字工作室」做成浏览器新标签页：一屏之内看到天气、音乐、作品截图、项目和日志。

**仓库地址**：https://github.com/yuban211/personal-digital-studio
**线上站点**：https://yuban211.duckdns.org:59684/studio/

---

## 内容从哪来

站点的项目和文章都存在 SQLite 里，同时在 `content/` 目录留了一份 Markdown 源文件。

**后台可以反过来从 GitHub 拉取**：

> 后台 → 仓库同步 → 填仓库名和分支 → 「检查并同步到站点」

同步按 slug 匹配：仓库里有库里没有就新增，两边都有就更新（**仓库为准**），
库里独有���**不会删**——避免一次误操作清空全部内容。

仓库里这样放：

```
content/
├── projects/
│   └── garden-action.md
└── posts/
    └── my-note.md
```

Markdown 文件开头可以写 front-matter：

```markdown
---
title: 标题
slug: url-friendly-slug
summary: 一句话摘要
status: 已上线
tags: 标签1, 标签2
date: 2026-01-01
draft: false
---
```

没写 `summary` 就取正文第一段，没写 `slug` 就从标题生成。

**同步只读取 Markdown 文本，不会执行仓库里的任何代码。**​

---

## 目录结构

```
浏览器首页/
├── content/                   ★ 仓库同步的内容源（Markdown）
│   ├── projects/              项目，每个文件一个项目
│   └── posts/                 日志，每个文件一篇文章
│
├── browser-extension/        浏览器首页扩展（Chrome Manifest V3）
│   ├── manifest.json         MV3 清单，chrome_url_overrides.newtab
│   ├── newtab.html           页面骨架
│   ├── newtab.css            样式（沿用站点设计令牌）
│   ├── newtab.js             逻辑（数据拉取 + 天气场景引擎 + 播放器 + 轮播）
│   └── icons/                16 / 48 / 128 px 图标
│
├── server/                   个人数字工作室服务端（FastAPI + Jinja2 + SQLite）
│   ├── app/
│   │   ├── main.py           路由入口
│   │   ├── newtab_api.py     ★ 扩展专用只读 JSON 接口
│   │   ├── repo_sync.py      ★ 从 GitHub 拉取 Markdown 并解析
│   │   ├── repo_routes.py    ★ 后台同步路由与数据库写入
│   │   ├── cache_layer.py    分层缓存
│   │   ├── weather_mod.py    天气适配层
│   │   ├── security.py       scrypt 密码 + HMAC 会话 + CSRF
│   │   ├── static/           20 个静态文件
│   │   └── templates/        Jinja2 模板（含 admin 子目录）
│   ├── instance/
│   │   ├── live.db           SQLite 数据库
│   │   └── media/            媒体库（上传的图片）
│   ├── tests/                pytest 测试
│   ├── _vendor/              离线依赖库（服务器无法联网，随包上传）
│   ├── run.py                启动入口
│   ├── create_admin.py       创建后台账号
│   ├── requirements.txt
│   └── .env.example
│
├── credentials/              ★ 敏感信息，勿提交版本库
│   ├── CREDENTIALS.txt
│   ├── studio.env            环境变量（含 APP_SECRET、天气密钥）
│   └── admin.json            后台账号密码哈希
│
└── docs/
    └── NEWTAB_EXTENSION.md   技术方案与实测记录
```

---

## 快速开始

### 服务端

```sh
cd server
pip install -r requirements.txt
export STUDIO_DB=instance/live.db
export STUDIO_MEDIA_DIR=instance/media
export APP_SECRET="$(python -c 'import secrets;print(secrets.token_hex(32))')"
export APP_PREFIX=/studio
python run.py
```

访问 `http://127.0.0.1:8000/studio/`

创建后台账号：

```sh
python create_admin.py
```

### 浏览器扩展

**开发者模式安装**

1. 打开 `chrome://extensions/`
2. 右上角开启「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择 `browser-extension/` 目录
5. 新开标签页即可

**技术路径**（已实测验证）

Chrome Manifest V3 扩展 + `chrome_url_overrides.newtab`。
在星愿浏览器（Chromium 128 内核）实测：加载扩展时浏览器弹出
「此网页被扩展程序 "Studio NewTab Probe" 更改了」确认框，授权后新标签页即被接管。

详见 `docs/NEWTAB_EXTENSION.md`。

---

## 数据流

```
浏览器扩展 (chrome-extension/)
      │  fetch  GET /studio/api/newtab.json
      ▼
服务端 (server/app/newtab_api.py)
      │  ├─ SQLite: projects / posts
      │  ├─ 媒体库扫描: shots
      │  └─ 天气服务: LayeredCache → 心知天气
      ▼
   JSON { ok, projects[], posts[], weather{}, shots[] }
```

**离线降级**：拉取失败（8 秒超时）时页面仍完整可用，状态灯显示「离线」，
项目/日志显示空态，天气显示占位。**不白屏。**

---

## 部署到服务器

雨云容器只有 SFTP，无 shell。步骤：

1. SFTP 上传 `server/` 到 `/workspace/studio/`
2. 入口脚本 `启动脚本(可修改).sh` → `bash /workspace/start.tls.sh`
3. `PYTHONPATH=/workspace/studio/_vendor:/workspace/studio`
4. 改 Python 代码后**必须重启**；改模板/静态文件无需重启

**⚠️ 换行符纪律**：上传的 `.env` / `.sh` 必须是 **LF**。
Windows 用 `Set-Content` 生成会带 CRLF，导致 bash 报
`$'\r': command not found`，环境变量全失、路由前缀变空、全站 404。
**上传前务必检查 CR 数为 0。**

---

## 已知限制

- 文章内容为空（`posts` 表待填）
- 音乐播放器需上传 mp3 到媒体库，且当前未接列表渲染
- `avatar.svg` 是占位图
- Garden Action 的 `repo_url` 留空（源码未公开托管，页面已写明）
- 扩展未在 Chrome 官方商店发布，仅开发者模式安装

---

## 许可提示

视觉与部分实现参考了 `white66-7/blog`（PolyForm Noncommercial）。
该许可**第 1 条强制要求保留版权声明**。当前选择不加署名、自行负责；
**若将来开源或商用，必须补上署名。**
