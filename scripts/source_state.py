"""Identify the id-linkcite source a build came from, so a deploy can refuse a stale build.

The state is the HEAD commit plus a hash of uncommitted changes to tracked files and
of untracked, non-ignored files. Ignored paths (build/, node_modules/, .env) do not count.
"""
import hashlib
import subprocess
from pathlib import Path


def _git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL)


def source_state(root):
    root = Path(root)
    commit = _git(root, 'rev-parse', 'HEAD').decode().strip()
    digest = hashlib.sha256(_git(root, 'diff', 'HEAD', '--binary'))
    untracked = _git(root, 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0')
    for name in sorted(n for n in untracked if n):
        digest.update(b'\0untracked\0' + name + b'\0')
        path = root / name.decode()
        if path.is_file():
            digest.update(path.read_bytes())
    clean = digest.hexdigest() == hashlib.sha256(b'').hexdigest()
    return {'commit': commit, 'changes': None if clean else digest.hexdigest()}


def describe(state):
    if not state:
        return 'unknown source'
    return state['commit'][:7] + (' with uncommitted changes' if state.get('changes') else '')
