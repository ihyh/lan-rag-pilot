"""Bounded, direct public-page fetches; DNS addresses are checked and pinned."""
import http.client
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_BYTES = 2_000_000


def normalize_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 or c == '\\' for c in value):
        raise ValueError('请填写完整的公开网页网址')
    try:
        parts = urlsplit(value)
        if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.port not in (None, 80 if parts.scheme == 'http' else 443)):
            raise ValueError()
        host = parts.hostname.rstrip('.').encode('idna').decode('ascii').lower()
        if host == 'localhost' or host.endswith(('.localhost', '.local')) or '%' in host:
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if '.' not in host or any(not label or len(label) > 63 or label.startswith('-')
                                      or label.endswith('-') or not all(c.isalnum() or c == '-' for c in label)
                                      for label in host.split('.')):
                raise ValueError()
        else:
            if not address.is_global or address.is_multicast:
                raise ValueError()
            host = '[' + host + ']' if address.version == 6 else host
        # Encode non-ASCII path/query without changing existing percent escapes.
        from urllib.parse import quote
        return urlunsplit((parts.scheme, host, quote(parts.path or '/', safe="/%:@!$&'()*+,;=-._~"),
                           quote(parts.query, safe="/%?:@!$&'()*+,;=-._~"), ''))
    except (ValueError, UnicodeError) as exc:
        raise ValueError('仅支持公开HTTP/HTTPS网页，不能导入本机、内网、带账号或非标准端口的网址') from exc


def public_addresses(host, port):
    records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses or any(not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast
                            for ip in addresses):
        raise ValueError('网址指向本机或非公开网络，已停止读取')
    return addresses


def fetch(url):
    url = normalize_url(url)
    for redirect in range(4):
        parts = urlsplit(url)
        port = 443 if parts.scheme == 'https' else 80
        address = public_addresses(parts.hostname, port)[0]
        connection = (http.client.HTTPSConnection if parts.scheme == 'https' else http.client.HTTPConnection)(
            parts.hostname, port, timeout=25)
        # http.client retains the original host for Host and TLS certificate/SNI,
        # while the socket connects only to this already-validated address.
        connection._create_connection = lambda target, timeout, source_address=None: socket.create_connection(
            (address, port), timeout, source_address)
        try:
            connection.request('GET', urlunsplit(('', '', parts.path, parts.query, '')),
                               headers={'User-Agent': 'PersonalKnowledgeImport/1.0',
                                        'Accept': 'text/html,application/xhtml+xml', 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader('Location')
                if not location or redirect == 3:
                    raise ValueError('网页重定向过多或缺少目标网址')
                url = normalize_url(urljoin(url, location))
                continue
            if response.status != 200:
                raise ValueError('网页返回HTTP ' + str(response.status))
            content_type = response.getheader('Content-Type', '').split(';')[0].strip().lower()
            if content_type not in ('text/html', 'application/xhtml+xml'):
                raise ValueError('响应不是HTML网页，请使用文件上传')
            if response.getheader('Content-Encoding', 'identity').lower() not in ('identity', ''):
                raise ValueError('网页未提供可读取的未压缩正文')
            raw = response.read(MAX_BYTES + 1)
            if not raw or len(raw) > MAX_BYTES:
                raise ValueError('网页为空或超过2MB上限')
            return raw, url
        finally:
            connection.close()
