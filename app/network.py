"""Bounded HTTP downloads, with DNS pinned to a validated public address."""
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit
import httpx

MAX_BYTES = 8 * 1024 * 1024

def public_target(url):
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('仅允许不含用户名密码的 HTTP / HTTPS 地址')
    port = p.port or (443 if p.scheme == 'https' else 80)
    addresses = socket.getaddrinfo(p.hostname, port, type=socket.SOCK_STREAM)
    ips = list(dict.fromkeys(a[4][0] for a in addresses))
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ValueError('为保护 NAS，禁止访问本机、局域网或保留 IP 地址')
    # NAS IPv6 connectivity is often incomplete; try IPv4 first, then remaining
    # validated addresses. Never fall back to an unvalidated DNS lookup.
    return p, sorted(ips, key=lambda ip: ipaddress.ip_address(ip).version)

def download(url, *, method='GET', headers=None, json=None, limit=MAX_BYTES):
    for _ in range(6):
        p, ips = public_target(url)
        host = p.hostname.encode('idna').decode()
        host_header = f'[{host}]' if ':' in host else host
        if p.port:
            host_header += f':{p.port}'
        request_headers = {'User-Agent': 'HarborRSS/1.0 (+self-hosted reader)', **(headers or {}), 'Host': host_header}
        for index, ip in enumerate(ips[:4]):
            target = httpx.URL(url).copy_with(host=ip)
            try:
                with httpx.Client(trust_env=False, timeout=httpx.Timeout(90, connect=10)) as client:
                    with client.stream(method, target, headers=request_headers, json=json,
                                       extensions={'sni_hostname': host}) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            if method != 'GET':
                                raise ValueError('API 不允许重定向，请填写最终 API 地址')
                            location = response.headers.get('location')
                            if not location:
                                raise ValueError('重定向缺少目标地址')
                            url = urljoin(url, location)
                            break
                        response.raise_for_status()
                        body = bytearray()
                        for chunk in response.iter_bytes():
                            body.extend(chunk)
                            if len(body) > limit:
                                raise ValueError('响应超过大小限制')
                        return bytes(body), str(url), response.headers.get('content-type', '')
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if index == min(len(ips), 4) - 1:
                    raise
    raise ValueError('重定向次数过多')
