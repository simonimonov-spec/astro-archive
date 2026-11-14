"""Trusted TLS owned by Remote Operation; no proxy or system configuration changes.

Tailscale must already authorize certificates for this node. Private keys are
stored with Windows current-user DPAPI, never in plugin settings or UI payloads.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import tempfile
import threading
import time


class SecurityError(ValueError):
    pass


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def protect(data: bytes, *, decrypt=False) -> bytes:
    """DPAPI user scope. Fail closed on other systems or Windows profiles."""
    if os.name != 'nt':
        raise SecurityError('Защитата на ключа изисква Windows DPAPI.')
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, output = Blob(len(data), buffer), Blob()
    if decrypt:
        call = crypt.CryptUnprotectData
        call.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = call(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    else:
        call = crypt.CryptProtectData
        call.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = call(ctypes.byref(source), 'Astro Archive Remote TLS', None, None, None, 1, ctypes.byref(output))
    if not ok:
        raise SecurityError('Windows не може да отключи TLS ключа за този потребител. Издай сертификат на този компютър.')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(ctypes.cast(output.data, wintypes.HLOCAL))


def private_directory(path: Path):
    """Restrict only this plugin-owned directory to current user and SYSTEM."""
    if os.name != 'nt':
        raise SecurityError('Защитената папка за TLS изисква Windows.')
    path.mkdir(parents=True, exist_ok=True)
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    adv.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    token, length, sid_text, descriptor = wintypes.HANDLE(), wintypes.DWORD(), wintypes.LPWSTR(), ctypes.c_void_p()
    try:
        if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(length))
        info = ctypes.create_string_buffer(length.value)
        if not adv.GetTokenInformation(token, 1, info, length, ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.cast(info, ctypes.POINTER(ctypes.c_void_p))[0]
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise ctypes.WinError(ctypes.get_last_error())
        sddl = 'D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;' + sid_text.value + ')'
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if not adv.SetFileSecurityW(str(path), 0x80000004, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    except OSError:
        raise SecurityError('Не може да се ограничи достъпът до папката за TLS ключа.') from None
    finally:
        if token: kernel.CloseHandle(token)
        if sid_text: kernel.LocalFree(ctypes.cast(sid_text, wintypes.HLOCAL))
        if descriptor: kernel.LocalFree(descriptor)


def trusted_client_context():
    # Python's Windows default loader also treats the CA (intermediate) store as
    # trust anchors. Stale cross-signed intermediates there can shadow the valid
    # chain served by the node. Anchor at the existing Windows ROOT store only;
    # retain full signature, expiry, purpose and hostname validation.
    roots = ''.join(ssl.DER_cert_to_PEM_cert(cert)
                    for cert, kind, trust in ssl.enum_certificates('ROOT')
                    if kind == 'x509_asn' and (trust is True or ssl.Purpose.SERVER_AUTH.oid in trust))
    if not roots:
        raise SecurityError('Windows няма достъпно хранилище с доверени коренни сертификати.')
    return ssl.create_default_context(cadata=roots)


def trusted_certificate(context: ssl.SSLContext, hostname: str) -> dict:
    """Real TLS handshake: system CA trust, validity and exact DNS identity."""
    errors = []
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0)); listener.listen(1); listener.settimeout(5)
        def accept():
            try:
                raw, _ = listener.accept()
                with raw:
                    raw.settimeout(5)
                    with context.wrap_socket(raw, server_side=True):
                        pass
            except (OSError, ssl.SSLError) as error:
                errors.append(type(error).__name__)
        worker = threading.Thread(target=accept, daemon=True)
        worker.start()
        try:
            with socket.create_connection(listener.getsockname(), timeout=5) as raw:
                with trusted_client_context().wrap_socket(raw, server_hostname=hostname) as stream:
                    certificate = stream.getpeercert()
        except (OSError, ssl.SSLError):
            raise SecurityError('Сертификатът не е доверен, е изтекъл или не съответства на DNS името. HTTP няма да бъде включен.') from None
        finally:
            worker.join(6)
    if errors:
        raise SecurityError('Проверката на защитената TLS връзка е неуспешна.')
    return certificate


class TLSIdentity:
    def __init__(self, folder, options):
        self.folder = Path(folder) / 'TLS'
        self.options = options
        self.context = None
        self.hostname = ''
        self.expires = 0
        self.problem = ''
        self.lock = threading.RLock()

    @staticmethod
    def executable():
        path = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Tailscale/tailscale.exe'
        if not path.is_file():
            raise SecurityError('Не е намерен Tailscale. Инсталирай и свържи Tailscale на двете устройства.')
        return str(path)

    @staticmethod
    def run(arguments, timeout=20):
        try:
            result = subprocess.run(arguments, capture_output=True, timeout=timeout,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            raise SecurityError('Tailscale не отговори за HTTPS сертификата. Провери дали е включен.') from None
        if result.returncode:
            # Never expose stdout/stderr: certificate commands can contain keys.
            raise SecurityError('Tailscale не предостави HTTPS сертификат. Провери включения VPN и HTTPS Certificates за този компютър.')
        return result.stdout

    def node_hostname(self):
        data = json.loads(self.run([self.executable(), 'status', '--json']))
        name = str(data.get('Self', {}).get('DNSName', '')).lower().rstrip('.')
        domains = [str(v).lower().rstrip('.') for v in data.get('CertDomains', [])]
        addresses = data.get('Self', {}).get('TailscaleIPs', data.get('TailscaleIPs', []))
        if data.get('BackendState') != 'Running' or self.options['vpnAddress'] not in addresses:
            raise SecurityError('Избраният Tailscale адрес не е активният адрес на този компютър.')
        if not name.endswith('.ts.net') or name not in domains:
            raise SecurityError('Tailscale HTTPS Certificates не е включен за този компютър. Не се променят автоматично VPN настройки.')
        return name

    def load_context(self, bundle):
        private_directory(self.folder)
        with tempfile.TemporaryDirectory(prefix='load-', dir=self.folder) as temp:
            directory = Path(temp); private_directory(directory)
            cert, key = directory / 'chain.pem', directory / 'key.pem'
            cert.write_text(bundle['certificate'], encoding='ascii')
            key.write_text(bundle['key'], encoding='ascii')
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.options |= ssl.OP_NO_COMPRESSION | getattr(ssl, 'OP_NO_RENEGOTIATION', 0)
            context.set_ciphers('ECDHE+AESGCM:ECDHE+CHACHA20')
            try:
                context.load_cert_chain(cert, key)
            except (OSError, ssl.SSLError):
                raise SecurityError('Невалиден TLS сертификат или частен ключ.') from None
        certificate = trusted_certificate(context, bundle['hostname'])
        expires = ssl.cert_time_to_seconds(certificate['notAfter'])
        name = bundle['hostname']
        def check_name(stream, requested, original):
            if requested != name:
                return ssl.ALERT_DESCRIPTION_UNRECOGNIZED_NAME
        context.set_servername_callback(check_name)
        return context, expires

    def issue(self, hostname):
        with tempfile.TemporaryDirectory(prefix='issue-', dir=self.folder) as temp:
            directory = Path(temp); private_directory(directory)
            certificate = directory / 'certificate.pem'
            output = self.run([self.executable(), 'cert', '--cert-file', str(certificate),
                               '--key-file', '-', '--min-validity', '720h', hostname], timeout=55)
            match = re.search(rb'-----BEGIN (?:EC |RSA )?PRIVATE KEY-----[\s\S]+?-----END (?:EC |RSA )?PRIVATE KEY-----', output)
            if not match or not certificate.is_file():
                raise SecurityError('Tailscale не върна пълен сертификат и ключ.')
            return dict(hostname=hostname, certificate=certificate.read_text(encoding='ascii'),
                        key=match.group().decode('ascii'))

    def prepare(self):
        with self.lock:
            private_directory(self.folder)
            vpn = self.options.get('mode') == 'vpn'
            hostname = self.node_hostname() if vpn else str(self.options.get('dns', '')).lower().rstrip('.')
            if not hostname or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', hostname) or '.' not in hostname:
                raise SecurityError('За HTTPS посочи пълно DNS име с доверен сертификат. IP адрес не може да замести името.')
            path = self.folder / ('tailscale.dpapi' if vpn else 'lan.dpapi')
            bundle, context, expires = None, None, 0
            if path.exists():
                try:
                    saved = json.loads(protect(path.read_bytes(), decrypt=True))
                    if saved['hostname'] == hostname:
                        context, expires = self.load_context(saved); bundle = saved
                except (SecurityError, KeyError, ValueError):
                    pass  # Reissue locally; a copied DPAPI bundle cannot be reused.
            self.problem = ''
            if not bundle or expires < time.time() + 30 * 86400 or not vpn:
                try:
                    if vpn:
                        fresh = self.issue(hostname)
                    else:
                        fresh = dict(hostname=hostname,
                                     certificate=Path(self.options.get('certificateFile', '')).read_text(encoding='ascii'),
                                     key=Path(self.options.get('privateKeyFile', '')).read_text(encoding='ascii'))
                    new_context, new_expires = self.load_context(fresh)
                    temporary = path.with_suffix('.new')
                    temporary.write_bytes(protect(json.dumps(fresh).encode('utf-8')))
                    temporary.replace(path)
                    bundle, context, expires = fresh, new_context, new_expires
                except (OSError, SecurityError, ValueError):
                    if not context or expires <= time.time():
                        raise SecurityError('Не е готов доверен HTTPS сертификат. Провери Tailscale или LAN сертификата. HTTP остава изключен.') from None
                    self.problem = 'Подновяването на TLS сертификата е неуспешно; текущият още е валиден. Провери Tailscale или LAN сертификата.'
            self.hostname, self.context, self.expires = hostname, context, expires
            return context

    def status(self):
        return dict(secure=bool(self.context and self.expires > time.time()), hostname=self.hostname,
                    certificateExpires=time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(self.expires)) if self.expires else '',
                    problem=self.problem)
