# -*- coding: utf-8 -*-
"""Tests for scripts/paperops_figures.py (spec-driven figure pipeline).

All filesystem-writing tests run against tmp_path trees: the module's path
globals (ROOT, FIG_DIR, ...) are monkeypatched per test, so nothing is ever
written into the repository's 05_manuscript/, reports/, or data/ directories.
No real `dot` or `mmdc` binary is required.
"""
from __future__ import annotations
import argparse
import codecs
import csv
import importlib.util
import shutil as real_shutil
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / 'scripts' / 'paperops_figures.py'


def _load_module():
    spec = importlib.util.spec_from_file_location('paperops_figures_under_test',
                                                  MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    """Fresh module instance with every path global redirected to tmp_path."""
    m = _load_module()
    monkeypatch.setattr(m, 'ROOT', tmp_path)
    monkeypatch.setattr(m, 'FIG_DIR', tmp_path / 'reports' / 'figures')
    monkeypatch.setattr(m, 'FIG_SRC_DIR', tmp_path / 'reports' / 'figures' / 'src')
    monkeypatch.setattr(m, 'MANUSCRIPT_FIG_DIR', tmp_path / '05_manuscript' / 'figures')
    monkeypatch.setattr(m, 'LOG', tmp_path / 'logs' / 'ACTIVITY_LOG.md')
    return m


CHAPTER = '05_manuscript/chapters/ch_test.qmd'

CONFIG_ONE = """\
figures:
  - id: fig_alpha
    kind: dot
    caption: Alpha caption.
    target_file: 05_manuscript/chapters/ch_test.qmd
    target_heading: '## PaperOps Architecture'
    source: |
      digraph fig_alpha { a -> b; }
"""

CONFIG_TWO = CONFIG_ONE + """\
  - id: fig_beta
    kind: dot
    caption: Beta caption.
    target_file: 05_manuscript/chapters/ch_test.qmd
    target_heading: '## Metrics'
    source: |
      digraph fig_beta { b -> c; }
"""

# The nasty real-world layout: a longer ### heading that CONTAINS the target
# as a substring, plus the target inside a fenced code block, plus the one
# real target heading.
CHAPTER_TRICKY = """\
---
title: Test chapter
---

### PaperOps Architecture Details

Prose about details.

```text
## PaperOps Architecture
```

## PaperOps Architecture

Intro paragraph.

## Metrics

Numbers prose.
"""

ALPHA_BLOCK = '![Alpha caption.](figures/fig_alpha.svg){#fig-alpha}'
BETA_BLOCK = '![Beta caption.](figures/fig_beta.svg){#fig-beta}'


def setup_env(m, config_text=CONFIG_ONE, chapter_text=CHAPTER_TRICKY):
    (m.ROOT / 'config').mkdir(parents=True, exist_ok=True)
    (m.ROOT / 'config' / 'figures.yaml').write_text(config_text, encoding='utf-8')
    if chapter_text is not None:
        target = m.ROOT / CHAPTER
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(chapter_text, encoding='utf-8', newline='\n')


def fake_svg(m, fig_id):
    m.FIG_DIR.mkdir(parents=True, exist_ok=True)
    (m.FIG_DIR / f'{fig_id}.svg').write_text('<svg></svg>', encoding='utf-8')


def run_preview(m, prefix='pv', figure_id=None):
    args = argparse.Namespace(figure_id=figure_id, output_prefix=prefix)
    m.cmd_figure_placeholder_preview(args)
    return m.ROOT / f'reports/review/{prefix}.csv'


def run_apply(m, preview_rel='reports/review/pv.csv', apply=True, dry_run=False):
    args = argparse.Namespace(from_preview=preview_rel, apply=apply, dry_run=dry_run)
    m.cmd_apply_figure_placeholder(args)


def read_rows(csv_path):
    with open(csv_path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def write_rows(m, csv_path, rows):
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(m.PREVIEW_FIELDS))
        w.writeheader()
        w.writerows(rows)


def chapter_text(m):
    return (m.ROOT / CHAPTER).read_text(encoding='utf-8')


# ---------------------------------------------------------------------------
# Heading anchoring
# ---------------------------------------------------------------------------

def test_heading_match_is_line_anchored_not_substring(mod):
    matches = mod.heading_match_lines(CHAPTER_TRICKY, '## PaperOps Architecture')
    lines = CHAPTER_TRICKY.split('\n')
    assert len(matches) == 1
    assert lines[matches[0]] == '## PaperOps Architecture'
    # The ### heading contains the target as a substring but must not match.
    assert '### PaperOps Architecture Details' not in [lines[i] for i in matches]


def test_heading_only_as_substring_is_not_found(mod):
    text = '### PaperOps Architecture Details\n\nProse.\n'
    idx, err = mod.locate_heading(text, '## PaperOps Architecture')
    assert idx is None and err == 'heading_not_found'


def test_heading_inside_code_fence_is_ignored(mod):
    text = 'Intro\n\n```md\n## PaperOps Architecture\n```\n\nOutro\n'
    idx, err = mod.locate_heading(text, '## PaperOps Architecture')
    assert idx is None and err == 'heading_not_found'


def test_heading_ambiguous(mod):
    text = '## Metrics\n\nA.\n\n## Metrics\n\nB.\n'
    idx, err = mod.locate_heading(text, '## Metrics')
    assert idx is None and err == 'heading_ambiguous'


def test_preview_blocks_not_found_and_ambiguous(mod, capsys):
    setup_env(mod, CONFIG_TWO,
              '### PaperOps Architecture Details\n\n## Metrics\n\nx\n\n## Metrics\n\ny\n')
    csv_path = run_preview(mod)
    rows = {r['figure_id']: r for r in read_rows(csv_path)}
    assert rows['fig_alpha']['status'] == 'blocked'
    assert rows['fig_alpha']['reason'] == 'heading_not_found'
    assert rows['fig_beta']['status'] == 'blocked'
    assert rows['fig_beta']['reason'] == 'heading_ambiguous'
    out = capsys.readouterr().out
    assert 'ready_rows=0' in out
    assert 'blocked_rows=2' in out


def test_apply_inserts_under_exact_heading_only(mod):
    setup_env(mod)
    fake_svg(mod, 'fig_alpha')
    run_preview(mod)
    run_apply(mod)
    text = chapter_text(mod)
    lines = text.split('\n')
    # The fence-aware matcher must still see exactly one real heading line;
    # the insertion goes right under it (heading, blank, block).
    matches = mod.heading_match_lines(text, '## PaperOps Architecture')
    assert len(matches) == 1
    target_idx = matches[0]
    assert lines[target_idx] == '## PaperOps Architecture'
    assert lines[target_idx + 1] == ''
    assert lines[target_idx + 2] == ALPHA_BLOCK
    # Not inserted after the ### heading or inside the fence.
    details_idx = lines.index('### PaperOps Architecture Details')
    assert ALPHA_BLOCK not in lines[details_idx:details_idx + 3]
    assert chapter_text(mod).count(ALPHA_BLOCK) == 1
    fence_start = lines.index('```text')
    assert lines[fence_start + 1] == '## PaperOps Architecture'  # fence untouched
    # Rendered SVG copied next to the manuscript.
    assert (mod.MANUSCRIPT_FIG_DIR / 'fig_alpha.svg').exists()


def test_apply_blocks_ambiguous_heading_at_apply_time(mod, capsys):
    setup_env(mod)
    fake_svg(mod, 'fig_alpha')
    csv_path = run_preview(mod)
    # Target grows a second identical heading after the human approved.
    target = mod.ROOT / CHAPTER
    doctored = chapter_text(mod) + '\n## PaperOps Architecture\n\nDup.\n'
    target.write_text(doctored, encoding='utf-8', newline='\n')
    rows = read_rows(csv_path)
    rows[0]['target_sha256'] = mod.file_sha256(target)  # sha now matches again
    write_rows(mod, csv_path, rows)
    with pytest.raises(SystemExit) as e:
        run_apply(mod)
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert 'heading_ambiguous' in out
    assert ALPHA_BLOCK not in chapter_text(mod)  # never guesses


def test_apply_blocks_on_sha_mismatch_without_touching_file(mod, capsys):
    setup_env(mod)
    fake_svg(mod, 'fig_alpha')
    run_preview(mod)
    target = mod.ROOT / CHAPTER
    edited = chapter_text(mod) + '\nHuman edit after preview.\n'
    target.write_text(edited, encoding='utf-8', newline='\n')
    with pytest.raises(SystemExit) as e:
        run_apply(mod)
    assert e.value.code == 1
    assert 'sha_mismatch' in capsys.readouterr().out
    assert chapter_text(mod) == edited          # no insertion happened
    assert not (mod.ROOT / 'backups').exists()  # no pointless backup


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_reapply_blocks_already_applied_and_never_duplicates(mod, capsys):
    setup_env(mod)
    fake_svg(mod, 'fig_alpha')
    run_preview(mod)
    run_apply(mod)
    after_first = chapter_text(mod)
    capsys.readouterr()
    # Re-running the same approved preview must be a benign no-op (exit 0).
    run_apply(mod)
    out = capsys.readouterr().out
    assert 'applied_rows=0' in out
    assert 'blocked_rows=1' in out
    assert 'already_applied' in out
    assert chapter_text(mod) == after_first
    assert chapter_text(mod).count(ALPHA_BLOCK) == 1
    # A fresh preview also reports it as already applied.
    csv2 = run_preview(mod, prefix='pv2')
    row = read_rows(csv2)[0]
    assert row['status'] == 'skipped'
    assert row['reason'] == 'already_applied'
    # Applying that fresh preview skips the row and stays a no-op.
    capsys.readouterr()
    run_apply(mod, 'reports/review/pv2.csv')
    out = capsys.readouterr().out
    assert 'applied_rows=0' in out
    assert 'skipped_rows=1' in out
    assert chapter_text(mod) == after_first


# ---------------------------------------------------------------------------
# Honest apply report, preview_sha256, backup location
# ---------------------------------------------------------------------------

def test_apply_report_counts_reasons_sha_and_backup_location(mod, capsys):
    setup_env(mod, CONFIG_TWO)
    fake_svg(mod, 'fig_alpha')
    fake_svg(mod, 'fig_beta')
    csv_path = run_preview(mod)
    original = chapter_text(mod)
    # Simulate a stale/hand-edited preview row: beta now targets a heading
    # that does not exist.
    rows = read_rows(csv_path)
    for r in rows:
        if r['figure_id'] == 'fig_beta':
            r['target_heading'] = '## Missing Heading'
    write_rows(mod, csv_path, rows)
    expected_sha = mod.file_sha256(csv_path)
    with pytest.raises(SystemExit) as e:
        run_apply(mod)
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert 'applied_rows=1' in out
    assert 'blocked_rows=1' in out
    assert f'preview_sha256={expected_sha}' in out
    report = (mod.ROOT / f'reports/review/figure_apply_{mod.today()}.md').read_text(encoding='utf-8')
    assert '- applied_rows: 1' in report
    assert '- blocked_rows: 1' in report
    assert '- skipped_rows: 0' in report
    assert f'- preview_sha256: {expected_sha}' in report
    assert '- fig_beta: blocked (heading_not_found)' in report
    assert '- fig_alpha: applied' in report
    # Alpha applied, beta not.
    assert ALPHA_BLOCK in chapter_text(mod)
    assert BETA_BLOCK not in chapter_text(mod)
    # Backup lives OUTSIDE the guarded manuscript tree, with the original text.
    assert not (mod.ROOT / '05_manuscript' / 'backups').exists()
    backup_root = mod.ROOT / 'backups' / 'manuscript'
    backups = sorted(backup_root.glob('manuscript_before_figure_apply_*/chapters/ch_test.qmd'))
    assert len(backups) == 1
    assert backups[0].read_text(encoding='utf-8') == original
    assert 'backups/manuscript/manuscript_before_figure_apply_' in report


def test_dry_run_is_default_and_writes_nothing(mod, capsys):
    setup_env(mod)
    fake_svg(mod, 'fig_alpha')
    run_preview(mod)
    before = chapter_text(mod)
    run_apply(mod, apply=False)  # no --apply => dry-run
    out = capsys.readouterr().out
    assert 'DRY-RUN' in out
    assert 'would_apply_rows=1' in out
    assert 'preview_sha256=' in out
    assert chapter_text(mod) == before
    assert not (mod.ROOT / 'backups').exists()


# ---------------------------------------------------------------------------
# YAML spec loading and validation
# ---------------------------------------------------------------------------

def test_repo_yaml_ships_the_five_original_specs(mod):
    repo_cfg = REPO / 'config' / 'figures.yaml'
    (mod.ROOT / 'config').mkdir(parents=True, exist_ok=True)
    real_shutil.copyfile(repo_cfg, mod.ROOT / 'config' / 'figures.yaml')
    specs, invalid, origin = mod.load_figure_specs(quiet=True)
    assert origin == 'yaml'
    assert invalid == []
    assert list(specs) == ['fig_pipeline', 'fig_evidence_flow', 'fig_guarded_apply',
                           'fig_architecture', 'fig_verification_states']
    for fig_id, spec in specs.items():
        assert spec['kind'] == 'dot'
        assert spec['source'].startswith(f'digraph {fig_id} {{')
        assert spec['mermaid']  # companion .mmd source preserved
        assert spec['target_file'].startswith('05_manuscript/chapters/')
        assert spec['target_heading'].startswith('## ')
        assert spec['caption']


def test_yaml_validation_reports_invalid_entries_without_crashing(mod, capsys):
    bad = """\
figures:
  - id: fig_ok
    kind: dot
    caption: OK.
    target_heading: '## X'
    source: |
      digraph g { a; }
  - id: fig_nocaption
    kind: dot
    target_heading: '## X'
    source: |
      digraph g { a; }
  - id: fig_badkind
    kind: png
    caption: Bad kind.
    target_heading: '## X'
    source: |
      whatever
  - id: fig_ok
    kind: mermaid
    caption: Duplicate id.
    target_heading: '## X'
    source: |
      flowchart LR
  - 42
"""
    setup_env(mod, bad, chapter_text=None)
    specs, invalid, origin = mod.load_figure_specs()
    assert origin == 'yaml'
    assert list(specs) == ['fig_ok']
    assert len(invalid) == 4
    joined = '\n'.join(invalid)
    assert 'caption' in joined
    assert 'kind must be one of' in joined
    assert 'duplicate figure id' in joined
    assert 'mapping' in joined
    out = capsys.readouterr().out
    assert 'WARN: invalid figure spec skipped' in out


def test_missing_yaml_falls_back_to_builtin_demo_with_warn(mod, capsys):
    specs, invalid, origin = mod.load_figure_specs()
    assert origin == 'builtin'
    assert list(specs) == ['fig_paperops_demo']
    assert specs['fig_paperops_demo']['kind'] == 'dot'
    out = capsys.readouterr().out
    assert 'WARN' in out
    assert 'config/figures.yaml' in out


def test_pyyaml_absent_falls_back_to_builtin_demo(mod, monkeypatch, capsys):
    setup_env(mod, CONFIG_ONE, chapter_text=None)
    monkeypatch.setattr(mod, 'yaml', None)
    specs, invalid, origin = mod.load_figure_specs()
    assert origin == 'builtin'
    assert list(specs) == ['fig_paperops_demo']
    out = capsys.readouterr().out
    assert 'WARN' in out
    assert 'PyYAML' in out


def test_unparseable_yaml_falls_back_with_warn(mod, capsys):
    setup_env(mod, 'figures: [unclosed\n  - ][', chapter_text=None)
    specs, invalid, origin = mod.load_figure_specs()
    assert origin == 'builtin'
    assert 'WARN' in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Renderer degradation
# ---------------------------------------------------------------------------

CONFIG_DOT_AND_MERMAID = CONFIG_ONE + """\
  - id: fig_flow
    kind: mermaid
    caption: Flow caption.
    target_file: 05_manuscript/chapters/ch_test.qmd
    target_heading: '## Metrics'
    source: |
      flowchart LR
        a --> b
"""


def test_renderer_missing_degrades_to_sources_with_exit_0(mod, monkeypatch, capsys):
    setup_env(mod, CONFIG_DOT_AND_MERMAID, chapter_text=None)
    monkeypatch.setattr(mod.shutil, 'which', lambda *a, **k: None)
    args = argparse.Namespace(figure_id=None, formats='svg,png', strict=False)
    mod.cmd_render_figures(args)  # must NOT raise
    out = capsys.readouterr().out
    assert 'renderer_missing=2' in out
    assert 'rendered_ok=0' in out
    assert 'failed=0' in out
    assert 'WARN' in out
    assert 'graphviz' in out.lower()
    assert 'mermaid-cli' in out
    # Sources are still written even without renderers.
    assert (mod.FIG_SRC_DIR / 'fig_alpha.dot').exists()
    assert (mod.FIG_SRC_DIR / 'fig_flow.mmd').exists()
    report = (mod.FIG_DIR / f'figure_render_report_{mod.today()}.md').read_text(encoding='utf-8')
    assert report.count('- status: renderer_missing') == 2


def test_renderer_missing_with_strict_exits_1(mod, monkeypatch):
    setup_env(mod, CONFIG_ONE, chapter_text=None)
    monkeypatch.setattr(mod.shutil, 'which', lambda *a, **k: None)
    args = argparse.Namespace(figure_id=None, formats='svg', strict=True)
    with pytest.raises(SystemExit) as e:
        mod.cmd_render_figures(args)
    assert e.value.code == 1


def test_real_render_error_still_exits_1(mod, monkeypatch, capsys):
    setup_env(mod, CONFIG_ONE, chapter_text=None)
    monkeypatch.setattr(mod.shutil, 'which', lambda *a, **k: '/usr/bin/dot')

    def fake_run(cmd, **kw):
        assert kw.get('encoding') == 'utf-8'
        assert kw.get('errors') == 'replace'
        assert 'text' not in kw
        return types.SimpleNamespace(returncode=1, stderr='boom: syntax error', stdout='')

    monkeypatch.setattr(mod.subprocess, 'run', fake_run)
    args = argparse.Namespace(figure_id=None, formats='svg', strict=False)
    with pytest.raises(SystemExit) as e:
        mod.cmd_render_figures(args)
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert 'failed=1' in out


# ---------------------------------------------------------------------------
# Encodings
# ---------------------------------------------------------------------------

def test_read_text_compat_utf8_sig_and_cp949(mod, tmp_path):
    p1 = tmp_path / 'bom.txt'
    p1.write_bytes(codecs.BOM_UTF8 + '## 제목\n'.encode('utf-8'))
    assert mod.read_text_compat(p1) == '## 제목\n'
    p2 = tmp_path / 'k.txt'
    p2.write_bytes('한글 근거 문장\n'.encode('cp949'))
    assert mod.read_text_compat(p2) == '한글 근거 문장\n'


def test_read_text_compat_lossy_fallback_warns(mod, tmp_path, capsys):
    p = tmp_path / 'junk.bin'
    p.write_bytes(b'ok \xff\xfe\xff broken')
    text = mod.read_text_compat(p)
    assert 'ok' in text
    assert 'WARN' in capsys.readouterr().out


def test_preview_csv_written_utf8_sig(mod):
    setup_env(mod)
    csv_path = run_preview(mod)
    assert csv_path.read_bytes().startswith(b'\xef\xbb\xbf')
    rows = read_rows(csv_path)
    assert rows[0]['figure_id'] == 'fig_alpha'


# ---------------------------------------------------------------------------
# CLI contract
# ---------------------------------------------------------------------------

def test_register_subcommands_keeps_existing_names_and_flags(mod):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command')
    mod.register_subcommands(sub)
    a = parser.parse_args(['propose-figures'])
    assert a.func is mod.cmd_propose_figures
    a = parser.parse_args(['render-figures', '--figure-id', 'fig_x',
                           '--formats', 'svg', '--strict'])
    assert a.func is mod.cmd_render_figures and a.strict is True
    a = parser.parse_args(['figure-placeholder-preview', '--figure-id', 'fig_x',
                           '--output-prefix', 'p'])
    assert a.func is mod.cmd_figure_placeholder_preview
    a = parser.parse_args(['apply-figure-placeholder', '--from-preview', 'x.csv',
                           '--dry-run', '--apply'])
    assert a.func is mod.cmd_apply_figure_placeholder
