---
title: 没有 npm 的时候，怎么把一个 TypeScript 插件编译出来
slug: build-plugin-without-npm
summary: 手搓 tarball、绕过 prepare 脚本、逐条核对 API 签名——在没有完整工具链的机器上装第三方插件。
tags: 工具链,插件,踩坑
date: 2026-10-28
draft: false
---

## 场景

要装一个社区插件，仓库在 GitHub 上，但本机没有 npm，也没有 pnpm。运行时自带一个精简版 Node，插件声明要用 pnpm 11。

按常规路径走，第一步就会卡住。

## 障碍一：包不在 npm 上

插件只在 GitHub 仓库里发布，npm registry 上查无此包。README 里写的安装命令是 `plugin add github:owner/repo#v1.0.0`。

直接用会报错：**找不到那个标签**。仓库里实际只有更早的一个 tag，README 写的是尚未发布的版本。

解决：改用分支名或具体 commit，而不是标签。

## 障碍二：构建脚本被白名单拦住

pnpm 出于供应链安全，默认不执行依赖的构建脚本。报错里会直接告诉你该往哪加：

```yaml
# pnpm-workspace.yaml
allowBuilds:
  某个包@完整路径: true
```

注意这里要用**完整 spec**，不是包名。写包名不生效。

## 障碍三：没有 npm，但构建脚本依赖 npm

白名单放开后，新的报错出现了：`npm run build`——可是本机没有 npm。

这个包的标准构建流程是 `clean → tsdown → tsc`，其中 `clean` 脚本内部调用了 `npm run clean`。绕过方法很简单，拆开手动执行：

```sh
node ./scripts/clean.mjs          # 清理产物
node ./node_modules/tsdown/dist/run.mjs   # 打包
node ./node_modules/typescript/bin/tsc -p tsconfig.build.json  # 出类型
```

`tsdown` 的入口在 `package.json` 的 `exports` 里，不一定在 `dist/cli.mjs`，找不到会报 `MODULE_NOT_FOUND`。

## 障碍四：打包也要 npm

`pnpm pack` 同样会触发 `prepare` 脚本。于是最后一步干脆手工打 tarball——npm 包格式其实很简单：

- 一个 gzip 压缩的 tar
- 根目录是 `package/`
- 里面放 `package.json` 和 `files` 字段列出的文件

Python 的 `tarfile` 十几行就能搞定，记得把 `uid/gid` 清零、`mtime` 设为 0，保证可复现。

## 障碍五：版本不兼容

终于装上了，却被拦下：

```
Plugin X@0.3.0 is incompatible with dsh 0.2.1-rc.2
```

插件要求 `0.2.0-rc.2`，本机是更新的 `0.2.1-rc.2`。这时**先别急着豁免**，值得花十分钟核对一下：

1. 插件从宿主包导入了哪些符号？逐个列出来
2. 本机运行时实际暴露的 API 签名是什么？用 inspect 工具查实时状态，不要看文档
3. 逐条对得上吗？

这次核对的结论是：插件只用到 `slots.inject`、`slots.register`、`locale.register`、`locale.bind`、`sessions.binding` 五个公开客户端 API，签名全部一致，插槽的注册契约（`id`/`order`/`label`）也没变。宿主入口甚至是个空函数。

**补丁级版本号差异 + 只依赖公开 API = 实际风险很低**，这才值得申请豁免。

## 障碍六：改完发现白名单键名不对

`allowBuilds` 里的键必须和 pnpm 报错里给出的完全一致。直接写包名不生效，要写 `包名@file:/绝对路径/xxx.tgz` 这种完整形式。

报错提示里通常已经给了正确写法，照抄即可。

## 一句话总结

每一步的报错信息里其实都藏着解法：

- `Could not resolve v1.0.0 to a commit` → 标签不存在，换 commit
- `not in the allowBuilds allowlist` → 按提示加白名单
- `'npm' is not recognized` → 拆开手动执行底层命令
- `is incompatible with` → 先核对 API，再决定要不要豁免
- `Ignored build scripts` → 键名用完整 spec

**别猜，去读报错。**它比任何文档都准。
