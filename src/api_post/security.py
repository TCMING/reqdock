import hashlib
import hmac
import json
import secrets
import time
import threading
from pathlib import Path

SENSITIVE = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "password",
    "passwd",
    "secret",
    "secret_key",
    "secretkey",
    "api_key",
    "apikey",
    "access_key",
    "access_token",
    "refresh_token",
    "token",
    "signature",
    "sign",
    "x-api-key",
    "x-auth-token",
}


def sensitive(key):
    key = str(key).lower().replace("-", "_")
    return key in {x.replace("-", "_") for x in SENSITIVE} or key.endswith(
        ("_token", "_secret", "_password", "_api_key", "_signature", "_credential")
    )


def redact(value, known=()):
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if sensitive(k) else redact(v, known)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v, known) for v in value]
    if isinstance(value, str):
        for item in sorted(set(known), key=len, reverse=True):
            if item:
                value = value.replace(item, "[REDACTED]")
    return value


def safe_text(text, known=()):
    try:
        return json.dumps(redact(json.loads(text), known), ensure_ascii=False, indent=2)
    except (ValueError, RecursionError):
        return redact(text, known)


class Vault:
    def __init__(self, root: Path):
        import keyring

        self.backend = keyring
        if not type(keyring.get_keyring()).__module__.startswith(
            "keyring.backends.Windows"
        ):
            raise RuntimeError("此版本要求 Windows 系统凭证后端，不允许明文替代")
        self.service = (
            "api-post:" + hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:24]
        )

    def get(self, key):
        return self.backend.get_password(self.service, key)

    def set(self, key, value):
        self.backend.set_password(self.service, key, value)

    def delete(self, key):
        if self.get(key) is not None:
            self.backend.delete_password(self.service, key)


class Auth:
    SESSION_SECONDS = 365 * 86400

    def __init__(self, vault):
        self.vault = vault
        self.master = vault.get("local_master")
        if not self.master:
            self.master = secrets.token_urlsafe(48)
            vault.set("local_master", self.master)
        self.pairs = {}
        self.attempts = []
        self.lock = threading.RLock()

    def bearer(self, request):
        token = request.headers.get("authorization", "")
        return hmac.compare_digest(token.encode(), ("Bearer " + self.master).encode())

    def session(self, request):
        cookie = request.cookies.get("api_post_session", "")
        try:
            data, sign = cookie.rsplit(".", 1)
            expected = hmac.new(
                self.master.encode(), data.encode(), hashlib.sha256
            ).hexdigest()
            expiry, nonce = data.split(":", 1)
            if (
                hmac.compare_digest(sign, expected)
                and int(expiry) > time.time()
                and nonce not in json.loads(self.vault.get("revoked") or "[]")
            ):
                return cookie
        except (ValueError, TypeError):
            pass
        return None

    def csrf(self, cookie):
        return hmac.new(
            self.master.encode(), ("csrf:" + cookie).encode(), hashlib.sha256
        ).hexdigest()

    def sign_session(self, nonce, expiry):
        data = f"{int(expiry)}:{nonce}"
        return (
            data
            + "."
            + hmac.new(self.master.encode(), data.encode(), hashlib.sha256).hexdigest()
        )

    def remember(self, cookie):
        """Renew an authenticated browser without rotating its revocable identity."""
        expiry, nonce = cookie.rsplit(".", 1)[0].split(":", 1)
        if int(expiry) - time.time() < self.SESSION_SECONDS // 2:
            return self.sign_session(nonce, time.time() + self.SESSION_SECONDS)
        return cookie

    def issue_code(self):
        self.pairs = {k: v for k, v in self.pairs.items() if v > time.time()}
        if len(self.pairs) >= 5:
            self.pairs.clear()
        code = secrets.token_urlsafe(24)
        self.pairs[hashlib.sha256(code.encode()).hexdigest()] = time.time() + 300
        return code

    def exchange(self, code):
        now = time.time()
        self.attempts = [v for v in self.attempts if v > now - 60]
        if len(self.attempts) >= 10:
            raise ValueError("尝试过多，请一分钟后重试")
        self.attempts.append(now)
        key = hashlib.sha256(code.encode()).hexdigest()
        expiry = self.pairs.pop(key, 0)
        if expiry < now:
            raise ValueError("配对码无效或已过期")
        return self.sign_session(secrets.token_hex(16), now + self.SESSION_SECONDS)

    def revoke(self, cookie):
        with self.lock:
            self._revoke(cookie)

    def _revoke(self, cookie):
        nonce = cookie.rsplit(".", 1)[0].split(":", 1)[1]
        revoked = json.loads(self.vault.get("revoked") or "[]")
        if len(revoked) >= 100:
            self.master = secrets.token_urlsafe(48)
            self.vault.set("local_master", self.master)
            revoked = []
        else:
            revoked.append(nonce)
        self.vault.set("revoked", json.dumps(revoked))
