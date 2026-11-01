"""Bounded discovery of media returned by HTTP and SSE responses; no fetching."""
import base64
import hashlib
import ipaddress
import json
import re
from pathlib import PurePosixPath
from urllib.parse import urlsplit, unquote, parse_qsl
from .security import sensitive

EXTENSIONS = {
    'image': {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.bmp'},
    'video': {'.mp4', '.mov', '.webm', '.mkv', '.m4v', '.avi'},
    'audio': {'.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.opus'},
}


class ResponseMedia:
    def __init__(self):
        self.items = {}
        self.limited = False

    def add(self, kind, url=None, content=None):
        if len(self.items) >= 32:
            self.limited = True
            return
        identity = hashlib.sha256((url.encode() if url else content)).hexdigest()
        self.items.setdefault(kind+':'+identity, {'kind': kind, 'url': url, 'content': content})

    def text(self, text):
        if len(text) > 2*1024**2:
            self.limited = True
            return
        try:
            value = json.loads(text)
        except (ValueError, RecursionError):
            value = text
        stack = [(value, '', 0)]
        visited = 0
        while stack and visited < 10000:
            item, hint, depth = stack.pop()
            visited += 1
            if depth > 24:
                self.limited = True
                continue
            if isinstance(item, dict):
                declared = str(item.get('type', '') or item.get('mime_type', '') or item.get('mime', '') or item.get('content_type', '')).lower()
                kind = next((k for k in EXTENSIONS if k in declared), hint)
                for key, value in item.items():
                    key = key.lower()
                    child = next((k for k in EXTENSIONS if k in key), kind)
                    if key == 'b64_json':
                        child = 'image'
                    if key in {'b64_json', 'base64', 'data'} and isinstance(value, str) and child in EXTENSIONS and not value.startswith(('http:', 'https:', 'data:')):
                        self.inline(value, child)
                    else:
                        stack.append((value, child, depth+1))
            elif isinstance(item, list):
                stack.extend((v, hint, depth+1) for v in item[:2000])
                self.limited |= len(item) > 2000
            elif isinstance(item, str):
                if item.startswith('data:'):
                    match = re.fullmatch(r'data:(image|video|audio)/[a-zA-Z0-9.+-]+;base64,([A-Za-z0-9+/=\r\n]+)', item)
                    if match:
                        self.inline(match[2], match[1])
                    continue
                urls = [item] if item.startswith(('https://','http://')) and not re.search(r'\s',item) else re.findall(r'https?://[^\s<>"\x27`]+', item)
                for url in urls[:32]:
                    url = url.rstrip('),].')
                    if '[redacted]' in unquote(url).lower():
                        continue
                    if len(url)>8192 or re.search(r'[\x00-\x20\x7f]',url):
                        continue
                    try:
                        parsed = urlsplit(url)
                        if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
                            continue
                        extension = PurePosixPath(unquote(parsed.path)).suffix.lower()
                    except ValueError:
                        continue
                    kind = hint or next((k for k, values in EXTENSIONS.items() if extension in values), '')
                    if kind in EXTENSIONS:
                        self.add(kind, url=url)
        self.limited |= bool(stack)

    def protect(self, known):
        for item in self.items.values():
            url = item.get('url')
            if url and url not in known and any(sensitive(key) for key, _ in parse_qsl(urlsplit(url).query)):
                known.append(url)

    def inline(self, value, kind):
        if len(value) > 6*1024**2:
            self.limited = True
            return
        try:
            content = base64.b64decode(re.sub(r'\s', '', value), validate=True)
        except ValueError:
            return
        if content and len(content) <= 4*1024**2:
            self.add(kind, content=content)


def link_scope(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ''
    if host.lower() == 'localhost':
        return 'local'
    try:
        address = ipaddress.ip_address(host)
        if address.is_loopback:
            return 'local'
        if address.is_private:
            return 'internal'
    except ValueError:
        pass
    if host.endswith(('.domain', '.qae', '.storage.internal.invalid')):
        return 'internal'
    return 'public'
