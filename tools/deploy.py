#!/usr/bin/env python3
"""Deploy the current build to the id-linkcite LXC over SSH (key-based, as root).

    python3 scripts/build.py           # first: build/public.nq from the vocabularies' repositories
    python3 tools/deploy.py            # copy the release and run deploy/install.sh there
    python3 tools/deploy.py --inspect  # show the server's services and what it is serving

A deploy refuses a build made from a different commit or different uncommitted changes
than the current checkout; rebuild, or pass --allow-stale to deploy it anyway.

The host is root@id-linkcite.localdomain unless ID_LINKCITE_HOST says otherwise.
"""
import argparse
import io
import json
import os
import subprocess
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from source_state import source_state, stale_reasons  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HOST = os.environ.get('ID_LINKCITE_HOST', 'root@id-linkcite.localdomain')
FILES = ['package.json', 'package-lock.json', 'trifid', 'deploy', 'editorial_service', 'scripts/editorial.py', 'requirements.txt', 'site/assets', 'build/public.nq', 'build/manifest.json', 'build/welcome.hbs', 'build/pages', 'build/editorial-resources.json', 'build/editorial-canonical.json']
INSPECT = r'''
for unit in id-linkcite id-linkcite-oxigraph id-linkcite-editorial-public id-linkcite-editorial-admin oxigraph; do printf '%-22s %s %s\n' "$unit" "$(systemctl is-enabled $unit 2>/dev/null || true)" "$(systemctl is-active $unit 2>/dev/null || true)"; done
echo "release: $(readlink /opt/id-linkcite/current || echo none)"
echo "store:   $(readlink /var/lib/id-linkcite/store || echo none)"
ss -ltnp | awk 'NR == 1 || /:(787[89]|7880|8000) /'
'''


def ssh(command, data=None):
    return subprocess.run(['ssh', '-o', 'BatchMode=yes', HOST, command], input=data, check=True)


def release_id():
    commit = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = subprocess.run(['git', 'diff', '--quiet', 'HEAD'], cwd=ROOT).returncode != 0
    return time.strftime('%Y%m%d-%H%M%S') + '-' + commit + ('-dirty' if dirty else '')


def deployed_record():
    """What /health reports as deployed: the branch, commit, time and whether it was clean."""
    state = source_state(ROOT)
    branch = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=ROOT, text=True).strip()
    return {'branch': branch, 'commit': state['commit'],
            'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'dirty': state['changes'] is not None}


def archive():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        record = json.dumps(deployed_record(), indent=2).encode()
        info = tarfile.TarInfo('deployed.json')
        info.size, info.mtime, info.mode = len(record), int(time.time()), 0o644
        tar.addfile(info, io.BytesIO(record))
        for name in FILES:
            path = ROOT / name
            if not path.exists():
                sys.exit(f'Missing {name}. Run python3 scripts/build.py first.' if name.startswith('build/') else f'Missing {name}.')
            tar.add(path, arcname=name, filter=lambda info: None if '__pycache__' in info.name else info)
    return buffer.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--inspect', action='store_true', help="show the server's state without changing it")
    parser.add_argument('--allow-stale', action='store_true',
                        help='deploy even if build/ was made from different source than the checkout')
    args = parser.parse_args()
    if args.inspect:
        ssh(INSPECT)
        return
    manifest_path = ROOT / 'build/manifest.json'
    if not manifest_path.exists():
        sys.exit('No complete build: build/manifest.json is missing. Run python3 scripts/build.py first.')
    manifest = json.loads(manifest_path.read_text())
    reasons = stale_reasons(ROOT, manifest)
    if reasons:
        message = '; '.join(reasons) + '. Run python3 scripts/build.py first'
        if not args.allow_stale:
            sys.exit(message + ', or pass --allow-stale.')
        print(f'Warning: {message}; deploying anyway (--allow-stale).', file=sys.stderr)
    summary = ', '.join(f"{v['path']} ({v['resources']})" for v in manifest['vocabularies'])
    print(f"Deploying {summary}{'' if manifest['definitions'] else ', without definitions'} to {HOST}", flush=True)
    rid = release_id()
    release = f'/opt/id-linkcite/releases/{rid}'
    data = archive()
    # Check again after packing, so an edit made while archiving cannot slip in unchecked.
    if not args.allow_stale and stale_reasons(ROOT, manifest) != reasons:
        sys.exit('The source changed while the release was being packed; nothing was uploaded. Deploy again.')
    ssh(f'install -d -m 0755 /opt/id-linkcite/releases && mkdir {release} && tar -xzf - -C {release}', data=data)
    ssh(f'RELEASE_ID={rid} sh {release}/deploy/install.sh')


if __name__ == '__main__':
    main()
