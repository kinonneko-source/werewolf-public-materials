"""Local format sanity checks only, NOT the platform's authoritative dry-run."""
import gzip
import hashlib
import io
import json
import re
import tarfile
from pathlib import Path


def build(destination):
    repo = Path(__file__).resolve().parents[1]
    source = repo / 'community' / 'werewolf-notes'
    manifest = json.loads((source / 'plugin.json').read_text())
    name = manifest['name']
    assert source.name == name and re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name)
    assert manifest['schema'] == 'krow-plugin/1'
    assert re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', manifest['version'])
    assert manifest['components'] == {'skills': ['werewolf-notes'], 'tools': None, 'mcp_servers': []}
    assert {p.name for p in (source / 'skills').iterdir()} == {'werewolf-notes'}
    files = sorted(p for p in source.rglob('*') if p.is_file())
    assert all(not p.is_symlink() for p in source.rglob('*'))
    assert not (source / 'tools').exists() and not (source / 'mcp.json').exists()
    assert not any(p.name == 'scripts' for p in source.rglob('*'))
    assert len(files) < 2000 and sum(p.stat().st_size for p in files) < 64 * 1024 * 1024
    assert all(p.stat().st_size < 8 * 1024 * 1024 for p in files)
    # Explicit file allowlist: no runtime, user records, secrets, or executable extras.
    assert {str(p.relative_to(source)) for p in files} == {'README.md', 'plugin.json', 'skills/werewolf-notes/SKILL.md'}
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f'{name}-{manifest["version"]}.tar.gz'
    with archive.open('wb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as compressed, tarfile.open(fileobj=compressed, mode='w') as tar:
        for path in files + [repo / 'LICENSE']:
            relative = path.relative_to(source) if path != repo / 'LICENSE' else Path('LICENSE')
            info = tarfile.TarInfo(f'{name}/{relative.as_posix()}')
            body = path.read_bytes()
            info.size, info.mode, info.mtime = len(body), 0o644, 0
            tar.addfile(info, io.BytesIO(body))
    assert archive.stat().st_size <= 8 * 1024 * 1024
    with tarfile.open(archive) as tar:
        names = [m.name for m in tar.getmembers()]
        assert all(m.isfile() and not m.issym() and not m.islnk() for m in tar.getmembers())
        assert all(n.startswith(name + '/') and '..' not in Path(n).parts for n in names)
        assert all(not n.endswith('.py') for n in names)
    report = {'local_sanity_passed': True, 'platform_dry_run': 'NOT_RUN: no authorized endpoint',
              'community_equivalent_host': False, 'archive': archive.name,
              'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'bytes': archive.stat().st_size, 'files': names,
              'checks_scope': 'allowlisted pure-text package, manifest, paths, limits, no executables; not platform ownership/version/secret-scan approval'}
    (destination / 'community-package-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('destination')
    build(parser.parse_args().destination)
