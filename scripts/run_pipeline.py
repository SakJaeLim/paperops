# -*- coding: utf-8 -*-
"""One-click PaperOps automation: collect -> score -> screen -> gap -> download -> cards -> figures.

Runs ONLY the automatic stages. Human gates (review-evidence-candidates, promote-evidence,
apply-manuscript-patch, verify-evidence) are never invoked from here, by design.

Output is plain ASCII on purpose: emoji output crashed with UnicodeEncodeError whenever
stdout was redirected on cp949 Windows (run_pipeline.bat > log.txt, Task Scheduler, CI).
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_command(args, check=True):
    cmd = [sys.executable, str(ROOT / 'scripts' / 'paperops.py')] + args
    print('[RUN] paperops ' + ' '.join(args), flush=True)
    env = dict(os.environ)
    env.setdefault('PYTHONIOENCODING', 'utf-8')
    result = subprocess.run(cmd, cwd=str(ROOT), env=env)
    if result.returncode != 0:
        if check:
            print('[FAIL] pipeline stopped at: ' + ' '.join(args), flush=True)
            sys.exit(1)
        print('[WARN] non-fatal step failed: ' + ' '.join(args), flush=True)
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description='PaperOps one-click automatic pipeline')
    parser.add_argument('--limit', type=int, default=20, help='per-source collection limit')
    parser.add_argument('--skip-downloads', action='store_true', help='skip PDF downloads')
    parser.add_argument('--skip-figures', action='store_true', help='skip figure rendering')
    args = parser.parse_args()

    print('[START] PaperOps automatic pipeline (human gates are NOT run here)', flush=True)
    run_command(['collect', '--limit', str(args.limit)])
    run_command(['score'])
    run_command(['screen', '--limit', str(args.limit * 2)])
    run_command(['gap'])
    if not args.skip_downloads:
        run_command(['download-pdfs', '--limit', str(args.limit)])
    run_command(['cards'])
    if not args.skip_figures:
        # Figure rendering degrades gracefully without Graphviz (sources still written),
        # and a real renderer failure should not erase the collection work above.
        run_command(['render-figures'], check=False)
    print('[OK] automatic pipeline finished', flush=True)
    print('[NEXT] human gates: review-evidence-candidates -> promote-evidence -> verify-evidence', flush=True)


if __name__ == '__main__':
    main()
