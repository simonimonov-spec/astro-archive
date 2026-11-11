"""Create reviewable, allowlisted source trees; never publish or use credentials."""
from __future__ import annotations

import csv
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).with_name('publication-files.json')
REPOSITORY = re.compile(r'^[a-z][a-z0-9-]+$')
TEXT = {'.py', '.pyw', '.js', '.css', '.html', '.svg', '.md', '.txt', '.json', '.cmd', '.ps1'}
FORBIDDEN_SUFFIXES = {'.fit', '.fts', '.fits', '.fz', '.gz', '.sqlite3', '.sqlite', '.db',
                      '.pem', '.key', '.pfx', '.p12', '.pt', '.pth', '.npz', '.npy',
                      '.log', '.tmp', '.pyc', '.zip', '.onnx', '.safetensors',
                      '.pkl', '.pickle', '.joblib', '.csv', '.ecsv', '.jsonl'}
SECRET_PATTERNS = {
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----'),
    'GitHub token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b'),
    'API token': re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{30,}\b'),
    'AWS access key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'credential URL': re.compile(r'https?://[^\s/:]+:[^\s/@]+@'),
    'personal Windows path': re.compile(r'[A-Za-z]:[\\/]+Users[\\/]+(?!Public\b|Default\b)[^\\/\s]+', re.I),
}


def safe_relative(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise ValueError('Use a nonempty relative POSIX path')
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(p in ('', '.', '..') for p in value.split('/')):
        raise ValueError('Path traversal or noncanonical path')
    return relative


def source_file(root, value):
    relative = safe_relative(value)
    path = Path(root).joinpath(*relative.parts)
    for part in (path, *path.parents):
        if part == Path(root):
            break
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise ValueError(f'Linked path is not publishable: {value}')
    if not path.resolve().is_relative_to(Path(root).resolve()) or not path.is_file():
        raise ValueError(f'Missing or external source: {value}')
    forbidden = {'.git', '.venv', '.release-tests', 'cache', '__pycache__',
                 'publication-review', 'node_modules'}
    if any(p.lower() in forbidden for p in relative.parts) or path.suffix.lower() in FORBIDDEN_SUFFIXES:
        raise ValueError(f'Runtime/data file is not publishable: {value}')
    if (path.name.startswith('.env') or path.name in {'.netrc', 'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519'}
            or re.search(r'(?:credentials|token|secret|password|cookies).*\.json', path.name, re.I)):
        raise ValueError(f'Credential file is not publishable: {value}')
    return path


def inspect_text(path):
    if path.suffix.lower() not in TEXT:
        return []
    content = path.read_text(encoding='utf-8-sig')
    return [dict(kind=kind, line=content.count('\n', 0, match.start()) + 1)
            for kind, pattern in SECRET_PATTERNS.items() for match in pattern.finditer(content)]


def plan(root=ROOT, manifest=MANIFEST, core_only=False):
    document = json.loads(Path(manifest).read_text(encoding='utf-8-sig'))
    if document.get('format') != 1 or not isinstance(document.get('repositories'), dict):
        raise ValueError('Unsupported publication manifest')
    result = {}
    for name, entries in document['repositories'].items():
        if core_only and name != 'astro-archive':
            continue
        if not REPOSITORY.fullmatch(name) or not isinstance(entries, list) or not entries:
            raise ValueError('Invalid repository specification')
        files = []
        destinations = set()
        for entry in entries:
            source = source_file(root, entry['source'])
            destination = str(safe_relative(entry['destination']))
            if destination.casefold() in destinations:
                raise ValueError('Duplicate destination')
            destinations.add(destination.casefold())
            findings = inspect_text(source)
            if findings:
                summary = ', '.join(f"{f['kind']} at line {f['line']}" for f in findings)
                raise ValueError(f"Review required: {entry['source']}: {summary}")
            files.append(dict(source=entry['source'], destination=destination,
                              bytes=source.stat().st_size,
                              sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
        if name == 'astro-archive' and any(
                f['destination'].startswith('Plugins/') and
                not f['destination'].startswith('Plugins/.examples/') and
                len(PurePosixPath(f['destination']).parts) > 2 for f in files):
            raise ValueError('Installed plugin code must not be included in core')
        result[name] = sorted(files, key=lambda f: f['destination'])
    return result


def excluded_files(root, included):
    """List original workspace paths; generated candidates and Git are grouped."""
    grouped = {'.git', 'publication-review', '.release-tests'}
    for parent, directories, names in os.walk(root, followlinks=False):
        relative_parent = Path(parent).relative_to(root)
        for directory in sorted(directories):
            candidate = Path(parent) / directory
            relative = (relative_parent / directory).as_posix()
            if directory in grouped or candidate.is_symlink() or getattr(candidate, 'is_junction', lambda: False)():
                yield relative + '/**', 'Git metadata / generated review or test files / linked directory'
        directories[:] = sorted(d for d in directories if d not in grouped
                                 and not (Path(parent) / d).is_symlink()
                                 and not getattr(Path(parent) / d, 'is_junction', lambda: False)())
        for name in sorted(names):
            relative = (relative_parent / name).as_posix()
            if relative in included:
                continue
            if relative.startswith('Backend/Cache/'):
                reason = 'Private runtime data, observations, settings, sessions or credentials'
            elif relative.startswith('.venv/') or '/__pycache__/' in relative:
                reason = 'Local Python environment or generated bytecode'
            else:
                reason = 'Not in the explicit publication allowlist'
            yield relative, reason


def build(root=ROOT, manifest=MANIFEST, core_only=False):
    root = Path(root).resolve()
    repositories = plan(root, manifest, core_only)  # validate everything before copying
    output = root / 'publication-review' / ('candidate-' + uuid.uuid4().hex[:12])
    if output.parent.exists() and (output.parent.is_symlink() or getattr(output.parent, 'is_junction', lambda: False)()):
        raise ValueError('Review directory must not be a link')
    output.mkdir(parents=True, exist_ok=False)
    for name, files in repositories.items():
        for file in files:
            source = source_file(root, file['source'])
            payload = source.read_bytes()
            if hashlib.sha256(payload).hexdigest() != file['sha256']:
                raise ValueError('Source changed during export; create a fresh candidate')
            target = output / name / file['destination']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        with zipfile.ZipFile(output / (name + '.zip'), 'x', zipfile.ZIP_DEFLATED) as archive:
            for file in files:
                archive.write(output / name / file['destination'], name + '/' + file['destination'])
    included = {file['source'] for files in repositories.values() for file in files}
    excluded = list(excluded_files(root, included))
    with (output / 'excluded-files.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['path', 'reason'])
        writer.writerows(excluded)
    (output / 'publication-manifest.json').write_text(
        json.dumps(dict(repositories=repositories), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    lines = ['# Предложени файлове за GitHub — изисква се потвърждение', '',
             'Нищо не е качено публично. Директориите и ZIP файловете са локални кандидати.', '',
             'Прегледайте LICENSE-STATUS.md за статуса на лицензирането и предстоящите решения.', '',
             'Автоматична проверка: няма съвпадения с разпознаваните шаблони за тайни',
             'и лични Windows пътища в предложените текстови файлове.',
             'Това не замества ръчния преглед, включително на иконите.', '',
             'SHA-256 и точните изходни пътища са в `publication-manifest.json`.', '',
             '## Файлове за публикуване', '']
    for name, files in repositories.items():
        lines += [f'### {name} — {len(files)} файла', '', '```text']
        lines += [f['destination'] for f in files]
        lines += ['```', '']
    lines += ['## Изключени файлове', '',
              f'Пълният списък ({len(excluded)} реда) е в `excluded-files.csv`.', '',
              '- Цялата `Backend/Cache/`: каталози, профил, FITS, сесии, токени, учебни набори, модели и отчети.',
              '- `.venv/`, генериран bytecode, `.git/`, локални тестови среди и кандидати.',
              '- Старият локален `Прочети ме.txt` и всички файлове извън изричния списък.',
              '- Плъгините са изключени от основното хранилище и присъстват само в съответното отделно хранилище.', '',
              'Локалните данни не са променяни или изтривани. Не публикувай CSV/отчета за преглед.', '']
    (output / 'REVIEW.md').write_text('\n'.join(lines), encoding='utf-8')
    return output, repositories, len(excluded)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core-only', action='store_true', help='Export core without needing optional plugin sources')
    arguments = parser.parse_args()
    output, repositories, excluded = build(core_only=arguments.core_only)
    print(output)
    print(json.dumps({name: len(files) for name, files in repositories.items()}))
    print(f'Excluded entries: {excluded}. Nothing has been published.')
