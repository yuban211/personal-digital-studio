---
title: Windows 上写脚本的三个坑：编码、执行策略、跨盘移动
slug: windows-script-pitfalls
summary: GBK 解码把引号配对搞崩、PowerShell 拒绝执行未签名脚本、os.rename 跨盘直接失败。
tags: Windows,PowerShell,编码
date: 2026-10-28
draft: false
---

迁移项目时连续踩了三个坑，都不复杂但都会让人卡住。

## 坑一：UTF-8 脚本被 GBK 解码，语法直接崩

Windows PowerShell 5.1 默认按系统 ANSI 代码页（中文系统是 GBK）读取 `.ps1` 文件。而编辑器写出来的是 UTF-8。

结果是中文字符串里的一个字节被当成引号或者括号，整份脚本的解析结构全部错位：

```
Unexpected token 'studio-newtab';           To = 'browser-extension' },
The string is missing the terminator: ".
```

看起来像是少了个引号，实际是编码问题。

**解法：给文件加 UTF-8 BOM。**PowerShell 5.1 看到 BOM 就会正确识别 UTF-8：

```python
from pathlib import Path
p = Path("script.ps1")
text = p.read_text(encoding="utf-8")
p.write_text(text, encoding="utf-8-sig")
```

写完之后前三字节应该是 `239, 187, 191`。

## 坑二：执行策略拒绝运行

加了 BOM 还是不行：

```
无法加载文件 xxx.ps1。未对文件 xxx.ps1 进行数字签名。
```

Windows 默认的 Restricted 策略禁止执行未签名的脚本。临时放行：

```sh
powershell.exe -NoProfile -ExecutionPolicy Bypass -File script.ps1
```

`-NoProfile` 还能避免用户 profile 里的东西干扰执行。

## 坑三：跨盘移动不能靠 rename

迁移项目时用了 `shutil.move`，它会先尝试 `os.rename`。跨盘时 rename 不支持，会 fallback 到复制+删除——但这个 fallback 路径在某些权限组合下会失败：

```
PermissionError: [WinError 5] 拒绝访问
```

**更稳的做法是拆成三段：复制 → 逐文件哈希校验 → 删源。**

```python
# 1. 复制
shutil.copy2(src_file, dst_file)

# 2. 校验（一个字节都不能差）
if sha256(src_file) != sha256(dst_file):
    raise SystemExit("中止：不删源文件")

# 3. 全部校验通过后才删源
shutil.rmtree(src_dir)
```

哈希校验这一段不能省。少一个校验步骤，就可能在源已删、目标不完整的情况下宣告"迁移成功"。

## 附带一条：沙箱和权限不是一回事

迁移目标在 `D:\.211`，PowerShell 能写，但 Python 子进程报 `PermissionError`。

这不是文件系统 ACL 的问题——`Get-Acl` 显示权限完全正常，`Authenticated Users` 有 `Modify`。是执行环境本身对子进程做了工作区限定。

排查这类问题时，先用两种方式各写一次：

- PowerShell 直接写 → 成功
- Python 子进程写 → 失败

如果结果不同，就不要继续在 ACL 上找原因了，那是沙箱，不是权限。
