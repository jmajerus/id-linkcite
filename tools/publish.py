#!/usr/bin/env python3
"""Rebuild the site and deploy it if anything changed since the live release.

    npm run publish                 # check, build, compare with the live release, deploy if changed
    npm run publish -- --dry-run    # check, build and compare, but do not deploy
    npm run publish -- --force      # deploy even if nothing changed

Meant to run after a published text (vocabularies.json "texts", such as an annex PDF) is rebuilt in
its own repository; the manuscript's "Build Annex N + publish" VS Code tasks call it. It refuses to
publish from anything but a clean checkout of main, checks that each text is a readable PDF, and
prints what differs from the live release before deploying: the whole site is deployed, so changed
vocabulary sources go live too.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
from deploy import HOST  # noqa: E402

PYTHON = str(ROOT / '.venv/bin/python')
LIVE_MANIFEST = '/opt/id-linkcite/current/build/manifest.json'
# Words a licence notice in the first pages of each text should contain. Missing, it is a warning.
LICENSE_WORDS = ('CC BY-NC', 'Creative Commons')


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def check_checkout():
    branch = git('rev-parse', '--abbrev-ref', 'HEAD')
    if branch != 'main':
        sys.exit(f'Not published: id-linkcite is on {branch}, not main.')
    if git('status', '--porcelain'):
        sys.exit('Not published: id-linkcite has uncommitted changes (git status).')


def check_texts(config):
    """Each text must be a PDF with pages; a missing licence notice is only reported."""
    for work in config.get('works', []):
        for text in work.get('texts', []):
            source = (ROOT / text['source']).resolve()
            if not source.is_file():
                sys.exit(f'Not published: {source} is missing.')
            info = subprocess.run(['pdfinfo', str(source)], capture_output=True, text=True)
            pages = next((int(line.split()[1]) for line in info.stdout.splitlines() if line.startswith('Pages:')), 0)
            if info.returncode != 0 or pages == 0:
                sys.exit(f'Not published: {source} is not a readable PDF.\n{info.stderr.strip()}')
            front = subprocess.run(['pdftotext', '-l', '4', str(source), '-'], capture_output=True, text=True).stdout
            note = '' if any(w in front for w in LICENSE_WORDS) else ' (warning: no licence notice in the first pages)'
            print(f"{work['path']}/text/{text['file']}: {pages} pages{note}")


def live_manifest():
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', HOST, f'cat {LIVE_MANIFEST}'],
                            capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f'Not published: could not read the live release from {HOST}.\n{result.stderr.strip()}')
    return json.loads(result.stdout)


def differences(live, new):
    """What the new build changes, in words; empty if it matches the live release."""
    changes = []
    if live.get('site', {}).get('commit') != new['site']['commit']:
        changes.append(f"site code: {str(live.get('site', {}).get('commit'))[:7]} -> {new['site']['commit'][:7]}")
    old_texts = {t['href']: t for t in live.get('texts', [])}
    new_texts = {t['href']: t for t in new.get('texts', [])}
    for href in sorted(old_texts.keys() | new_texts.keys()):
        old, cur = old_texts.get(href), new_texts.get(href)
        if not old:
            changes.append(f'{href}: added')
        elif not cur:
            changes.append(f'{href}: removed')
        elif old['sha256'] != cur['sha256']:
            changes.append(f"{href}: changed ({old['built']} -> {cur['built']}, {old['bytes']:,} -> {cur['bytes']:,} bytes)")
    old_vocabs = {v['path']: v for v in live.get('vocabularies', [])}
    for v in new['vocabularies']:
        old = old_vocabs.pop(v['path'], None)
        if not old:
            changes.append(f"{v['path']}: added ({v['resources']} resources)")
        elif [s['sha256'] for s in old['sources']] != [s['sha256'] for s in v['sources']]:
            changes.append(f"{v['path']}: sources changed ({old['resources']} -> {v['resources']} resources)")
    changes += [f'{path}: removed' for path in old_vocabs]
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--dry-run', action='store_true', help='build and compare, but do not deploy')
    parser.add_argument('--force', action='store_true', help='deploy even if nothing changed')
    args = parser.parse_args()
    sys.stdout.reconfigure(line_buffering=True)  # keep this output in order with the build steps

    check_checkout()
    check_texts(json.loads((ROOT / 'vocabularies.json').read_text()))
    live = live_manifest()
    # The same steps as npm run build.
    subprocess.run([PYTHON, str(ROOT.parent / 'dehumanization-patterns/scripts/build_terminology_scheme.py')],
                   cwd=ROOT, check=True)
    subprocess.run([PYTHON, 'scripts/build.py'], cwd=ROOT, check=True)
    new = json.loads((ROOT / 'build/manifest.json').read_text())

    changes = differences(live, new)
    print('\nChanges from the live release:' if changes else '\nThe build matches the live release.')
    for change in changes:
        print(f'  {change}')
    if args.dry_run:
        print('Dry run: not deployed.')
        return
    if not changes and not args.force:
        print('Nothing to deploy.')
        return
    subprocess.run([sys.executable, 'tools/deploy.py'], cwd=ROOT, check=True)


if __name__ == '__main__':
    main()
