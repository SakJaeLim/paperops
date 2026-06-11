# -*- coding: utf-8 -*-
"""Build a clean public release of PaperOps into dist/public_release/.

Whitelist-based export: only explicitly listed paths are copied, so
copyrighted paper PDFs, parsed full texts, evidence quotes, personal
manuscript chapters, and private logs can never leak into the public repo.

Usage:
    python scripts/build_public_release.py            # build
    python scripts/build_public_release.py --check    # sanitize-scan only
"""
from __future__ import annotations
import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist/public_release'

# (source, destination-inside-release)
WHITELIST_FILES = [
    ('scripts/paperops.py', 'scripts/paperops.py'),
    ('scripts/paperops_extra.py', 'scripts/paperops_extra.py'),
    ('scripts/paperops_figures.py', 'scripts/paperops_figures.py'),
    ('scripts/paperops_draft_audit.py', 'scripts/paperops_draft_audit.py'),
    ('scripts/build_public_release.py', 'scripts/build_public_release.py'),
    ('requirements.txt', 'requirements.txt'),
    ('pyproject.toml', 'pyproject.toml'),
    ('run_paperops.bat', 'run_paperops.bat'),
    ('README_PUBLIC.md', 'README.md'),
    ('README_PUBLIC.ko.md', 'README.ko.md'),
    ('README_PUBLIC.zh.md', 'README.zh.md'),
    ('README_PUBLIC.ja.md', 'README.ja.md'),
    ('README_PUBLIC.fr.md', 'README.fr.md'),
    ('README_PUBLIC.ar.md', 'README.ar.md'),
    ('LICENSE', 'LICENSE'),
    ('docs/00_MASTER_DESIGN.md', 'docs/00_MASTER_DESIGN.md'),
    ('docs/01_MVP_ROADMAP.md', 'docs/01_MVP_ROADMAP.md'),
    ('docs/03_TOOL_SYNTHESIS.md', 'docs/03_TOOL_SYNTHESIS.md'),
]

WHITELIST_DIRS = [
    ('config', 'config'),
    ('reports/figures/src', 'assets/figures/src'),
]

WHITELIST_GLOBS = [
    ('reports/figures', '*.svg', 'assets/figures'),
]

# Patterns that must never appear in exported text files.
FORBIDDEN_PATTERNS = [
    (re.compile(r'[A-Za-z0-9._%+-]+@(?:gmail|naver|daum|kakao)\.[A-Za-z]{2,}'),
     'personal email'),
    (re.compile(r'sk-[A-Za-z0-9]{20,}'), 'API key-like token'),
    (re.compile(r'ghp_[A-Za-z0-9]{20,}'), 'GitHub token'),
    (re.compile(r'AKIA[0-9A-Z]{16}'), 'AWS key'),
]

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
    return [p for p in base.rglob('*')
            if p.is_file() and '.git' not in p.parts]


def sanitize_scan(base):
    issues = []
    for path in release_files(base):
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        for pat, label in FORBIDDEN_PATTERNS:
            for m in pat.finditer(text):
                issues.append(f'{path.relative_to(base)}: {label}: {m.group(0)[:40]}')
    return issues


def build():
    if DIST.exists():
        # Preserve .git so the release repo keeps its remote/history.
        for child in DIST.iterdir():
            if child.name == '.git':
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    DIST.mkdir(parents=True, exist_ok=True)
    missing = []
    for src, dst in WHITELIST_FILES:
        s = ROOT / src
        if not s.exists():
            missing.append(src)
            continue
        d = DIST / dst
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
    for src, dst in WHITELIST_DIRS:
        s = ROOT / src
        if not s.exists():
            missing.append(src)
            continue
        shutil.copytree(s, DIST / dst, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for src, pattern, dst in WHITELIST_GLOBS:
        s = ROOT / src
        if not s.exists():
            missing.append(src)
            continue
        d = DIST / dst
        d.mkdir(parents=True, exist_ok=True)
        for f in s.glob(pattern):
            shutil.copy2(f, d / f.name)
    (DIST / '.gitignore').write_text(PUBLIC_GITIGNORE, encoding='utf-8')
    return missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true',
                    help='scan existing dist/public_release only')
    args = ap.parse_args()
    if not args.check:
        missing = build()
        for m in missing:
            print(f'WARN missing source: {m}')
    if not DIST.exists():
        print('ERROR: dist/public_release does not exist; run without --check first')
        raise SystemExit(1)
    issues = sanitize_scan(DIST)
    n_files = len(release_files(DIST))
    print(f'release_dir={DIST}')
    print(f'file_count={n_files}')
    print(f'sanitize_issues={len(issues)}')
    for i in issues:
        print(f'ISSUE: {i}')
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    (DIST / 'RELEASE_INFO.txt').write_text(
        f'PaperOps public release built {stamp}\n'
        f'files={n_files}\nsanitize_issues={len(issues)}\n', encoding='utf-8')
    if issues:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
