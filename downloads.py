"""Bounded streaming ZIP transfer, safe extraction and atomic set commits."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import time
import unicodedata
import uuid
import zipfile

MARKER = '.iasc-set.json'
FITS = {'.fit','.fits','.fts','.fz'}
ANCILLARY = {'.txt','.csv','.json','.md','.dat'}


def slug(value):
    value = unicodedata.normalize('NFC',value)
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip(' .')[:70]
    if not value or re.match(r'(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)',value):
        value = '_'+value
    return value


def check_cancel(cancel):
    if cancel.is_set():
        raise InterruptedError('Изтеглянето е отменено.')


def digest_file(path, cancel):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024*1024):
            check_cancel(cancel)
            digest.update(chunk)
    return digest.hexdigest()


def checked_members(archive, limit):
    names, total, files = set(), 0, []
    for info in archive.infolist():
        name = info.filename.replace('\\','/')
        path = PurePosixPath(name)
        if path.is_absolute() or not path.parts or any(p in ('..','.') or ':' in p or re.search(r'[\x00-\x1f<>"|?*]',p)
                or p.rstrip(' .')!=p or re.match(r'(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)',p) for p in path.parts):
            raise ValueError('ZIP съдържа небезопасен файлов път.')
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0,stat.S_IFREG,stat.S_IFDIR)) or info.flag_bits&1:
            raise ValueError('ZIP съдържа връзка, специален или защитен файл.')
        key = name.rstrip('/').casefold()
        if key in names:
            raise ValueError('ZIP съдържа повторени имена на файлове.')
        names.add(key)
        if info.is_dir():
            continue
        total += info.file_size
        if total>limit or len(files)>=1000:
            raise ValueError('Разархивирането надвишава зададения лимит.')
        if path.suffix.lower() not in FITS|ANCILLARY:
            raise ValueError('ZIP съдържа неподдържан тип файл: '+path.suffix[:12])
        files.append((info,path))
    if not any(p.suffix.lower() in FITS for _,p in files):
        raise ValueError('Комплектът не съдържа FITS файлове.')
    return files,total


def verify_fits(path):
    from astropy.io import fits
    try:
        with fits.open(path,mode='readonly',memmap=True,do_not_scale_image_data=True) as hdus:
            if not any(int(h.header.get('NAXIS',0))>=2 or bool(h.header.get('ZIMAGE')) for h in hdus):
                raise ValueError('FITS файлът не съдържа изображение: '+path.name)
    except OSError:
        raise ValueError('Невалиден FITS файл: '+path.name) from None


def verify_target(row, cancel, full=False):
    target = Path(row['target'])
    if not row['target'] or not target.is_dir() or target.is_symlink() or target.is_junction():
        return False
    try:
        if (target/MARKER).stat().st_size>2_000_000:
            return False
        marker = json.loads((target/MARKER).read_text(encoding='utf-8'))
        if marker.get('id')!=row['id']:
            return False
        files = marker['files']
        if not files:
            return False
        for item in files:
            path = target/item['name']
            if not path.resolve().is_relative_to(target.resolve()) or path.is_symlink() or path.stat().st_size!=item['size']:
                return False
            if full and digest_file(path,cancel)!=item['sha256']:
                return False
        return True
    except (OSError,ValueError,KeyError,TypeError):
        return False


def download(client, row, root, cache, cancel, progress, limit_mib=2048):
    root = Path(root).resolve(strict=True)
    # Existing user files, even in a same-named directory, are never replaced.
    relative = Path(slug(row['campaign_name'])+'-'+row['campaign']) / (slug(row['team_name'])+'-'+row['team']) / (slug(row['name'])+'-'+row['id'][:10])
    target = root/relative
    if not target.resolve().is_relative_to(root):
        raise ValueError('Невалидна папка за комплекта.')
    for path in target.parents:
        if path.is_symlink() or path.is_junction():
            raise ValueError('Архивна папка не може да сочи през символна връзка.')
        if path==root:
            break
    if target.exists():
        existing = dict(row,target=str(target))
        if verify_target(existing,cancel,full=True):
            saved = json.loads((target/MARKER).read_text(encoding='utf-8'))
            return dict(target=str(target),manifest=json.dumps(saved['files']),digest=saved['zipSha256'],downloaded=saved['downloaded'])
        raise ValueError('Папката на комплекта вече съществува и не е потвърдена като същото изтегляне. Файловете са запазени.')
    if not row['url']:
        raise ValueError('IASC не предостави разпознат линк за изтегляне на този комплект.')
    cache = Path(cache)
    cache.mkdir(parents=True,exist_ok=True)
    archive_path = cache/(uuid.uuid4().hex+'.part')
    staging = None
    try:
        limit, size, digest = limit_mib*1024**2, 0, hashlib.sha256()
        with client.request(row['url']) as response, archive_path.open('xb') as output:
            total = int(response.headers.get('Content-Length','0'))
            if total>limit:
                raise ValueError('ZIP файлът надвишава зададения лимит за изтегляне.')
            if shutil.disk_usage(cache).free<(total or limit)+64*1024**2:
                raise ValueError('Няма достатъчно място за временния ZIP файл.')
            last_progress = 0
            while chunk := response.read(1024*1024):
                check_cancel(cancel)
                size += len(chunk)
                if size>limit:
                    raise ValueError('Изтеглянето надвишава зададения лимит.')
                output.write(chunk)
                digest.update(chunk)
                if time.monotonic()-last_progress>.35:
                    progress(size,total or max(size+1,1),f"Изтегляне на {row['name']} · {size/1024**2:.1f} MB")
                    last_progress = time.monotonic()
            output.flush()
            os.fsync(output.fileno())
            if total and size!=total:
                raise ValueError('Изтеглянето е непълно. Комплектът не е добавен в архива.')
        check_cancel(cancel)
        with zipfile.ZipFile(archive_path) as archive:
            members, uncompressed = checked_members(archive,limit*4)
            if shutil.disk_usage(root).free<uncompressed+64*1024**2:
                raise ValueError('Няма достатъчно свободно място за разархивиране.')
            target.parent.mkdir(parents=True,exist_ok=True)
            staging = target.parent/('.iasc-pending-'+uuid.uuid4().hex)
            staging.mkdir()
            manifest = []
            for index,(info,relative_file) in enumerate(members):
                check_cancel(cancel)
                destination = staging.joinpath(*relative_file.parts)
                destination.parent.mkdir(parents=True,exist_ok=True)
                file_hash, written = hashlib.sha256(), 0
                with archive.open(info) as source, destination.open('xb') as output:
                    while chunk := source.read(1024*1024):
                        check_cancel(cancel)
                        written += len(chunk)
                        if written>info.file_size:
                            raise ValueError('Невалиден размер на файл в ZIP.')
                        output.write(chunk)
                        file_hash.update(chunk)
                if written!=info.file_size:
                    raise ValueError('Непълен файл в ZIP.')
                if destination.suffix.lower() in FITS:
                    verify_fits(destination)
                manifest.append(dict(name=relative_file.as_posix(),size=written,sha256=file_hash.hexdigest()))
                progress(index+1,len(members),'Проверка и разархивиране · '+relative_file.name)
        check_cancel(cancel)
        stamp = time.time()
        marker = dict(version=1,id=row['id'],campaign=row['campaign'],team=row['team'],imageSetName=row['name'],
                      zipSha256=digest.hexdigest(),downloaded=stamp,files=manifest)
        with (staging/MARKER).open('x',encoding='utf-8') as stream:
            json.dump(marker,stream,ensure_ascii=False,indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        check_cancel(cancel)
        staging.rename(target)
        staging = None
        return dict(target=str(target),manifest=json.dumps(manifest),digest=digest.hexdigest(),downloaded=stamp)
    except zipfile.BadZipFile:
        raise ValueError('IASC не върна валиден ZIP или файлът е повреден. Нищо не е добавено в архива.') from None
    finally:
        archive_path.unlink(missing_ok=True)
        if (staging is not None and staging.is_dir() and not staging.is_symlink() and not staging.is_junction()
                and staging.name.startswith('.iasc-pending-') and staging.parent==target.parent
                and staging.resolve().is_relative_to(root) and staging.resolve().parent==target.parent.resolve()):
            shutil.rmtree(staging)
