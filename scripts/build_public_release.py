# -*- coding: utf-8 -*-
"""Build a clean public release of PaperOps into dist/public_release/.

Whitelist-based export: only explicitly listed paths are copied, so
copyrighted paper PDFs, parsed full texts, evidence quotes, personal
manuscript chapters, and private logs can never leak into the public repo.

Safety model, in order:
  1. copy whitelisted paths into dist/public_release/
  2. sanitize the exported config YAML copies: values of secret-bearing
     keys are blanked in the copy (the working tree is never modified)
  3. scan every exported text file against FORBIDDEN_PATTERNS
  4. if the scan finds anything, the release directory is renamed to
     dist/public_release_QUARANTINE_<timestamp> so nothing publishable
     remains at the expected path, and the exit status is 1

Usage:
    python scripts/build_public_release.py            # build + sanitize + scan
    python scripts/build_public_release.py --check    # scan an existing
        # dist/public_release only: no copy, no sanitize, and
        # RELEASE_INFO.txt is not rewritten -- but a failing scan still
        # quarantines the directory.
"""
from __future__ import annotations
import argparse
import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist/public_release'

# (source, destination-inside-release)
WHITELIST_FILES = [
    ('scripts/demo_public.py', 'scripts/demo_public.py'),
    ('docs/PUBLIC_DEMO.md', 'docs/PUBLIC_DEMO.md'),
    ('scripts/paperops.py', 'scripts/paperops.py'),
    ('scripts/paperops_figures.py', 'scripts/paperops_figures.py'),
    ('scripts/paperops_draft_audit.py', 'scripts/paperops_draft_audit.py'),
    ('scripts/build_public_release.py', 'scripts/build_public_release.py'),
    ('scripts/run_pipeline.py', 'scripts/run_pipeline.py'),
    ('requirements.txt', 'requirements.txt'),
    ('pyproject.toml', 'pyproject.toml'),
    ('run_paperops.bat', 'run_paperops.bat'),
    ('run_pipeline.bat', 'run_pipeline.bat'),
    ('README.md', 'README.md'),
    ('README.ko.md', 'README.ko.md'),
    ('README.de.md', 'README.de.md'),
    ('README.es.md', 'README.es.md'),
    ('README.zh.md', 'README.zh.md'),
    ('README.ja.md', 'README.ja.md'),
    ('README.fr.md', 'README.fr.md'),
    ('README.ar.md', 'README.ar.md'),
    ('LICENSE', 'LICENSE'),
    ('CHANGELOG.md', 'CHANGELOG.md'),
    ('docs/00_MASTER_DESIGN.md', 'docs/00_MASTER_DESIGN.md'),
    ('docs/01_MVP_ROADMAP.md', 'docs/01_MVP_ROADMAP.md'),
    ('docs/03_TOOL_SYNTHESIS.md', 'docs/03_TOOL_SYNTHESIS.md'),
    ('docs/RUN_AX_ONTOLOGY_GOVERNANCE.md', 'docs/RUN_AX_ONTOLOGY_GOVERNANCE.md'),
]

WHITELIST_DIRS = [
    ('config', 'config'),
    ('tests', 'tests'),
    ('.github', '.github'),
    ('reports/figures/src', 'assets/figures/src'),
]

WHITELIST_GLOBS = [
    ('reports/figures', '*.svg', 'assets/figures'),
]

EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')

# Email addresses allowed to appear in the export: documentation
# placeholders and well-known no-reply addresses.
ALLOWED_EMAILS = [
    re.compile(r'^[A-Za-z0-9._%+-]+@example\.(?:com|org)$'),
    re.compile(r'^noreply@anthropic\.com$'),
    re.compile(r'^[A-Za-z0-9._%+-]+@users\.noreply\.github\.com$'),
]

# (rule name, compiled regex) -- patterns that must never appear in
# exported text files.  The scan report names the rule that fired.
# Note the config-assignment rule uses [ \t] rather than \s around the
# separator on purpose: \s would let the value run onto the next line, so
# an empty key followed by a long identifier on the following line would
# false-positive.
FORBIDDEN_PATTERNS = [
    ('email-address', EMAIL_RE),
    ('github-fine-grained-pat', re.compile(r'github_pat_[A-Za-z0-9_]{20,}')),
    ('github-token', re.compile(r'gh[pousr]_[A-Za-z0-9]{20,}')),
    ('private-key-block', re.compile(
        r'-----BEGIN(?:\x20RSA|\x20EC|\x20OPENSSH|\x20DSA|\x20PGP)?'
        r'\x20PRIVATE\x20KEY-----')),
    ('aws-access-key', re.compile(r'AKIA[0-9A-Z]{16}')),
    ('sk-secret-key', re.compile(r'sk-(?:ant-)?[A-Za-z0-9_-]{20,}')),
    ('slack-token', re.compile(r'xox[baprs]-[A-Za-z0-9-]{10,}')),
    ('google-api-key', re.compile(r'AIza[0-9A-Za-z_-]{35}')),
    ('jwt', re.compile(r'eyJ[A-Za-z0-9_-]{20,}\.eyJ')),
    ('config-assignment-secret', re.compile(
        r"(?i)(?:api[_-]?key|secret|token|password)"
        r"[ \t]*[:=][ \t]*['\"]?[A-Za-z0-9_\-]{16,}")),
]

# Config keys whose exported values are always blanked.
SENSITIVE_KEY_RE = re.compile(r'(?i)api[_-]?key|mailto|token|secret')
# user_agent is blanked only when its value embeds an email address.
USER_AGENT_KEY_RE = re.compile(r'(?i)^user[_-]?agent$')
# A simple `key: value` YAML line (top-level or indented mapping entry).
CONFIG_LINE_RE = re.compile(r'^(\s*)([A-Za-z0-9_.\-]+)\s*:\s?(.*)$')
# Values that are already blank: empty, empty-quoted, or comment-only.
BLANK_VALUE_RE = re.compile(r'^(?:""|\'\')?\s*(?:#.*)?$')

TEXT_SUFFIXES = {'.py', '.md', '.txt', '.yaml', '.yml', '.toml', '.bat',
                 '.csv', '.mmd', '.dot', '.svg', '.json'}

PUBLIC_GITIGNORE = """\
.venv/
__pycache__/
*.pyc
.env
data/
logs/
matrices/
notes/
reports/
05_manuscript/
manuscript/
dist/
*.sqlite*
*.pdf
*.zip
*.tmp
*.bak
"""


def release_files(base):
    return [p for p in Path(base).rglob('*')
            if p.is_file() and '.git' not in p.parts]


def release_file_count(base):
    """Number of files in the release, excluding RELEASE_INFO.txt itself.

    Documented choice: RELEASE_INFO.txt describes the release, so it is
    not counted as release content.  Because it is the only file written
    after the count is taken, and it is excluded from the count, the
    number is identical whether computed in build mode (just before
    RELEASE_INFO.txt is written) or in --check mode (where a previous
    RELEASE_INFO.txt already exists on disk).
    """
    base = Path(base)
    return len([p for p in release_files(base)
                if not (p.parent == base and p.name == 'RELEASE_INFO.txt')])


def _email_allowed(text):
    return any(p.match(text) for p in ALLOWED_EMAILS)


def sanitize_scan(base):
    base = Path(base)
    issues = []
    for path in release_files(base):
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        for name, pat in FORBIDDEN_PATTERNS:
            for m in pat.finditer(text):
                if name == 'email-address' and _email_allowed(m.group(0)):
                    continue
                issues.append(
                    f'{path.relative_to(base)}: {name}: {m.group(0)[:40]}')
    return issues


def sanitize_configs(dist=None):
    """Blank secret-bearing values in the exported config YAML copies.

    Line-level rewrite (no YAML library needed): any `key: value` line
    whose key name contains api_key / mailto / token / secret, or whose
    key is user_agent with an email address in the value, is rewritten as
    `key: ""` with indentation preserved.  Values that are already blank
    are left untouched and not reported.  Only files under <dist>/config
    are rewritten; the working tree copy is never modified.

    Returns a list of (relative-path, key) redaction records.  The
    records never include the removed values.
    """
    dist = Path(dist) if dist is not None else DIST
    redactions = []
    cfg_dir = dist / 'config'
    if not cfg_dir.exists():
        return redactions
    for path in sorted(cfg_dir.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in ('.yaml', '.yml'):
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        out = []
        changed = False
        for line in text.splitlines():
            m = CONFIG_LINE_RE.match(line)
            if m:
                indent, key, value = m.group(1), m.group(2), m.group(3)
                sensitive = bool(SENSITIVE_KEY_RE.search(key)) or bool(
                    USER_AGENT_KEY_RE.match(key) and EMAIL_RE.search(value))
                if sensitive and not BLANK_VALUE_RE.match(value.strip()):
                    out.append(f'{indent}{key}: ""')
                    redactions.append((path.relative_to(dist).as_posix(), key))
                    changed = True
                    continue
            out.append(line)
        if changed:
            path.write_text('\n'.join(out) + '\n', encoding='utf-8')
    return redactions


def quarantine(dist=None, stamp=None):
    """Move a failed release out of the publishable path.

    Renames <dist> to <dist>_QUARANTINE_<timestamp>, replacing any prior
    quarantine of the same name.  Returns the quarantine path.
    """
    dist = Path(dist) if dist is not None else DIST
    stamp = stamp or datetime.now().strftime('%Y%m%d_%H%M%S')
    target = dist.with_name(f'{dist.name}_QUARANTINE_{stamp}')
    if target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    dist.rename(target)
    return target


def build(root=None, dist=None):
    root = Path(root) if root is not None else ROOT
    dist = Path(dist) if dist is not None else DIST
    if dist.exists():
        # Preserve .git so the release repo keeps its remote/history.
        for child in dist.iterdir():
            if child.name == '.git':
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    dist.mkdir(parents=True, exist_ok=True)
    missing = []
    for src, dst in WHITELIST_FILES:
        s = root / src
        if not s.exists():
            missing.append(src)
            continue
        d = dist / dst
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
    for src, dst in WHITELIST_DIRS:
        s = root / src
        if not s.exists():
            missing.append(src)
            continue
        shutil.copytree(s, dist / dst, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(
                            '__pycache__', '*.pyc', '.pytest_cache'))
    for src, pattern, dst in WHITELIST_GLOBS:
        s = root / src
        if not s.exists():
            missing.append(src)
            continue
        d = dist / dst
        d.mkdir(parents=True, exist_ok=True)
        for f in s.glob(pattern):
            shutil.copy2(f, d / f.name)
    (dist / '.gitignore').write_text(PUBLIC_GITIGNORE, encoding='utf-8')
    return missing


def write_release_info(dist, n_files, n_missing, redactions, n_issues,
                       stamp=None):
    stamp = stamp or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    lines = [
        f'PaperOps public release built {stamp}',
        f'files={n_files} (count excludes RELEASE_INFO.txt itself)',
        f'missing_whitelist_warnings={n_missing}',
        f'config_redactions={len(redactions)}',
    ]
    for rel, key in redactions:
        lines.append(f'redacted: {rel}: {key}')
    lines.append(f'sanitize_issues={n_issues}')
    (Path(dist) / 'RELEASE_INFO.txt').write_text(
        '\n'.join(lines) + '\n', encoding='utf-8')


def run(check=False, root=None, dist=None):
    """Build (unless check), sanitize, scan, report.  Returns exit status."""
    root = Path(root) if root is not None else ROOT
    dist = Path(dist) if dist is not None else DIST
    missing = []
    redactions = []
    if not check:
        missing = build(root, dist)
        for m in missing:
            print(f'WARN missing source: {m}')
        redactions = sanitize_configs(dist)
        for rel, key in redactions:
            print(f'REDACTED {rel}: {key}')
    if not dist.exists():
        print(f'ERROR: {dist} does not exist; run without --check first')
        return 1
    issues = sanitize_scan(dist)
    n_files = release_file_count(dist)
    print(f'release_dir={dist}')
    print(f'file_count={n_files}')
    print(f'missing_whitelist_warnings={len(missing)}')
    print(f'config_redactions={len(redactions)}')
    print(f'sanitize_issues={len(issues)}')
    for i in issues:
        print(f'ISSUE: {i}')
    if not check:
        write_release_info(dist, n_files, len(missing), redactions,
                           len(issues))
    if issues:
        qdir = quarantine(dist)
        print(f'QUARANTINED: sanitize scan failed; release moved to {qdir}')
        print(f'Nothing publishable remains at {dist}.')
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true',
                    help='scan existing dist/public_release only '
                         '(no build/sanitize; failures still quarantine)')
    args = ap.parse_args()
    status = run(check=args.check)
    if status:
        raise SystemExit(status)


if __name__ == '__main__':
    main()
