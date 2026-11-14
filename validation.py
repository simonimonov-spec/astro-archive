"""Transport policy and portable data formats; no application imports."""
from __future__ import annotations
import ipaddress, json, re, socket, uuid
from pathlib import Path, PureWindowsPath

SHARE_MARKER = '.astroarchive-share.json'
ARCHIVE_MARKER = '.astroarchive-shared-archive.json'
KINDS = ('frames', 'stacks', 'photos')

def label(value, maximum=120):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError('Невалиден или празен текст.')
    return value.strip()

def child_name(value):
    value = label(value, 100)
    if value in ('.', '..') or value.endswith(('.', ' ')) or any(c in value for c in '/\\:*?"<>|') or value.startswith('.'):
        raise ValueError('Въведи само име на новата папка, без път.')
    if value.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'{p}{i}' for p in ('COM','LPT') for i in range(1,10)]}:
        raise ValueError('Това име е запазено от Windows.')
    return value

def share_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'AstroArchive-[A-Za-z0-9_-]{1,48}', value):
        raise ValueError('Името на дела трябва да започва с AstroArchive- и да съдържа латински букви, цифри, - или _.')
    return value

def lan(address, prefix):
    ip = ipaddress.ip_address(address)
    private = any(ip in ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')) if ip.version == 4 else False
    if not private or type(prefix) is not int or not 16 <= prefix <= 30:
        raise ValueError('Избери IPv4 адрес от домашната LAN мрежа и маска /16–/30.')
    network = ipaddress.ip_network(f'{ip}/{prefix}', strict=False)
    if ip in (network.network_address, network.broadcast_address):
        raise ValueError('Невалиден адрес на LAN компютър.')
    return str(ip), str(network)

def unc(value):
    if not isinstance(value, str) or len(value) > 1000 or any(ord(c) < 32 for c in value):
        raise ValueError('Невалиден мрежов адрес.')
    value = value.strip().replace('/', '\\')
    if not value.startswith('\\\\') or value.startswith(('\\\\?\\','\\\\.\\')) or ':' in value or any(c in value for c in '*?"<>|'):
        raise ValueError('Използвай адрес \\\\компютър\\AstroArchive-име, а не уеб адрес.')
    parts = value[2:].split('\\')
    if len(parts) < 2 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,252}', parts[0]) or any(p in ('','..','.') for p in parts):
        raise ValueError('Невалиден мрежов път.')
    share_name(parts[1])
    for part in parts[2:]:child_name(part)
    return '\\\\' + '\\'.join(parts), parts[0], parts[1]

def metadata(path, expected=None):
    path = Path(path)
    if path.stat().st_size > 16384:
        raise ValueError('Прекалено голямо описание на споделената папка.')
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('version') != 1 or data.get('kind') not in ('workspace','archive'):
        raise ValueError('Папката няма валидно описание от Shared Archives.')
    if expected and data['kind'] != expected:raise ValueError('Избери правилния вид споделена папка.')
    if data['kind'] == 'archive' and data.get('type') not in KINDS:raise ValueError('Непознат вид архив.')
    label(data.get('name'));uuid.UUID(data.get('id', ''))
    return data

def atomic(path, data):
    path = Path(path);temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as stream:
            json.dump(data,stream,ensure_ascii=False,indent=2)
            stream.flush()
            import os
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)
