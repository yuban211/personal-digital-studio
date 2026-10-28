#!/usr/bin/env python3
"""Interactively configure a local administrator; secrets never echo to terminal."""
import getpass
import re
import sys
from app.security import ADMIN_FILE, read_admin, write_admin


def main():
    if read_admin():
        answer = input("管理员配置已存在。要覆盖并重置密码吗？输入 RESET 确认：").strip()
        if answer != "RESET":
            print("未更改管理员配置。")
            return
    username = input("设置管理员用户名（3-40 个字母、数字、点、下划线或短横线）：").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,40}", username):
        print("用户名格式不正确。", file=sys.stderr); raise SystemExit(2)
    while True:
        password = getpass.getpass("设置管理员密码（至少 14 位）：")
        confirm = getpass.getpass("再次输入密码：")
        if password != confirm:
            print("两次密码不一致，请重试。"); continue
        if len(password) < 14:
            print("密码至少需要 14 位。"); continue
        if len(password) > 1024:
            print("密码过长。"); continue
        break
    write_admin(username, password)
    print(f"管理员凭据已安全写入 {ADMIN_FILE}（密码仅保存哈希，文件已尝试限制为当前用户可读写）。")
    print("请勿提交 instance/ 目录或把密码发送给任何人。")

if __name__ == "__main__": main()
