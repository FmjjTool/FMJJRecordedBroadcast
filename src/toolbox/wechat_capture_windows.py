"""Session-scoped Windows network facilities for explicit reading detection.

L5 owner of the temporary proxy/certificate lease.  Nothing is queried or changed
at import or construction time.  The worker owns the lease and must keep its
proxy alive until ``release()["ok"]`` is true.  No application configuration,
account store, request data, or recovery journal is read or written here.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
from pathlib import Path
import re
import secrets
import ssl
import sys
import tempfile
from urllib.parse import urlsplit


_HOST = "mp.weixin.qq.com"
_ERRORS = {
    "unsupported": "自动检测暂时只支持 Windows。",
    "invalid_port": "检测服务端口无效，请重新开始。",
    "invalid_certificate": "检测证书无效，请重新开始。",
    "busy": "已有自动检测正在运行，请先结束该检测。",
    "existing_proxy": "当前已启用系统代理或自动代理，请暂时关闭后重试。",
    "proxy_read_failed": "无法读取系统网络设置，尚未开始检测。",
    "proxy_write_failed": "无法应用检测所需的临时网络设置。",
    "proxy_restore_failed": "网络设置尚未恢复，请保持检测窗口开启。",
    "certificate_prepare_failed": "无法准备临时检测证书，请稍后重试。",
    "certificate_install_failed": "未能信任临时检测证书，检测未开始。",
    "certificate_remove_failed": "临时检测证书尚未移除，请保持检测窗口开启。",
    "certificate_cleanup_failed": "临时检测文件尚未清理，请稍后重试。",
    "cleanup_pending": "临时网络设置尚未清理，请保持检测窗口开启。",
    "native_failed": "系统暂时无法启动自动检测，请稍后重试。",
}


class CaptureNativeError(RuntimeError):
    """A safe error: never includes native exception text or system values."""

    def __init__(self, code: str):
        self.code = code if code in _ERRORS else "native_failed"
        super().__init__(_ERRORS[self.code])


def _private_directory(path: Path) -> None:
    """Restrict only our new temporary directory before writing encrypted PEM."""
    if sys.platform != "win32":
        path.chmod(0o700)
        return
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    convert.restype = wintypes.BOOL
    set_security = advapi.SetFileSecurityW
    set_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    set_security.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    # Protected DACL: LocalSystem and this directory's owner only.
    if not convert("D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;OW)", 1, ctypes.byref(descriptor), None):
        raise CaptureNativeError("certificate_prepare_failed")
    try:
        if not set_security(str(path), 0x00000004 | 0x80000000, descriptor):
            raise CaptureNativeError("certificate_prepare_failed")
    finally:
        kernel.LocalFree(descriptor)


class CaptureCertificate:
    """One short-lived CA and one leaf for the exact reading host.

    The CA private key is never serialized.  The encrypted leaf PEM exists only
    while SSL loads it and is deleted before ``prepare`` returns.
    """

    def __init__(self):
        self.context: ssl.SSLContext | None = None
        self.root_der = b""
        self._directory: Path | None = None

    def prepare(self) -> CaptureCertificate:
        if self.context is not None:
            return self
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

            now = datetime.now(timezone.utc)
            suffix = secrets.token_hex(12)
            root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "FMJJ Reading Detection " + suffix)])
            root = (
                x509.CertificateBuilder().subject_name(root_name).issuer_name(root_name)
                .public_key(root_key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=2)).not_valid_after(now + timedelta(hours=1))
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
                .add_extension(x509.NameConstraints([x509.DNSName(_HOST)], None), critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(root_key.public_key()), critical=False)
                .sign(root_key, hashes.SHA256())
            )
            leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            leaf = (
                x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, _HOST)]))
                .issuer_name(root_name).public_key(leaf_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=2)).not_valid_after(now + timedelta(hours=1))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(_HOST)]), critical=False)
                .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()), critical=False)
                .sign(root_key, hashes.SHA256())
            )
            self._directory = Path(tempfile.mkdtemp(prefix="fmjj-reading-"))
            _private_directory(self._directory)
            cert_path = self._directory / "chain.pem"
            key_path = self._directory / "key.pem"
            password = secrets.token_hex(32).encode("ascii")
            cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM) + root.public_bytes(serialization.Encoding.PEM))
            key_path.write_bytes(leaf_key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.BestAvailableEncryption(password),
            ))
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.set_alpn_protocols(["http/1.1"])
            context.load_cert_chain(str(cert_path), str(key_path), password=password)
            self._remove_temporary_files()
            self.root_der = root.public_bytes(serialization.Encoding.DER)
            self.context = context
            return self
        except Exception:
            self.close()
            raise CaptureNativeError("certificate_prepare_failed") from None

    def _remove_temporary_files(self) -> None:
        if self._directory is None:
            return
        failed = False
        for name in ("key.pem", "chain.pem"):
            try:
                (self._directory / name).unlink(missing_ok=True)
            except OSError:
                failed = True
        try:
            self._directory.rmdir()
        except OSError:
            failed = True
        if failed:
            raise CaptureNativeError("certificate_cleanup_failed")
        self._directory = None

    def close(self) -> None:
        self.context = None
        self.root_der = b""
        self._remove_temporary_files()


@dataclass(frozen=True)
class ProxySettings:
    """In-memory WinINet snapshot; never serialize or log this object."""

    flags: int
    server: str
    bypass: str
    autoconfig_url: str


def _validate_root(root_der: bytes) -> None:
    try:
        from cryptography import x509
        if not isinstance(root_der, bytes) or not 100 <= len(root_der) <= 16384:
            raise ValueError
        root = x509.load_der_x509_certificate(root_der)
        constraints = root.extensions.get_extension_for_class(x509.BasicConstraints)
        names = root.extensions.get_extension_for_class(x509.NameConstraints)
        permitted = names.value.permitted_subtrees
        if root.subject != root.issuer or not constraints.value.ca or not names.critical:
            raise ValueError
        if permitted != [x509.DNSName(_HOST)] or names.value.excluded_subtrees:
            raise ValueError
    except Exception:
        raise CaptureNativeError("invalid_certificate") from None


class WindowsCaptureLease:
    """Own only settings installed by this session; support cleanup retries.

    ``api`` is a fakeable OS boundary.  Allocate this object before calling
    ``acquire`` so a partially failed acquisition can still be released.
    """

    def __init__(self, api=None):
        self._api = api
        self._mutex = None
        self._started = False
        self._original: ProxySettings | None = None
        self._installed: ProxySettings | None = None
        self._proxy_pending = False
        self._certificate_pending = False
        self._root_der = b""
        self._external_change = False

    def acquire(self, port: int, root_der: bytes) -> WindowsCaptureLease:
        if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
            raise CaptureNativeError("invalid_port")
        _validate_root(root_der)
        if self._started:
            raise CaptureNativeError("busy")
        self._started = True
        try:
            if self._api is None:
                self._api = _WindowsAPI()
            self._mutex = self._api.acquire_mutex()
            self._original = self._api.read_proxy()
            if self._original.flags & (2 | 4):
                raise CaptureNativeError("existing_proxy")
            self._installed = ProxySettings(3, f"https=127.0.0.1:{port}", "<local>;localhost;127.*;[::1]", "")
            self._root_der = root_der
            # Mark before calls which can fail after a native write.
            self._certificate_pending = True
            self._api.add_certificate(root_der)
            self._proxy_pending = True
            self._api.set_proxy(self._installed)
            if self._api.read_proxy() != self._installed:
                raise CaptureNativeError("proxy_write_failed")
            return self
        except Exception as error:
            code = error.code if isinstance(error, CaptureNativeError) else "native_failed"
            if not self.release()["ok"]:
                code = "cleanup_pending"
            raise CaptureNativeError(code) from None

    def _contains_own_endpoint(self, current: ProxySettings) -> bool:
        if self._installed is None:
            return False
        port = int(self._installed.server.rsplit(":", 1)[1])
        for entry in re.split(r"[;\s]+", current.server.casefold()):
            value = entry.rsplit("=", 1)[-1]
            try:
                parsed = urlsplit(value if "://" in value else "//" + value)
                if parsed.port != port:
                    continue
                host = (parsed.hostname or "").rstrip(".")
                try:
                    address = ipaddress.ip_address(host)
                except ValueError:
                    # A hosts-file/DNS alias can still point at this listener.
                    # Do not resolve it or assume an unfamiliar name is remote.
                    return True
                mapped = getattr(address, "ipv4_mapped", None)
                if address.is_loopback or address.is_unspecified or (mapped and mapped.is_loopback):
                    return True
            except ValueError:
                # Malformed external values with our port are also uncertain.
                if re.search(rf":{port}(?:[^0-9]|$)", value):
                    return True
        return False

    def release(self) -> dict:
        if self._api is not None and self._proxy_pending:
            try:
                current = self._api.read_proxy()
                if current == self._installed or current == self._original:
                    self._api.set_proxy(self._original)
                    if self._api.read_proxy() != self._original:
                        raise CaptureNativeError("proxy_restore_failed")
                    self._proxy_pending = False
                elif self._contains_own_endpoint(current):
                    self._external_change = True
                else:
                    # Preserve a user's new proxy instead of overwriting it.
                    self._external_change = True
                    self._proxy_pending = False
            except Exception:
                pass
        # Keep trust and the proxy alive together until our endpoint is gone.
        if self._api is not None and not self._proxy_pending and self._certificate_pending:
            try:
                if self._api.remove_certificate(self._root_der):
                    self._certificate_pending = False
                    self._root_der = b""
            except Exception:
                pass
        if self._api is not None and not self._proxy_pending and not self._certificate_pending and self._mutex is not None:
            try:
                self._api.release_mutex(self._mutex)
                self._mutex = None
            except Exception:
                pass
        ok = not self._proxy_pending and not self._certificate_pending and self._mutex is None
        message = "检测期间的临时设置已清理。" if ok else _ERRORS["cleanup_pending"]
        if self._external_change and self._proxy_pending:
            message = "网络设置在检测期间发生变化，请检查系统代理并保持检测窗口开启。"
        return {
            "ok": ok,
            "proxy_restored": not self._proxy_pending,
            "certificate_removed": not self._certificate_pending,
            "external_change": self._external_change,
            "message": message,
        }


class _OptionValue(ctypes.Union):
    _fields_ = [("dwValue", wintypes.DWORD), ("pszValue", ctypes.c_void_p), ("ftValue", wintypes.FILETIME)]


class _Option(ctypes.Structure):
    _fields_ = [("dwOption", wintypes.DWORD), ("value", _OptionValue)]


class _OptionList(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("pszConnection", wintypes.LPWSTR),
                ("dwOptionCount", wintypes.DWORD), ("dwOptionError", wintypes.DWORD),
                ("pOptions", ctypes.POINTER(_Option))]


class _HashBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


class _CertificateContext(ctypes.Structure):
    _fields_ = [("dwCertEncodingType", wintypes.DWORD), ("pbCertEncoded", ctypes.POINTER(ctypes.c_ubyte)),
                ("cbCertEncoded", wintypes.DWORD), ("pCertInfo", ctypes.c_void_p), ("hCertStore", ctypes.c_void_p)]


class _WindowsAPI:
    """Thin ctypes adapter; no native operation runs until a method is called."""

    def __init__(self):
        if sys.platform != "win32":
            raise CaptureNativeError("unsupported")
        self._inet = ctypes.WinDLL("wininet", use_last_error=True)
        self._crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._bind()

    def _bind(self):
        void = ctypes.c_void_p
        dword = wintypes.DWORD
        self._inet.InternetQueryOptionW.argtypes = [void, dword, void, ctypes.POINTER(dword)]
        self._inet.InternetQueryOptionW.restype = wintypes.BOOL
        self._inet.InternetSetOptionW.argtypes = [void, dword, void, dword]
        self._inet.InternetSetOptionW.restype = wintypes.BOOL
        self._kernel.GlobalFree.argtypes = [void]
        self._kernel.GlobalFree.restype = void
        self._kernel.CreateMutexW.argtypes = [void, wintypes.BOOL, wintypes.LPCWSTR]
        self._kernel.CreateMutexW.restype = void
        self._kernel.WaitForSingleObject.argtypes = [void, dword]
        self._kernel.WaitForSingleObject.restype = dword
        self._kernel.ReleaseMutex.argtypes = [void]
        self._kernel.ReleaseMutex.restype = wintypes.BOOL
        self._kernel.CloseHandle.argtypes = [void]
        self._kernel.CloseHandle.restype = wintypes.BOOL
        self._crypt.CertOpenStore.argtypes = [void, dword, void, dword, void]
        self._crypt.CertOpenStore.restype = void
        self._crypt.CertCloseStore.argtypes = [void, dword]
        self._crypt.CertCloseStore.restype = wintypes.BOOL
        self._crypt.CertAddEncodedCertificateToStore.argtypes = [void, dword, ctypes.POINTER(ctypes.c_ubyte), dword, dword, ctypes.POINTER(void)]
        self._crypt.CertAddEncodedCertificateToStore.restype = wintypes.BOOL
        self._crypt.CertFindCertificateInStore.argtypes = [void, dword, dword, dword, void, void]
        self._crypt.CertFindCertificateInStore.restype = void
        self._crypt.CertFreeCertificateContext.argtypes = [void]
        self._crypt.CertFreeCertificateContext.restype = wintypes.BOOL
        self._crypt.CertDeleteCertificateFromStore.argtypes = [void]
        self._crypt.CertDeleteCertificateFromStore.restype = wintypes.BOOL

    def acquire_mutex(self):
        handle = self._kernel.CreateMutexW(None, False, r"Local\FMJJ.WechatReadingDetection.v1")
        if not handle:
            raise CaptureNativeError("busy")
        result = self._kernel.WaitForSingleObject(handle, 0)
        if result not in (0, 0x80):
            self._kernel.CloseHandle(handle)
            raise CaptureNativeError("busy")
        return handle

    def release_mutex(self, handle):
        if not self._kernel.ReleaseMutex(handle):
            raise CaptureNativeError("cleanup_pending")
        self._kernel.CloseHandle(handle)

    def _query_proxy(self, flags_option: int) -> ProxySettings:
        options = (_Option * 4)()
        for item, option in zip(options, (flags_option, 2, 3, 4)):
            item.dwOption = option
        listing = _OptionList(ctypes.sizeof(_OptionList), None, 4, 0, options)
        size = wintypes.DWORD(ctypes.sizeof(listing))
        try:
            if not self._inet.InternetQueryOptionW(None, 75, ctypes.byref(listing), ctypes.byref(size)):
                raise CaptureNativeError("proxy_read_failed")
            strings = [ctypes.wstring_at(options[i].value.pszValue) if options[i].value.pszValue else "" for i in (1, 2, 3)]
            return ProxySettings(int(options[0].value.dwValue), *strings)
        finally:
            for i in (1, 2, 3):
                if options[i].value.pszValue:
                    self._kernel.GlobalFree(options[i].value.pszValue)

    def read_proxy(self) -> ProxySettings:
        try:
            return self._query_proxy(10)  # FLAGS_UI, with legacy FLAGS fallback.
        except CaptureNativeError:
            return self._query_proxy(1)

    def set_proxy(self, state: ProxySettings):
        options = (_Option * 4)()
        options[0].dwOption = 1
        options[0].value.dwValue = state.flags
        buffers = [ctypes.create_unicode_buffer(value) for value in (state.server, state.bypass, state.autoconfig_url)]
        for item, option, value in zip(options[1:], (2, 3, 4), buffers):
            item.dwOption = option
            item.value.pszValue = ctypes.cast(value, ctypes.c_void_p).value
        listing = _OptionList(ctypes.sizeof(_OptionList), None, 4, 0, options)
        if not self._inet.InternetSetOptionW(None, 75, ctypes.byref(listing), ctypes.sizeof(listing)):
            raise CaptureNativeError("proxy_write_failed")
        for option in (39, 37):  # SETTINGS_CHANGED then REFRESH.
            if not self._inet.InternetSetOptionW(None, option, None, 0):
                raise CaptureNativeError("proxy_write_failed")

    def _open_root(self):
        # Explicit CurrentUser, existing ROOT store; never LocalMachine.
        name = ctypes.create_unicode_buffer("ROOT")
        store = self._crypt.CertOpenStore(ctypes.c_void_p(10), 0, None, 0x10000 | 0x4000, ctypes.cast(name, ctypes.c_void_p))
        if not store:
            raise CaptureNativeError("certificate_install_failed")
        return store

    def add_certificate(self, root_der: bytes):
        store = self._open_root()
        context = ctypes.c_void_p()
        encoded = (ctypes.c_ubyte * len(root_der)).from_buffer_copy(root_der)
        try:
            if not self._crypt.CertAddEncodedCertificateToStore(store, 0x10001, encoded, len(root_der), 1, ctypes.byref(context)):
                raise CaptureNativeError("certificate_install_failed")
        finally:
            if context.value:
                self._crypt.CertFreeCertificateContext(context)
            self._crypt.CertCloseStore(store, 0)

    def _find_certificate(self, store, root_der: bytes):
        digest = hashlib.sha1(root_der).digest()
        data = (ctypes.c_ubyte * len(digest)).from_buffer_copy(digest)
        blob = _HashBlob(len(digest), data)
        context = self._crypt.CertFindCertificateInStore(store, 0x10001, 0, 0x10000, ctypes.byref(blob), None)
        if not context and (ctypes.get_last_error() & 0xffffffff) not in (0x80092004, 1168):
            raise CaptureNativeError("certificate_remove_failed")
        return context

    def remove_certificate(self, root_der: bytes) -> bool:
        store = self._open_root()
        context = None
        try:
            context = self._find_certificate(store, root_der)
            if not context:
                return True
            certificate = ctypes.cast(context, ctypes.POINTER(_CertificateContext)).contents
            if ctypes.string_at(certificate.pbCertEncoded, certificate.cbCertEncoded) != root_der:
                raise CaptureNativeError("certificate_remove_failed")
            owned_context, context = context, None
            # Delete consumes the context even when the call reports failure.
            if not self._crypt.CertDeleteCertificateFromStore(owned_context):
                raise CaptureNativeError("certificate_remove_failed")
            context = self._find_certificate(store, root_der)
            return not context
        finally:
            if context:
                self._crypt.CertFreeCertificateContext(context)
            self._crypt.CertCloseStore(store, 0)
