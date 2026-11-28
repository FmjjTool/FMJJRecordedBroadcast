"""Loopback HTTPS relay for explicitly started reading detection.

Only the exact reading host is decrypted. Other public HTTPS destinations are
opaque tunnels. Requests and responses are never logged or saved; the observer
emits only the minimum parameters from one fresh HTTP request. Import is inert.
"""
from __future__ import annotations

import ipaddress
import re
import select
import socket
import socketserver
import ssl
import threading
import time
from typing import Callable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit


_HOST = "mp.weixin.qq.com"
_HEADER_LIMIT = 32768
_FORM_LIMIT = 65536
_URL_LIMIT = 32768
_CHUNK = 65536
_RESOLVE_TIMEOUT = 3.0
_FIELDS = frozenset(("__biz", "biz", "key", "uin", "pass_ticket", "appmsg_token"))
_DIAGNOSTIC_STAGES = frozenset((
    "proxy_connections", "connect_requests", "wechat_connects",
    "upstream_tls", "client_tls", "http_requests",
    "observer_disabled",
))
_ARTICLE_PATH = re.compile(r"/s(?:/[A-Za-z0-9_-]+)?\Z")
_READING_PATHS = frozenset((
    "/mp/getappmsgext", "/mp/profile_ext", "/mp/getappmsgad",
    "/mp/getappmsgcomment", "/mp/getmasssendmsg", "/mp/appmsgalbum",
    "/mp/getappmsgalbum", "/mp/appmsg_comment", "/mp/appmsg_like",
))
_DNS_NAME = re.compile(
    r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z"
)


class CaptureProxyError(RuntimeError):
    """Static error text; never expose a request or system exception."""

    def __init__(self):
        super().__init__("无法启动本机检测服务，请稍后重试。")


def _safe_parameters(value: str) -> dict[str, str] | None:
    try:
        pairs = parse_qsl(value, keep_blank_values=True, max_num_fields=80,
                          encoding="utf-8", errors="strict")
    except (ValueError, UnicodeError):
        return None
    result = {}
    for key, item in pairs:
        if key not in _FIELDS:
            continue
        key = "__biz" if key == "biz" else key
        if (key in result or not item or len(item) > 8192
                or any(not 33 <= ord(character) <= 126 for character in item)):
            return None
        result[key] = item
    return result


def _reading_parts(target: str):
    if (not isinstance(target, str) or not target or len(target) > _URL_LIMIT
            or any(not 33 <= ord(character) <= 126 for character in target)):
        return None
    try:
        parts = urlsplit(target)
        if parts.fragment or parts.username or parts.password:
            return None
        if parts.scheme or parts.netloc:
            if (parts.scheme != "https" or parts.hostname != _HOST
                    or parts.port not in (None, 443)):
                return None
        elif not target.startswith("/") or target.startswith("//"):
            return None
    except ValueError:
        return None
    return parts


def extract_reading_request(target: str, headers: Mapping[str, str],
                            body: bytes = b"") -> str | None:
    """Pure extraction; never mix requests or read authentication headers.

    An article Referer contributes its public account identifier only.
    """
    parts = _reading_parts(target)
    if parts is None or not (_ARTICLE_PATH.fullmatch(parts.path)
                             or parts.path in _READING_PATHS):
        return None
    selected = {name.lower(): value for name, value in headers.items()
                if name.lower() in ("host", "referer", "content-type")}
    if selected.get("host", _HOST).lower() not in (_HOST, _HOST + ":443"):
        return None
    values = _safe_parameters(parts.query)
    if values is None:
        return None
    content_type = selected.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type == "application/x-www-form-urlencoded" and body:
        if len(body) > _FORM_LIMIT:
            return None
        try:
            form = _safe_parameters(body.decode("ascii"))
        except UnicodeError:
            return None
        if form is None or values.keys() & form.keys():
            return None
        values.update(form)
    if not values.get("key") or not values.get("uin"):
        return None
    if not values.get("__biz"):
        referer = _reading_parts(selected.get("referer", ""))
        if (referer is None or referer.scheme != "https"
                or referer.hostname != _HOST or not _ARTICLE_PATH.fullmatch(referer.path)):
            return None
        # Only the public account id is selected, never reading parameters.
        try:
            account_ids = [value for key, value in parse_qsl(
                referer.query, keep_blank_values=True, max_num_fields=80,
                encoding="utf-8", errors="strict") if key == "__biz"]
        except (ValueError, UnicodeError):
            return None
        if len(account_ids) != 1:
            return None
        values["__biz"] = account_ids[0]
    if (not re.fullmatch(r"[A-Za-z0-9_+/=-]{4,256}", values["__biz"])
            or sum(len(value) for value in values.values()) > 16384):
        return None
    ordered = [(key, values[key]) for key in (
        "__biz", "key", "uin", "pass_ticket", "appmsg_token") if values.get(key)]
    result = "https://" + _HOST + "/s?" + urlencode(ordered)
    return result if len(result) <= _URL_LIMIT else None


class _RequestObserver:
    """Bounded HTTP/1 observer; malformed framing disables observation only."""

    def __init__(self, callback: Callable[[str], None], on_request=None,
                 on_disabled=None):
        self._callback = callback
        self._on_request = on_request
        self._on_disabled = on_disabled
        self._buffer = bytearray()
        self._disabled = False
        self._remaining = 0
        self._form = False
        self._chunked = False
        self._chunk_size = None
        self._body = bytearray()
        self._target = ""
        self._headers = {}

    def _disable(self):
        if self._disabled:
            return
        self._disabled = True
        self._buffer.clear()
        self._body.clear()
        self._target = ""
        self._headers.clear()
        if self._on_disabled is not None:
            try:
                self._on_disabled()
            except Exception:
                pass

    def _emit(self, body=b""):
        result = extract_reading_request(self._target, self._headers, body)
        if result:
            try:
                self._callback(result)
            except Exception:
                pass
        self._target = ""
        self._headers = {}
        self._body.clear()
        self._chunked = False
        self._chunk_size = None

    def _header(self, block: bytes) -> bool:
        lines = block.split(b"\r\n")
        try:
            method, target, version = lines[0].decode("ascii").split(" ")
            if (len(lines[0]) > _URL_LIMIT or not re.fullmatch(r"[A-Z]{1,20}", method)
                    or version not in ("HTTP/1.0", "HTTP/1.1")):
                return False
            selected = {}
            framing = {}
            for line in lines[1:]:
                if not line or line[:1] in (b" ", b"\t"):
                    return False
                name, colon, value = line.partition(b":")
                if not colon or not re.fullmatch(rb"[!#$%&'*+.^_|~0-9A-Za-z-]+", name):
                    return False
                name = name.lower()
                if name in (b"host", b"referer", b"content-type", b"content-length", b"transfer-encoding", b"upgrade"):
                    if name in framing:
                        return False
                    framing[name] = value.strip()
                    if name in (b"host", b"referer", b"content-type"):
                        selected[name.decode("ascii")] = value.strip().decode("ascii")
            if b"upgrade" in framing:
                return False
            transfer_encoding = framing.get(b"transfer-encoding", b"").strip().lower()
            length = framing.get(b"content-length", b"0")
            if transfer_encoding:
                # The bounded observer only accepts a single chunked coding and
                # never forwards or interprets arbitrary transfer codings.
                if transfer_encoding != b"chunked" or b"content-length" in framing:
                    return False
                self._remaining = 0
                self._chunked = True
            else:
                if not re.fullmatch(rb"[0-9]{1,18}", length):
                    return False
                self._remaining = int(length)
                self._chunked = False
            self._form = selected.get("content-type", "").split(";", 1)[0].strip().lower() == "application/x-www-form-urlencoded"
            self._target = target if method in ("GET", "POST") and selected.get("host", "").lower() in (_HOST, _HOST + ":443") else ""
            self._headers = selected
            if self._target and self._on_request is not None:
                try:
                    self._on_request()
                except Exception:
                    pass
            if self._form and self._remaining > _FORM_LIMIT:
                self._target = ""
                self._form = False
            if self._chunked and not self._form:
                return False
            if not self._chunked and (not self._form or not self._remaining):
                self._emit()
            return True
        except (ValueError, UnicodeError):
            return False

    def feed(self, data: bytes):
        if self._disabled:
            return
        if len(data) > _CHUNK:
            self._disable()
            return
        self._buffer.extend(data)
        while self._buffer and not self._disabled:
            if self._chunked:
                if self._chunk_size == 0:
                    if len(self._buffer) < 2:
                        return
                    if self._buffer[:2] != b"\r\n":
                        self._disable()
                        return
                    del self._buffer[:2]
                    self._chunked = False
                    self._chunk_size = None
                    self._emit(bytes(self._body))
                    continue
                if self._chunk_size is None:
                    boundary = self._buffer.find(b"\r\n")
                    if boundary < 0:
                        if len(self._buffer) > 64:
                            self._disable()
                        return
                    if boundary == 0 or boundary > 16:
                        self._disable()
                        return
                    line = bytes(self._buffer[:boundary]).split(b";", 1)[0]
                    if not re.fullmatch(rb"[0-9A-Fa-f]{1,16}", line):
                        self._disable()
                        return
                    self._chunk_size = int(line, 16)
                    del self._buffer[:boundary + 2]
                needed = self._chunk_size + 2
                if len(self._buffer) < needed:
                    if len(self._body) + len(self._buffer) > _FORM_LIMIT + 2:
                        self._disable()
                    return
                chunk = bytes(self._buffer[:self._chunk_size])
                if self._buffer[self._chunk_size:needed] != b"\r\n":
                    self._disable()
                    return
                self._body.extend(chunk)
                if len(self._body) > _FORM_LIMIT:
                    self._disable()
                    return
                del self._buffer[:needed]
                self._chunk_size = None
                continue
            if self._remaining:
                if self._form:
                    if len(self._buffer) < self._remaining:
                        return
                    body = bytes(self._buffer[:self._remaining])
                    del self._buffer[:self._remaining]
                    self._remaining = 0
                    self._emit(body)
                else:
                    consumed = min(len(self._buffer), self._remaining)
                    del self._buffer[:consumed]
                    self._remaining -= consumed
                    if self._remaining:
                        return
            else:
                boundary = self._buffer.find(b"\r\n\r\n")
                if boundary < 0:
                    if len(self._buffer) > _HEADER_LIMIT:
                        self._disable()
                    return
                if boundary > _HEADER_LIMIT:
                    self._disable()
                    return
                block = bytes(self._buffer[:boundary])
                del self._buffer[:boundary + 4]
                if not self._header(block):
                    self._disable()


def _connect_host(authority: str) -> str | None:
    try:
        if (not authority or len(authority) > 300
                or any(not 33 <= ord(character) <= 126 for character in authority)):
            return None
        parts = urlsplit("//" + authority)
        host = parts.hostname
        if (not host or parts.port != 443 or parts.username or parts.password
                or parts.path or parts.query or parts.fragment):
            return None
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not _DNS_NAME.fullmatch(host):
                return None
        return host.lower()
    except ValueError:
        return None


def _public_addresses(host: str, resolver) -> list[str]:
    records = resolver(host, 443, type=socket.SOCK_STREAM)
    if not records or len(records) > 32:
        raise CaptureProxyError()
    result = []
    for family, socktype, _protocol, _name, address in records:
        if family not in (socket.AF_INET, socket.AF_INET6) or socktype != socket.SOCK_STREAM:
            raise CaptureProxyError()
        ip = ipaddress.ip_address(address[0])
        if (not ip.is_global or ip.is_private or ip.is_reserved or ip.is_loopback
                or ip.is_link_local or ip.is_multicast or ip.is_unspecified
                or (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None)
                or address[1] != 443 or (len(address) > 3 and address[3])):
            raise CaptureProxyError()
        value = str(ip)
        if value not in result:
            result.append(value)
    return result


class _CaptureServer(socketserver.TCPServer):
    allow_reuse_address = False

    def __init__(self, owner):
        self.owner = owner
        self._slots = threading.BoundedSemaphore(32)
        self._threads = set()
        self._threads_lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _CaptureHandler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        thread = threading.Thread(target=self._request_thread,
                                  args=(request, client_address), daemon=True,
                                  name="wechat-reading-tunnel")
        with self._threads_lock:
            self._threads.add(thread)
        try:
            thread.start()
        except Exception:
            with self._threads_lock:
                self._threads.discard(thread)
            self._slots.release()
            self.shutdown_request(request)

    def _request_thread(self, request, address):
        try:
            self.finish_request(request, address)
        except Exception:
            pass
        finally:
            self.shutdown_request(request)
            with self._threads_lock:
                self._threads.discard(threading.current_thread())
            self._slots.release()

    def handle_error(self, request, client_address):
        pass


class _CaptureHandler(socketserver.BaseRequestHandler):
    def handle(self):
        owner = self.server.owner
        client = self.request
        upstream = None
        owner._track(client)
        owner._record_stage("proxy_connections")
        try:
            client.settimeout(5)
            raw = bytearray()
            while not raw.endswith(b"\r\n\r\n"):
                if owner._stopping.is_set() or len(raw) >= _HEADER_LIMIT:
                    return
                byte = client.recv(1)
                if not byte:
                    return
                raw.extend(byte)
            try:
                line = bytes(raw).split(b"\r\n", 1)[0].decode("ascii")
                method, authority, version = line.split(" ")
            except (ValueError, UnicodeError):
                client.sendall(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
                return
            host = _connect_host(authority)
            if method != "CONNECT" or version not in ("HTTP/1.0", "HTTP/1.1") or host is None:
                client.sendall(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
                return
            owner._record_stage("connect_requests")
            if host == _HOST:
                owner._record_stage("wechat_connects")
            try:
                addresses = owner._resolve_public(host)
                for address in addresses[:4]:
                    if owner._stopping.is_set():
                        return
                    try:
                        upstream = owner._socket_factory((address, 443), timeout=3)
                        owner._track(upstream)
                        break
                    except OSError:
                        continue
                if upstream is None:
                    raise CaptureProxyError()
                if host == _HOST:
                    original = upstream
                    upstream = owner._upstream_context.wrap_socket(upstream, server_hostname=_HOST)
                    owner._replace(original, upstream)
                    owner._record_stage("upstream_tls")
                    if upstream.selected_alpn_protocol() not in (None, "http/1.1"):
                        return
            except Exception:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
                return
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            observer = None
            if host == _HOST:
                original = client
                client = owner._server_context.wrap_socket(client, server_side=True)
                owner._replace(original, client)
                owner._record_stage("client_tls")
                if client.selected_alpn_protocol() not in (None, "http/1.1"):
                    return
                observer = _RequestObserver(
                    owner._on_capture,
                    lambda: owner._record_stage("http_requests"),
                    lambda: owner._record_stage("observer_disabled"))
            self._relay(owner, client, upstream, observer)
        except Exception:
            pass
        finally:
            for connection in (client, upstream):
                if connection is not None:
                    owner._untrack(connection)
                    try:
                        connection.close()
                    except OSError:
                        pass

    @staticmethod
    def _relay(owner, client, upstream, observer):
        peers = {client: upstream, upstream: client}
        while not owner._stopping.is_set():
            pending = [connection for connection in peers
                       if isinstance(connection, ssl.SSLSocket) and connection.pending()]
            ready = select.select(list(peers), [], [], 0 if pending else 0.25)[0]
            readable = list(dict.fromkeys(pending + ready))
            for connection in readable:
                try:
                    data = connection.recv(_CHUNK)
                except (ssl.SSLWantReadError, ssl.SSLWantWriteError, socket.timeout):
                    continue
                if not data:
                    return
                peers[connection].sendall(data)
                if observer is not None and connection is client:
                    observer.feed(data)


class WechatCaptureProxy:
    """Ephemeral listener. Stop only after the system network lease is released."""

    def __init__(self, server_context: ssl.SSLContext, on_capture: Callable[[str], None],
                 stop_event=None, resolver=None, socket_factory=None,
                 upstream_context: ssl.SSLContext | None = None, on_diagnostic=None):
        self._server_context = server_context
        self._on_capture = on_capture
        self._on_diagnostic = on_diagnostic
        self._stopping = stop_event if stop_event is not None else threading.Event()
        self._resolver = resolver or socket.getaddrinfo
        self._resolver_slots = threading.BoundedSemaphore(4)
        self._socket_factory = socket_factory or socket.create_connection
        if upstream_context is None:
            # Construct directly: create_default_context also opens an inherited
            # SSLKEYLOGFILE before it can be disabled by its caller.
            self._upstream_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            self._upstream_context.load_default_certs(ssl.Purpose.SERVER_AUTH)
        else:
            self._upstream_context = upstream_context
        if (not self._upstream_context.check_hostname
                or self._upstream_context.verify_mode != ssl.CERT_REQUIRED):
            raise CaptureProxyError()
        # Detection must never export TLS secrets, including diagnostic keylogs.
        self._upstream_context.keylog_filename = None
        self._server_context.keylog_filename = None
        self._upstream_context.set_alpn_protocols(["http/1.1"])
        self._server = None
        self._thread = None
        self._sockets = set()
        self._lock = threading.RLock()

    @property
    def port(self) -> int:
        return self._server.server_address[1] if self._server else 0

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stopping.is_set())

    def start(self):
        if self._server is not None or self._stopping.is_set():
            raise CaptureProxyError()
        try:
            self._server = _CaptureServer(self)
            self._thread = threading.Thread(target=self._server.serve_forever,
                                            kwargs={"poll_interval": 0.1}, daemon=True,
                                            name="wechat-reading-proxy")
            self._thread.start()
        except Exception:
            if self._server is not None:
                self._server.server_close()
            raise CaptureProxyError() from None
        return self

    def _track(self, connection):
        with self._lock:
            if self._stopping.is_set():
                connection.close()
                raise CaptureProxyError()
            self._sockets.add(connection)

    def _record_stage(self, stage: str):
        if stage not in _DIAGNOSTIC_STAGES or self._on_diagnostic is None:
            return
        try:
            self._on_diagnostic(stage)
        except Exception:
            pass

    def _resolve_public(self, host: str) -> list[str]:
        """Bound OS DNS latency without joining potentially stuck OS calls.

        At most four daemon calls exist per proxy. They receive a hostname only;
        late results are discarded and can never initiate a connection.
        """
        if self._stopping.is_set() or not self._resolver_slots.acquire(blocking=False):
            raise CaptureProxyError()
        done = threading.Event()
        expired = threading.Event()
        result = {}
        resolver, slots = self._resolver, self._resolver_slots

        def resolve():
            try:
                addresses = _public_addresses(host, resolver)
                if not expired.is_set():
                    result["addresses"] = addresses
            except Exception:
                pass
            finally:
                done.set()
                slots.release()

        worker = threading.Thread(target=resolve, daemon=True, name="wechat-reading-dns")
        try:
            worker.start()
        except Exception:
            slots.release()
            raise CaptureProxyError() from None
        deadline = time.monotonic() + _RESOLVE_TIMEOUT
        while not self._stopping.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if done.wait(min(0.1, remaining)):
                if time.monotonic() <= deadline and result.get("addresses"):
                    return result["addresses"]
                break
        expired.set()
        raise CaptureProxyError()

    def _untrack(self, connection):
        with self._lock:
            self._sockets.discard(connection)

    def _replace(self, original, replacement):
        with self._lock:
            self._sockets.discard(original)
            self._sockets.add(replacement)
            if self._stopping.is_set():
                replacement.close()
                raise CaptureProxyError()

    def stop(self) -> bool:
        """Idempotently close our listener and sockets; False permits a retry."""
        self._stopping.set()
        server, thread = self._server, self._thread
        if server is None:
            return True
        if thread and thread.is_alive():
            server.shutdown()
        server.server_close()
        with self._lock:
            connections = list(self._sockets)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
        deadline = time.monotonic() + 1
        if thread and thread.ident is not None:
            thread.join(max(0, deadline - time.monotonic()))
        with server._threads_lock:
            workers = list(server._threads)
        for worker in workers:
            if worker is not threading.current_thread():
                worker.join(max(0, deadline - time.monotonic()))
        with server._threads_lock:
            return not any(worker.is_alive() for worker in server._threads) and not (thread and thread.is_alive())
