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


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stale_reasons(root, manifest):
    """Why build output in this manifest no longer matches its inputs; empty if it does."""
    root = Path(root)
    reasons = []
    built, current = manifest.get('site'), source_state(root)
    if built != current:
        reasons.append(f'build/ was made from {describe(built)}, but the checkout is {describe(current)}')
    for vocab in manifest.get('vocabularies', []):
        for source in vocab.get('sources', []):
            path = (root / source['file']).resolve()
            recorded = source.get('sha256')
            actual = file_digest(path) if path.is_file() else None
            if recorded is None or recorded != actual:
                state = ('was not fingerprinted by the build' if recorded is None else
                         'is missing' if actual is None else 'has changed since the build')
                reasons.append(f"{source['file']} {state}")
    return reasons


def describe(state):
    if not state:
        return 'unknown source'
    return state['commit'][:7] + (' with uncommitted changes' if state.get('changes') else '')
