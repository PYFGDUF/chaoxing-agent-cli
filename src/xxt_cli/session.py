from __future__ import annotations
import base64
import hashlib
import json
import os
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CliError(Exception):
    def __init__(self, code, message, exit_code=1, details=None):
        super().__init__(message)
        self.code, self.exit_code, self.details = code, exit_code, details


def atomic_write(path: Path, content: str, private=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix=".xxt-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(content)
        if private:
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class Session:
    def __init__(self, directory: Path):
        self.directory = directory.expanduser().resolve()
        self.state = self.directory / "session.enc"
        self.key = self.directory / "session.key"

    def save(self, data):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not self.key.exists():
            try:
                fd = os.open(self.key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as out:
                    out.write(AESGCM.generate_key(bit_length=256))
        nonce = os.urandom(12)
        payload = AESGCM(self.key.read_bytes()).encrypt(nonce, json.dumps(data).encode(), None)
        atomic_write(self.state, base64.b64encode(nonce + payload).decode(), private=True)

    def load(self):
        if not self.state.exists():
            raise CliError("AUTH_REQUIRED", "请运行 xxt auth login 或 xxt auth import。", 3)
        try:
            raw = base64.b64decode(self.state.read_text())
            return json.loads(AESGCM(self.key.read_bytes()).decrypt(raw[:12], raw[12:], None))
        except Exception:
            raise CliError("SESSION_INVALID", "登录态读取失败，请重新登录。", 3) from None

    def import_file(self, path: Path, legacy_key: Path | None = None):
        raw = path.read_text(encoding="utf-8")
        if raw.startswith("xxt_state_v1:"):
            if legacy_key is None:
                raise CliError("INVALID_ARGUMENT", "加密客户端登录态需同时提供 --legacy-key。", 2)
            seed = "xxt-login-state::v1:" + legacy_key.read_text().strip()
            key = hashlib.sha256(seed.encode()).digest()
            encrypted = base64.b64decode(raw.split(":", 1)[1])
            raw = AESGCM(key).decrypt(encrypted[:12], encrypted[12:], None).decode()
        data = json.loads(raw)
        if isinstance(data, list):
            data = {"cookies": data, "origins": []}
        if not isinstance(data, dict) or not isinstance(data.get("cookies"), list):
            raise CliError("INVALID_ARGUMENT", "文件需为 Playwright storage_state 或 Cookie JSON 数组。", 2)
        # Retain Chaoxing cookies and origins only.
        data["cookies"] = [c for c in data["cookies"] if c.get("domain", "").lstrip(".") == "chaoxing.com"
                           or c.get("domain", "").endswith(".chaoxing.com")]
        data["origins"] = []
        if not data["cookies"]:
            raise CliError("INVALID_ARGUMENT", "文件中缺少学习通 Cookie。", 2)
        self.save(data)
