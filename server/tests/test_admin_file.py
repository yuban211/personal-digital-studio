"""管理员凭据路径可被环境变量覆盖的测试。"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_vendor"))


def test_admin_file_default_is_instance():
    """默认仍是 instance/admin.json。"""
    import app.security as s
    importlib.reload(s)
    assert s.ADMIN_FILE.name == "admin.json"
    assert s.ADMIN_FILE.parent.name == "instance"


def test_admin_file_env_override(tmp_path=None):
    """STUDIO_ADMIN_FILE 能覆盖路径 —— 部署时 instance/ 只读的情况需要。"""
    import app.security as s
    old = os.environ.get("STUDIO_ADMIN_FILE")
    try:
        os.environ["STUDIO_ADMIN_FILE"] = r"D:\.211\浏览器首页\credentials\admin.json"
        importlib.reload(s)
        assert str(s.ADMIN_FILE) == r"D:\.211\浏览器首页\credentials\admin.json"
    finally:
        if old is None:
            os.environ.pop("STUDIO_ADMIN_FILE", None)
        else:
            os.environ["STUDIO_ADMIN_FILE"] = old
        importlib.reload(s)


def test_write_admin_writes_to_override():
    """覆盖后写凭据会写到新路径。"""
    import app.security as s
    old = os.environ.get("STUDIO_ADMIN_FILE")
    # 用当前工作目录下的临时文件，避免系统 TEMP 的权限差异
    target = Path.cwd() / "_admin_probe.json"
    try:
        os.environ["STUDIO_ADMIN_FILE"] = str(target)
        importlib.reload(s)
        s.write_admin("tester", "some-password-123")
        assert target.exists()
        rec = s.read_admin()
        assert rec["username"] == "tester"
        assert s.verify_password("some-password-123", rec["password"])
        target.unlink()
    finally:
        if old is None:
            os.environ.pop("STUDIO_ADMIN_FILE", None)
        else:
            os.environ["STUDIO_ADMIN_FILE"] = old
        importlib.reload(s)


def test_hash_password_roundtrip():
    from app.security import hash_password, verify_password
    rec = hash_password("2483065007")
    assert rec["scheme"] == "scrypt"
    assert verify_password("2483065007", rec)
    assert not verify_password("2483065008", rec)
