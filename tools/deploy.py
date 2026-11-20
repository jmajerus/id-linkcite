#!/usr/bin/env python3
"""Deploy the current build to the id-linkcite LXC over SSH (key-based, as root).

    python3 scripts/build.py           # first: build/public.nq from the vocabularies' repositories
    python3 tools/deploy.py            # copy the release and run deploy/install.sh there
    python3 tools/deploy.py --inspect  # show the server's services and what it is serving

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

ROOT = Path(__file__).resolve().parent.parent
HOST = os.environ.get('ID_LINKCITE_HOST', 'root@id-linkcite.localdomain')
FILES = ['package.json', 'package-lock.json', 'trifid', 'deploy', 'editorial_service', 'scripts/editorial.py', 'requirements.txt', 'site/assets', 'build/public.nq', 'build/manifest.json', 'build/welcome.hbs', 'build/pages', 'build/editorial-resources.json']
INSPECT = r'''
for unit in id-linkcite id-linkcite-oxigraph oxigraph; do printf '%-22s %s %s\n' "$unit" "$(systemctl is-enabled $unit 2>/dev/null || true)" "$(systemctl is-active $unit 2>/dev/null || true)"; done
echo "release: $(readlink /opt/id-linkcite/current || echo none)"
echo "store:   $(readlink /var/lib/id-linkcite/store || echo none)"
ss -ltnp | awk 'NR == 1 || /:787[89] /'
'''


def ssh(command, data=None):
    return subprocess.run(['ssh', '-o', 'BatchMode=yes', HOST, command], input=data, check=True)


def release_id():
    commit = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = subprocess.run(['git', 'diff', '--quiet', 'HEAD'], cwd=ROOT).returncode != 0
    return time.strftime('%Y%m%d-%H%M%S') + '-' + commit + ('-dirty' if dirty else '')


def archive():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        for name in FILES:
            path = ROOT / name
            if not path.exists():
                sys.exit(f'Missing {name}. Run python3 scripts/build.py first.' if name.startswith('build/') else f'Missing {name}.')
            tar.add(path, arcname=name, filter=lambda info: None if '__pycache__' in info.name else info)
    return buffer.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--inspect', action='store_true', help="show the server's state without changing it")
    args = parser.parse_args()
    if args.inspect:
        ssh(INSPECT)
        return
    manifest = json.loads((ROOT / 'build/manifest.json').read_text())
    summary = ', '.join(f"{v['path']} ({v['resources']})" for v in manifest['vocabularies'])
    print(f"Deploying {summary}{'' if manifest['definitions'] else ', without definitions'} to {HOST}", flush=True)
    rid = release_id()
    release = f'/opt/id-linkcite/releases/{rid}'
    ssh(f'install -d -m 0755 /opt/id-linkcite/releases && mkdir {release} && tar -xzf - -C {release}', data=archive())
    ssh(f'RELEASE_ID={rid} sh {release}/deploy/install.sh')


if __name__ == '__main__':
    main()
