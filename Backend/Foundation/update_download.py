"""Stream a verified official Windows release to a separate local ZIP."""
import hashlib
from pathlib import Path
import re
import urllib.parse
import urllib.request
import uuid

REPOSITORY='simonimonov-spec/astro-archive'
MAX_BYTES=1_000_000_000


def package_for(release):
    if not release:return None
    tag=release.get('tag_name','')
    if not isinstance(tag,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+',tag):return None
    packages=[]
    for asset in release.get('assets',[]):
        name=asset.get('name','');digest=asset.get('digest','');size=asset.get('size')
        if not isinstance(name,str) or not re.fullmatch(r'AstroArchive_[A-Za-z0-9_.-]+_Windows_x64\.zip',name):continue
        url=f'https://github.com/{REPOSITORY}/releases/download/{tag}/{name}'
        if asset.get('browser_download_url')!=url or type(size) is not int or not 0<size<=MAX_BYTES:continue
        if not isinstance(digest,str) or not re.fullmatch(r'sha256:[a-fA-F0-9]{64}',digest):continue
        packages.append(dict(name=name,url=url,size=size,sha256=digest[7:].lower()))
    return packages[0] if len(packages)==1 else None


def download(package,folder,cancel,progress):
    folder=Path(folder)
    if folder.is_symlink() or getattr(folder,'is_junction',lambda:False)():raise ValueError('Папката за обновявания не може да е връзка.')
    if cancel.is_set():raise InterruptedError('Изтеглянето е отменено.')
    folder.mkdir(parents=True,exist_ok=True)
    destination=folder/package['name']
    if destination.exists():
        if destination.is_file() and not destination.is_symlink() and destination.stat().st_size==package['size']:
            digest=hashlib.sha256()
            with destination.open('rb') as stream:
                while chunk:=stream.read(131072):
                    if cancel.is_set():raise InterruptedError('Изтеглянето е отменено.')
                    digest.update(chunk)
            if digest.hexdigest()==package['sha256']:
                progress(package['size'],package['size']);return str(destination)
        destination=folder/(destination.stem+'-'+uuid.uuid4().hex[:8]+'.zip')
    temporary=folder/('.update-'+uuid.uuid4().hex+'.part')
    try:
        request=urllib.request.Request(package['url'],headers={'User-Agent':'AstroArchive','Accept':'application/octet-stream'})
        digest=hashlib.sha256();received=0
        with urllib.request.urlopen(request,timeout=15) as response,temporary.open('xb') as output:
            final=urllib.parse.urlsplit(response.url)
            if final.scheme!='https' or final.hostname not in {'github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'}:raise ValueError('Невалиден адрес на Windows пакета.')
            while True:
                if cancel.is_set():raise InterruptedError('Изтеглянето е отменено.')
                chunk=response.read(131072)
                if not chunk:break
                received+=len(chunk)
                if received>package['size']:raise ValueError('Размерът на изтегления пакет не съвпада.')
                output.write(chunk);digest.update(chunk);progress(received,package['size'])
        if cancel.is_set():raise InterruptedError('Изтеглянето е отменено.')
        if received!=package['size'] or digest.hexdigest()!=package['sha256']:raise ValueError('Пакетът е непълен или SHA-256 не съвпада. Опитай отново.')
        temporary.rename(destination)
        return str(destination)
    finally:
        temporary.unlink(missing_ok=True)
        try:folder.rmdir()
        except OSError:pass
