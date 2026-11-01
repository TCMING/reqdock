"""Explicit origin allowlist + validated DNS pinned at connect; no proxy/redirect."""

import asyncio
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import httpcore
import httpx
from .security import sensitive


class PolicyError(ValueError):
    pass


def origin(url):
    try:
        p = urlsplit(url)
        if (
            p.scheme not in {"http", "https"}
            or not p.hostname
            or p.username is not None
            or p.password is not None
            or p.fragment
        ):
            raise PolicyError("仅支持无用户信息和片段的 HTTP/HTTPS 地址")
        host = p.hostname
        if (
            not host.isascii()
            or host.endswith(".")
            or "%" in host
            or "\\" in url
            or any(ord(c) < 33 for c in url)
        ):
            raise PolicyError("地址含不允许的主机名或控制字符")
        if not all(c.isalnum() or c in "-.:" for c in host):
            raise PolicyError("主机名不合法")
        port = p.port or (443 if p.scheme == "https" else 80)
        if not 1 <= port <= 65535:
            raise PolicyError("端口不合法")
        return p.scheme, host.lower(), port
    except ValueError as e:
        raise PolicyError("地址格式不合法") from e


def validate_environment(env, server_port=18765):
    scheme, host, port = origin(env["base_url"])
    if urlsplit(env["base_url"]).query:
        raise PolicyError("环境地址不能包含查询参数")
    scope = env["network_scope"]
    if scope == "public" and scheme != "https":
        raise PolicyError("公网环境必须使用 HTTPS")
    if scope == "local" and host not in {"localhost", "127.0.0.1", "::1"}:
        raise PolicyError("本机策略只允许回环地址")
    if host in {"localhost", "127.0.0.1", "::1"} and port == server_port:
        raise PolicyError("不能请求测试工具自身端口")


def target_url(env, path):
    if path.startswith(("http://", "https://")):
        target = path
    else:
        if path.startswith("//"):
            raise PolicyError("不允许协议相对地址")
        target = env["base_url"].rstrip("/") + "/" + path.lstrip("/")
    if origin(target) != origin(env["base_url"]):
        raise PolicyError("请求目标必须与所选环境的协议、主机和端口一致")
    return target


def redact_url(url):
    p = urlsplit(url)
    return urlunsplit(
        (
            p.scheme,
            p.netloc,
            p.path,
            urlencode(
                [
                    (k, "[REDACTED]" if sensitive(k) else v)
                    for k, v in parse_qsl(p.query, keep_blank_values=True)
                ]
            ),
            "",
        )
    )


def check_ip(address, scope, port, server_port):
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if str(ip) in {"168.63.129.16", "100.100.100.200", "192.0.0.192", "fd00:ec2::254"}:
        raise PolicyError("禁止云平台特殊地址")
    if (
        ip.is_unspecified
        or ip.is_multicast
        or ip.is_link_local
        or (ip.is_reserved and not ip.is_loopback)
    ):
        raise PolicyError("目标为保留地址或 Metadata 地址")
    if ip.is_loopback:
        if scope != "local" or port == server_port:
            raise PolicyError("当前策略不允许此回环目标")
    elif scope == "local":
        raise PolicyError("本机环境解析出了非回环地址")
    elif scope == "public" and not ip.is_global:
        raise PolicyError("公网环境解析出了非公网地址")
    elif scope == "internal" and not (ip.is_global or ip.is_private):
        raise PolicyError("不允许的内网地址")


class PinnedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, env, server_port):
        self.env = env
        self.server_port = server_port
        self.backend = httpcore.AnyIOBackend()
        self.expected = origin(env["base_url"])

    async def connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ):
        if (host.lower(), port) != self.expected[1:]:
            raise PolicyError("连接目标与环境不一致")
        loop = asyncio.get_running_loop()
        records = await asyncio.wait_for(
            loop.getaddrinfo(host, port, type=socket.SOCK_STREAM),
            timeout=min(timeout or 10, 10),
        )
        addresses = list(dict.fromkeys(x[4][0] for x in records))
        if not addresses:
            raise PolicyError("没有可用的 DNS 地址")
        for address in addresses:
            check_ip(address, self.env["network_scope"], port, self.server_port)
        deadline = loop.time() + (timeout or 10)
        last = None
        for address in addresses:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                return await self.backend.connect_tcp(
                    address,
                    port,
                    timeout=remaining,
                    local_address=None,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last = exc
        raise last or httpcore.ConnectTimeout("连接超时")

    async def connect_unix_socket(self, *args, **kwargs):
        raise PolicyError("不允许 Unix socket")

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


class CoreStream(httpx.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        async for data in self.stream:
            yield data

    async def aclose(self):
        await self.stream.aclose()


class PinnedTransport(httpx.AsyncBaseTransport):
    def __init__(self, env, server_port):
        self.pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=PinnedBackend(env, server_port),
            max_connections=1,
            max_keepalive_connections=0,
            retries=0,
        )

    async def handle_async_request(self, request):
        req = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        response = await self.pool.handle_async_request(req)
        return httpx.Response(
            response.status,
            headers=response.headers,
            stream=CoreStream(response.stream),
            extensions=response.extensions,
        )

    async def aclose(self):
        await self.pool.aclose()
