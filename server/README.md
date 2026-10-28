# 个人数字工作室

一个面向 `/studio/` 子路径的独立个人项目展示与博客站点。技术栈：Python 3.11+、FastAPI、Jinja2、SQLite、原生 CSS/JS、GSAP（CDN 可选增强，离线时基础动效仍可用）。

## 功能
- 首页、项目列表/详情、博客列表/详情、Now、About、RSS、sitemap、robots、404。
- 管理后台：项目与文章新增/编辑/发布/取消发布/删除，站点设置，安全图片媒体库，JSON 导出与经过字段校验的备份恢复。
- 本地交互式管理员初始化；密码使用 scrypt 哈希存储，代码仓库不包含凭据。
- 登录会话签名、HttpOnly/SameSite Cookie、CSRF 校验、登录限速、服务端鉴权、草稿不公开。
- SQLite 持久化；首次启动只初始化表与默认项目，不覆盖已有数据。
- 所有 URL 以 `/studio` 为根前缀，便于挂在现有站点的子路径。

## 运行要求
Python 3.11 或更高版本。

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
python create_admin.py
python run.py
```

浏览器打开 `http://127.0.0.1:8000/studio/`。后台：`/studio/admin/`。

`create_admin.py` 会在终端中安全地提示输入用户名和密码（密码输入不回显），不需要把密码发给任何人。管理员配置写入 `instance/admin.json`，数据库位于 `instance/studio.db`；两者均被 `.gitignore` 排除。请妥善备份 `instance/`，不要提交到 Git。

## 路径前缀
默认 `APP_PREFIX=/studio`。若反向代理已经将 `/studio/` 转发到应用根路径，保持默认即可。若本地只想临时从 `/` 访问，可设置 `APP_PREFIX=`；正式部署到子路径时务必恢复 `/studio`。

## 安全提示
- 只绑定 `127.0.0.1` 进行本地开发；不要直接暴露到公网。
- 生产环境应设置强随机 `APP_SECRET` 环境变量并启用 HTTPS。
- 后台文章使用安全的基础 Markdown 渲染，不允许任意 HTML 执行。
- 图片上传与富文本 HTML 暂不开放，避免引入不必要的文件上传攻击面。

## 测试
```bash
pytest -q
```

## 媒体备份说明

JSON 备份包含项目、文章和站点设置；出于体积与完整性考虑，不内嵌图片二进制文件。需要完整备份时，请同时复制 `instance/media/` 目录。恢复 JSON 不会删除媒体文件。
