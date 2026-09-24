"""Offline SQLite/evidence backup with a portable SHA-256 manifest.

Stop API and runner before capture or restore. Never overwrites a destination.
Restore produces a verified staging copy, not a running deployment. Evidence
paths in the ledger must be recovered at their original locations; rewriting
those paths would invalidate existing hashes. The manifest detects accidental
corruption, not malicious manifest replacement.
"""
import argparse
import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def safe_files(root):
    for path in root.rglob('*'):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Symlinks and files outside the backup root are forbidden')
        if path.is_file():
            yield path


def capture(database, evidence, destination):
    database, evidence, destination = map(lambda p: Path(p).resolve(), (database, evidence, destination))
    if not database.is_file() or not evidence.is_dir():
        raise ValueError('Existing database and evidence directory required')
    if destination.is_relative_to(evidence):
        raise ValueError('Backup destination must be outside the evidence directory')
    destination.mkdir(parents=True, exist_ok=False)
    source_files = list(safe_files(evidence))
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(destination / 'database.db')) as target:
        source.backup(target)
        if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Database integrity check failed')
    for source in source_files:
        target = destination / 'evidence' / source.relative_to(evidence)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    (destination / 'evidence').mkdir(exist_ok=True)
    manifest = {'version': 1, 'database_origin': str(database), 'evidence_origin': str(evidence),
        'consistency': 'Requires stopped writers', 'files': {
        str(p.relative_to(destination).as_posix()): digest(p) for p in safe_files(destination)}}
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    verify(destination)
    return manifest


def verify(root):
    root = Path(root).resolve()
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('version') != 1 or not isinstance(manifest.get('files'), dict):
        raise ValueError('Unsupported manifest')
    actual = {p.relative_to(root).as_posix() for p in safe_files(root)} - {'manifest.json'}
    if actual != set(manifest['files']) or 'database.db' not in actual:
        raise ValueError('Backup has missing or unexpected files')
    for name, expected in manifest['files'].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or digest(path) != expected:
            raise ValueError('Backup digest mismatch')
    with closing(sqlite3.connect((root / 'database.db').as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Database integrity check failed')
    return True


def restore(root, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    verify(root)
    if destination.exists():
        raise ValueError('Restore destination must not exist')
    if destination.is_relative_to(root):
        raise ValueError('Restore destination must be outside the backup')
    shutil.copytree(root, destination)
    verify(destination)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['capture','verify','restore'])
    parser.add_argument('--backup', required=True)
    parser.add_argument('--database')
    parser.add_argument('--evidence')
    parser.add_argument('--destination')
    parser.add_argument('--writers-stopped', action='store_true')
    args = parser.parse_args()
    if args.action != 'verify' and not args.writers_stopped:
        parser.error('Stop API/runner first, then explicitly pass --writers-stopped')
    if args.action == 'capture':
        if not args.database or not args.evidence:
            parser.error('capture requires --database and --evidence')
        capture(args.database, args.evidence, args.backup)
    elif args.action == 'restore':
        if not args.destination:
            parser.error('restore requires --destination')
        restore(args.backup, args.destination)
    else:
        verify(args.backup)
    print('Completed. Offline backup checks passed; no production recovery guarantee is implied.')
