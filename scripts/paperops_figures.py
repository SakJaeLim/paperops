# -*- coding: utf-8 -*-
"""PaperOps figure pipeline.

Generates reproducible, spec-driven figures for the thesis manuscript.

Design principles (mirrors PaperOps governance):
- Figures are generated from explicit specs (config/figures.yaml), never
  imagined content. Only the sources provided in the spec file are rendered;
  this module never fabricates data figures.
- Sources (.dot / .mmd) are always saved so every figure is reproducible.
- Rendering uses Graphviz `dot` for kind=dot and mermaid-cli `mmdc` for
  kind=mermaid. A missing renderer degrades to sources-only with a warning
  (status renderer_missing, exit 0); pass --strict to fail instead. Real
  renderer errors still fail the command.
- Inserting figure references into the manuscript follows the guarded
  preview -> human approval -> apply flow. Target headings are matched as
  exact lines (never substrings), matches inside fenced code blocks are
  ignored, zero matches block with heading_not_found, multiple matches block
  with heading_ambiguous (never guess), and re-applying blocks with
  already_applied instead of duplicating.
- Quantitative/result charts are intentionally NOT generated here unless an
  actual data CSV exists; this module never fabricates results.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

try:  # optional dependency; this module must import without PyYAML
    import yaml
except Exception:  # pragma: no cover - environment dependent
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
FIG_DIR = ROOT / 'reports/figures'
FIG_SRC_DIR = FIG_DIR / 'src'
MANUSCRIPT_FIG_DIR = ROOT / '05_manuscript/figures'
LOG = ROOT / 'logs/ACTIVITY_LOG.md'

# Figure specs live outside the code so authors can edit them without
# touching the pipeline. Resolved against ROOT at call time.
FIGURES_CONFIG_REL = 'config/figures.yaml'

RENDERERS = {'dot': 'dot', 'mermaid': 'mmdc'}
RENDERER_INSTALL_HINTS = {
    'dot': ('install Graphviz so `dot` is on PATH '
            '(https://graphviz.org/download/ ; e.g. `apt install graphviz` '
            'or `winget install Graphviz.Graphviz`)'),
    'mmdc': ('install mermaid-cli so `mmdc` is on PATH '
             '(`npm install -g @mermaid-js/mermaid-cli`)'),
}

REQUIRED_SPEC_KEYS = ('id', 'kind', 'caption', 'target_heading', 'source')
SPEC_KINDS = tuple(RENDERERS)

PREVIEW_FIELDS = ('figure_id', 'target_file', 'target_heading',
                  'target_sha256', 'insert_block', 'status', 'reason')

# Blocked reasons that are benign on re-run (idempotency) and therefore do
# not turn the exit code non-zero.
BENIGN_BLOCK_REASONS = ('already_applied',)


def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def today():
    return datetime.now().strftime('%Y-%m-%d')


def log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if not LOG.exists():
        LOG.write_text('# 논문AGENT 활동 로그\n\n', encoding='utf-8')
    with LOG.open('a', encoding='utf-8') as f:
        f.write(f'- [{now()}] {msg}\n')


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def write_lf(path, text):
    """Write text with LF line endings (Sprint 17 lesson: avoid CRLF drift)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


def read_text_compat(path):
    """Read text as utf-8(-sig) strict, then cp949 strict, then utf-8 replace.

    Keeps Korean-locale files (cp949) readable without ever crashing on a
    stray byte; the lossy last resort warns loudly.
    """
    data = Path(path).read_bytes()
    try:
        return data.decode('utf-8-sig')
    except UnicodeDecodeError:
        pass
    try:
        return data.decode('cp949')
    except UnicodeDecodeError:
        pass
    print(f'WARN: {path}: not valid utf-8/cp949; decoded as utf-8 with '
          'replacement characters')
    return data.decode('utf-8', errors='replace')


def relpath(p):
    p = Path(p)
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


# ---------------------------------------------------------------------------
# Figure spec loading (config/figures.yaml)
# ---------------------------------------------------------------------------
# Schema: figures: [{id, kind: dot|mermaid, caption, target_heading,
# placeholder (optional), source: |...}] plus optional title / target_file /
# mermaid (companion .mmd source for GitHub display when kind is dot).
# No spec may claim performance results or external-system equivalence.

BUILTIN_DEMO_SPECS = {
    'fig_paperops_demo': {
        'kind': 'dot',
        'title': 'PaperOps minimal architecture (built-in demo spec)',
        'caption': ('PaperOps minimal architecture. Built-in demo spec used '
                    'because config/figures.yaml was not loaded.'),
        'target_file': '05_manuscript/chapters/ch3_method.qmd',
        'target_heading': '## PaperOps Architecture',
        'placeholder': None,
        'mermaid': None,
        'source': (
            'digraph fig_paperops_demo {\n'
            '  graph [fontname="Helvetica", fontsize=11, rankdir=TB];\n'
            '  node [fontname="Helvetica", fontsize=11, shape=box, '
            'style="rounded,filled", fillcolor="#F4F4F2"];\n'
            '  cli [label="PaperOps CLI"];\n'
            '  review [label="Human Review & Approval", fillcolor="#FFE9C7"];\n'
            '  manuscript [label="Thesis Manuscript", shape=cylinder, '
            'fillcolor="#EAF4EA"];\n'
            '  cli -> review;\n'
            '  review -> manuscript [label="guarded apply"];\n'
            '}\n'
        ),
    },
}


def _builtin_fallback(reason, quiet):
    if not quiet:
        print(f'WARN: {reason}; falling back to built-in demo spec '
              f'({", ".join(BUILTIN_DEMO_SPECS)})')
    specs = {fid: dict(spec) for fid, spec in BUILTIN_DEMO_SPECS.items()}
    return specs, [], 'builtin'


def validate_figure_entry(entry, index, seen_ids):
    """Validate one yaml figure entry.

    Returns (fig_id, normalized_spec, None) on success or
    (None, None, reason) on failure. Never raises.
    """
    where = f'figures[{index}]'
    if not isinstance(entry, dict):
        return None, None, f'{where}: entry must be a mapping, got {type(entry).__name__}'
    raw_id = entry.get('id')
    if isinstance(raw_id, str) and raw_id.strip():
        where = f'{where} ({raw_id.strip()})'
    missing = [k for k in REQUIRED_SPEC_KEYS
               if not (isinstance(entry.get(k), str) and entry.get(k).strip())]
    if missing:
        return None, None, f'{where}: missing/empty required key(s): {", ".join(missing)}'
    fig_id = raw_id.strip()
    kind = entry['kind'].strip()
    if kind not in SPEC_KINDS:
        return None, None, (f'{where}: kind must be one of '
                            f'{"|".join(SPEC_KINDS)}, got {kind!r}')
    if fig_id in seen_ids:
        return None, None, f'{where}: duplicate figure id'

    def opt_str(key):
        v = entry.get(key)
        return v if isinstance(v, str) and v.strip() else None

    spec = {
        'kind': kind,
        'caption': entry['caption'].strip(),
        'target_heading': entry['target_heading'].strip(),
        'source': entry['source'],
        'title': (opt_str('title') or fig_id).strip(),
        'target_file': opt_str('target_file'),
        'placeholder': opt_str('placeholder'),
        'mermaid': opt_str('mermaid'),
    }
    return fig_id, spec, None


def load_figure_specs(quiet=False):
    """Load figure specs from config/figures.yaml.

    Returns (specs, invalid, origin): specs maps figure_id to a normalized
    spec dict, invalid lists human-readable reasons for rejected entries,
    origin is 'yaml' or 'builtin'. A missing/unreadable config or a missing
    PyYAML falls back to a built-in one-figure demo spec with a warning;
    invalid individual entries are skipped and reported, never crash.
    """
    cfg = ROOT / FIGURES_CONFIG_REL
    if yaml is None:
        return _builtin_fallback(
            f'PyYAML is not installed; cannot read {FIGURES_CONFIG_REL}', quiet)
    if not cfg.exists():
        return _builtin_fallback(f'{FIGURES_CONFIG_REL} is missing', quiet)
    try:
        data = yaml.safe_load(read_text_compat(cfg))
    except Exception as e:
        return _builtin_fallback(
            f'{FIGURES_CONFIG_REL} could not be parsed ({type(e).__name__}: {e})',
            quiet)
    figures = data.get('figures') if isinstance(data, dict) else None
    if not isinstance(figures, list):
        return _builtin_fallback(f'{FIGURES_CONFIG_REL} has no `figures:` list', quiet)
    specs, invalid = {}, []
    for i, entry in enumerate(figures):
        fig_id, spec, err = validate_figure_entry(entry, i, specs)
        if err:
            invalid.append(err)
            continue
        specs[fig_id] = spec
    if invalid and not quiet:
        for reason in invalid:
            print(f'WARN: invalid figure spec skipped: {reason}')
    return specs, invalid, 'yaml'


# ---------------------------------------------------------------------------
# Heading anchoring
# ---------------------------------------------------------------------------

def heading_match_lines(text, heading):
    """Return 0-based indexes of lines that ARE the heading, exactly.

    A line matches only when, stripped, it equals the target heading
    (regex ^\\s*<escaped heading>\\s*$ applied per line), so
    '## PaperOps Architecture' never matches inside
    '### PaperOps Architecture Details'. Lines inside fenced code blocks
    (``` or ~~~, tracked statefully) are skipped.
    """
    pattern = re.compile(r'^\s*' + re.escape(heading.strip()) + r'\s*$')
    matches = []
    fence = None
    for i, line in enumerate(text.split('\n')):
        stripped = line.lstrip()
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            continue
        if stripped.startswith('```') or stripped.startswith('~~~'):
            fence = stripped[:3]
            continue
        if pattern.match(line):
            matches.append(i)
    return matches


def locate_heading(text, heading):
    """Return (line_index, None) for a unique heading line, else
    (None, 'heading_not_found') or (None, 'heading_ambiguous'). Never guesses.
    """
    matches = heading_match_lines(text, heading)
    if not matches:
        return None, 'heading_not_found'
    if len(matches) > 1:
        return None, 'heading_ambiguous'
    return matches[0], None


def figure_block(fig_id, spec):
    placeholder = (spec.get('placeholder') or '').strip()
    if placeholder:
        return placeholder + '\n'
    label = fig_id.replace('_', '-')
    rel = f'figures/{fig_id}.svg'
    return f'![{spec["caption"]}]({rel}){{#{label}}}\n'


def placeholder_present(text, fig_id, insert_block=''):
    """True when the figure's placeholder/image line already exists."""
    anchor = '{#%s}' % fig_id.replace('_', '-')
    if anchor in text:
        return True
    block = (insert_block or '').strip()
    return bool(block) and block in text


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def dot_available():
    return shutil.which('dot') is not None


def renderer_path(kind):
    """Resolve the renderer executable via shutil.which (Windows .cmd-safe)."""
    return shutil.which(RENDERERS[kind])


def render_figure(fig_id, spec, formats=('svg', 'png')):
    """Write sources and render one figure.

    Sources are always written. Result dict carries status:
    'rendered' | 'renderer_missing' (renderer not on PATH; sources only) |
    'error' (renderer present but failed).
    """
    FIG_SRC_DIR.mkdir(parents=True, exist_ok=True)
    kind = spec['kind']
    result = {'figure_id': fig_id, 'kind': kind, 'sources': [],
              'rendered': [], 'errors': [], 'status': 'rendered'}
    if kind == 'dot':
        src_path = FIG_SRC_DIR / f'{fig_id}.dot'
        write_lf(src_path, spec['source'])
        result['sources'].append(str(src_path))
        if spec.get('mermaid'):
            mmd_path = FIG_SRC_DIR / f'{fig_id}.mmd'
            write_lf(mmd_path, spec['mermaid'])
            result['sources'].append(str(mmd_path))
    else:
        src_path = FIG_SRC_DIR / f'{fig_id}.mmd'
        write_lf(src_path, spec['source'])
        result['sources'].append(str(src_path))
    exe = renderer_path(kind)
    if exe is None:
        result['status'] = 'renderer_missing'
        return result
    timeout = 180 if kind == 'mermaid' else 60
    for fmt in formats:
        out = FIG_DIR / f'{fig_id}.{fmt}'
        if kind == 'dot':
            cmd = [exe, f'-T{fmt}', str(src_path), '-o', str(out)]
            if fmt == 'png':
                cmd = [exe, f'-T{fmt}', '-Gdpi=200', str(src_path), '-o', str(out)]
        else:
            cmd = [exe, '-i', str(src_path), '-o', str(out)]
        try:
            proc = subprocess.run(cmd, capture_output=True, encoding='utf-8',
                                  errors='replace', timeout=timeout)
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or '').strip()
                result['errors'].append(f'{fmt}: {err[:300]}')
            elif not out.exists() or out.stat().st_size == 0:
                result['errors'].append(f'{fmt}: output missing or empty')
            else:
                result['rendered'].append(str(out))
        except Exception as e:
            result['errors'].append(f'{fmt}: {type(e).__name__}: {e}')
    if result['errors']:
        result['status'] = 'error'
    return result


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_propose_figures(args):
    specs, invalid, origin = load_figure_specs()
    out = FIG_DIR / f'figure_proposals_{today()}.md'
    lines = [f'# Figure Proposals {today()}\n\n',
             'Spec-driven figure candidates. Sources are always saved for '
             'reproducibility; rendering uses Graphviz (kind=dot) or '
             'mermaid-cli (kind=mermaid).\n\n',
             f'- spec_source: {origin} (`{FIGURES_CONFIG_REL}`)\n\n']
    for fig_id, spec in specs.items():
        target_file = spec.get('target_file')
        target = (ROOT / target_file) if target_file else None
        placed = False
        if target is not None and target.exists():
            placed = placeholder_present(read_text_compat(target), fig_id,
                                         figure_block(fig_id, spec))
        lines.append(f'## {fig_id}\n\n')
        lines.append(f'- title: {spec["title"]}\n')
        lines.append(f'- kind: {spec["kind"]}\n')
        lines.append(f'- target: `{target_file or "(none)"}` / '
                     f'`{spec["target_heading"]}`\n')
        lines.append(f'- already_placed: {str(placed).lower()}\n')
        lines.append(f'- caption: {spec["caption"]}\n\n')
    if invalid:
        lines.append('## Invalid specs (skipped)\n\n')
        lines += [f'- {reason}\n' for reason in invalid]
        lines.append('\n')
    lines.append('## Out of scope by design\n\n')
    lines.append('- Quantitative result charts are not proposed without an actual '
                 'experiment CSV. This module never fabricates results.\n')
    write_lf(out, ''.join(lines))
    print(out)
    print(f'figure_count={len(specs)}')
    log(f'propose-figures 실행: {len(specs)}개 후보 제안 (spec_source={origin})')


def cmd_render_figures(args):
    specs, invalid, origin = load_figure_specs()
    fig_ids = [args.figure_id] if getattr(args, 'figure_id', None) else list(specs)
    unknown = [f for f in fig_ids if f not in specs]
    if unknown:
        raise SystemExit(f'unknown figure id(s): {", ".join(unknown)}; '
                         f'known: {", ".join(specs) or "(none)"}')
    formats = tuple(f.strip() for f in
                    (getattr(args, 'formats', None) or 'svg,png').split(',')
                    if f.strip())
    strict = bool(getattr(args, 'strict', False))
    results = [render_figure(f, specs[f], formats) for f in fig_ids]
    ok = [r for r in results if r['status'] == 'rendered']
    missing = [r for r in results if r['status'] == 'renderer_missing']
    failed = [r for r in results if r['status'] == 'error']
    out = FIG_DIR / f'figure_render_report_{today()}.md'
    lines = [f'# Figure Render Report {today()}\n\n',
             f'- spec_source: {origin}\n\n']
    for r in results:
        lines.append(f'## {r["figure_id"]}\n\n')
        lines.append(f'- kind: {r["kind"]}\n')
        lines.append(f'- status: {r["status"]}\n')
        for p in r['sources']:
            lines.append(f'- source: `{relpath(p)}`\n')
        for p in r['rendered']:
            rp = Path(p)
            lines.append(f'- rendered: `{relpath(p)}` ({rp.stat().st_size} bytes)\n')
        for e in r['errors']:
            lines.append(f'- ERROR: {e}\n')
        if r['status'] == 'renderer_missing':
            name = RENDERERS[r['kind']]
            lines.append(f'- WARN: `{name}` not found in PATH; sources written '
                         f'only. To render: {RENDERER_INSTALL_HINTS[name]}\n')
        lines.append('\n')
    if invalid:
        lines.append('## Invalid specs (skipped)\n\n')
        lines += [f'- {reason}\n' for reason in invalid]
        lines.append('\n')
    lines.append(f'- rendered_ok: {len(ok)}\n'
                 f'- renderer_missing: {len(missing)}\n'
                 f'- failed: {len(failed)}\n'
                 f'- invalid_specs: {len(invalid)}\n')
    write_lf(out, ''.join(lines))
    print(out)
    print(f'rendered_ok={len(ok)}')
    print(f'renderer_missing={len(missing)}')
    print(f'failed={len(failed)}')
    for name in sorted({RENDERERS[r['kind']] for r in missing}):
        print(f'WARN: renderer `{name}` not found in PATH; sources were '
              f'written but figures using it were not rendered. To fix: '
              f'{RENDERER_INSTALL_HINTS[name]}'
              + ('' if strict else ' (use --strict to make this fatal)'))
    log(f'render-figures 실행: ok={len(ok)}, renderer_missing={len(missing)}, '
        f'failed={len(failed)}')
    if failed:
        raise SystemExit(1)
    if missing and strict:
        raise SystemExit(1)


def cmd_figure_placeholder_preview(args):
    specs, invalid, origin = load_figure_specs()
    fig_ids = [args.figure_id] if getattr(args, 'figure_id', None) else list(specs)
    unknown = [f for f in fig_ids if f not in specs]
    if unknown:
        raise SystemExit(f'unknown figure id(s): {", ".join(unknown)}')
    prefix = getattr(args, 'output_prefix', None) or f'figure_placeholder_preview_{today()}'
    rows = []
    md = [f'# Figure Placeholder Preview {today()}\n\n']
    for fig_id in fig_ids:
        spec = specs[fig_id]
        block = figure_block(fig_id, spec)
        target_file = spec.get('target_file') or ''
        target = (ROOT / target_file) if target_file else None
        status, reason = 'ready', ''
        if not target_file:
            status, reason = 'blocked', 'no_target_file'
        elif not target.exists():
            status, reason = 'blocked', 'target_missing'
        else:
            text = read_text_compat(target)
            _, err = locate_heading(text, spec['target_heading'])
            if err:
                status, reason = 'blocked', err
            elif placeholder_present(text, fig_id, block):
                status, reason = 'skipped', 'already_applied'
        rows.append({
            'figure_id': fig_id,
            'target_file': target_file,
            'target_heading': spec['target_heading'],
            'target_sha256': (file_sha256(target)
                              if target is not None and target.exists() else ''),
            'insert_block': block.strip(),
            'status': status,
            'reason': reason,
        })
        md.append(f'## {fig_id}\n\n- target: `{target_file or "(none)"}` / '
                  f'`{spec["target_heading"]}`\n- status: {status}'
                  + (f' ({reason})' if reason else '') + '\n\n```\n'
                  + block + '```\n\n')
    csv_path = ROOT / f'reports/review/{prefix}.csv'
    md_path = ROOT / f'reports/review/{prefix}.md'
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(PREVIEW_FIELDS))
        w.writeheader()
        w.writerows(rows)
    ready = len([r for r in rows if r['status'] == 'ready'])
    blocked = len([r for r in rows if r['status'] == 'blocked'])
    skipped = len([r for r in rows if r['status'] == 'skipped'])
    md.append(f'- ready_rows: {ready}\n- blocked_rows: {blocked}\n- skipped_rows: {skipped}\n')
    write_lf(md_path, ''.join(md))
    print(csv_path)
    print(md_path)
    print(f'ready_rows={ready}')
    print(f'blocked_rows={blocked}')
    print(f'skipped_rows={skipped}')
    log(f'figure-placeholder-preview 실행: ready={ready}, blocked={blocked}, skipped={skipped}')


def plan_figure_apply(rows):
    """Re-check every preview row against the current manuscripts (no writes).

    Returns (results, applied_ids, file_texts, modified_files, svg_copies):
    results is a list of {figure_id, outcome: applied|blocked|skipped,
    reason, detail} in row order; file_texts holds the post-insertion text
    per target file. Heading anchoring is re-verified line-exact at apply
    time so a preview approved by a human can never land in the wrong spot.
    """
    results = []
    applied_ids = []
    file_texts = {}
    file_shas = {}
    modified_files = set()
    svg_copies = []

    def record(fig_id, outcome, reason='', detail=''):
        results.append({'figure_id': fig_id, 'outcome': outcome,
                        'reason': reason, 'detail': detail})

    for r in rows:
        fig_id = (r.get('figure_id') or '').strip() or '(missing id)'
        status = (r.get('status') or '').strip()
        if status != 'ready':
            record(fig_id, 'skipped', 'not_ready',
                   f'preview status={status or "(empty)"}'
                   + (f' reason={r.get("reason")}' if r.get('reason') else ''))
            continue
        target_file = (r.get('target_file') or '').strip()
        target = ROOT / target_file if target_file else None
        if not target_file or not target.exists():
            record(fig_id, 'blocked', 'target_missing', target_file)
            continue
        if target_file not in file_texts:
            file_texts[target_file] = read_text_compat(target)
            file_shas[target_file] = file_sha256(target)
        text = file_texts[target_file]
        insert_block = (r.get('insert_block') or '').strip()
        if placeholder_present(text, fig_id, insert_block):
            record(fig_id, 'blocked', 'already_applied',
                   f'placeholder already present in {target_file}')
            continue
        if r.get('target_sha256') != file_shas[target_file]:
            record(fig_id, 'blocked', 'sha_mismatch',
                   f'{target_file} changed since preview; regenerate preview')
            continue
        heading = (r.get('target_heading') or '').strip()
        line_idx, err = locate_heading(text, heading)
        if err:
            record(fig_id, 'blocked', err, f'heading "{heading}" in {target_file}')
            continue
        if not insert_block:
            record(fig_id, 'blocked', 'empty_insert_block', '')
            continue
        needs_svg = f'{fig_id}.svg' in insert_block
        svg = FIG_DIR / f'{fig_id}.svg'
        if needs_svg and not svg.exists():
            record(fig_id, 'blocked', 'svg_missing',
                   f'run render-figures first ({relpath(svg)})')
            continue
        lines = text.split('\n')
        new_lines = (lines[:line_idx + 1] + [''] + insert_block.split('\n')
                     + lines[line_idx + 1:])
        file_texts[target_file] = '\n'.join(new_lines)
        modified_files.add(target_file)
        if needs_svg:
            svg_copies.append((svg, MANUSCRIPT_FIG_DIR / svg.name))
        applied_ids.append(fig_id)
        record(fig_id, 'applied', '', f'under "{heading}" in {target_file}')
    return results, applied_ids, file_texts, modified_files, svg_copies


def cmd_apply_figure_placeholder(args):
    preview = ROOT / args.from_preview
    if not preview.exists():
        raise SystemExit(f'preview not found: {preview}')
    preview_sha = file_sha256(preview)
    do_apply = bool(getattr(args, 'apply', False))
    dry_run = bool(getattr(args, 'dry_run', False)) or not do_apply
    rows = list(csv.DictReader(io.StringIO(read_text_compat(preview))))
    ready_count = len([r for r in rows if (r.get('status') or '') == 'ready'])
    results, applied_ids, file_texts, modified_files, svg_copies = plan_figure_apply(rows)
    blocked = [x for x in results if x['outcome'] == 'blocked']
    skipped = [x for x in results if x['outcome'] == 'skipped']
    fatal = [x for x in blocked if x['reason'] not in BENIGN_BLOCK_REASONS]

    if dry_run:
        for x in results:
            if x['outcome'] == 'applied':
                print(f'DRY-RUN would insert {x["figure_id"]}: {x["detail"]}')
            elif x['outcome'] == 'blocked':
                print(f'DRY-RUN BLOCKED {x["figure_id"]}: {x["reason"]}'
                      + (f' ({x["detail"]})' if x['detail'] else ''))
            else:
                print(f'DRY-RUN skipped {x["figure_id"]}: {x["detail"]}')
        print(f'ready_rows={ready_count}')
        print(f'would_apply_rows={len(applied_ids)}')
        print(f'blocked_rows={len(blocked)}')
        print(f'skipped_rows={len(skipped)}')
        print(f'preview_sha256={preview_sha}')
        log(f'apply-figure-placeholder dry-run: would_apply={len(applied_ids)}, '
            f'blocked={len(blocked)}, skipped={len(skipped)}')
        if fatal:
            raise SystemExit(1)
        return

    # Commit phase: back up outside the guarded manuscript tree, copy rendered
    # SVGs, then write the planned texts (LF).
    backup_parent = None
    if modified_files:
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_parent = (ROOT / 'backups' / 'manuscript'
                         / f'manuscript_before_figure_apply_{stamp}')
        backup_dir = backup_parent / 'chapters'
        backup_dir.mkdir(parents=True, exist_ok=True)
        for tf in sorted(modified_files):
            shutil.copy2(ROOT / tf, backup_dir / Path(tf).name)
        MANUSCRIPT_FIG_DIR.mkdir(parents=True, exist_ok=True)
        for src, dst in svg_copies:
            shutil.copy2(src, dst)
        for tf in sorted(modified_files):
            write_lf(ROOT / tf, file_texts[tf])

    report = ROOT / f'reports/review/figure_apply_{today()}.md'
    lines = [f'# Figure Apply Report {today()}\n\n## Summary\n',
             '- mode: apply\n',
             f'- applied: {"true" if applied_ids else "false"}\n',
             f'- applied_rows: {len(applied_ids)}\n',
             f'- blocked_rows: {len(blocked)}\n',
             f'- skipped_rows: {len(skipped)}\n',
             f'- preview: `{relpath(preview)}`\n',
             f'- preview_sha256: {preview_sha}\n',
             '- backup: ' + (f'`{relpath(backup_parent)}`' if backup_parent else 'none') + '\n',
             '\n## Row results\n']
    for x in results:
        line = f'- {x["figure_id"]}: {x["outcome"]}'
        if x['reason']:
            line += f' ({x["reason"]})'
        if x['detail']:
            line += f': {x["detail"]}'
        lines.append(line + '\n')
    lines.append('\n## Applied figures\n')
    lines += [f'- {fid}\n' for fid in applied_ids] or ['- none\n']
    write_lf(report, ''.join(lines))
    print(report)
    print(f'applied_rows={len(applied_ids)}')
    print(f'blocked_rows={len(blocked)}')
    print(f'skipped_rows={len(skipped)}')
    print(f'preview_sha256={preview_sha}')
    for x in blocked:
        print(f'BLOCKED {x["figure_id"]}: {x["reason"]}'
              + (f' ({x["detail"]})' if x['detail'] else ''))
    log(f'apply-figure-placeholder apply: applied={len(applied_ids)}, '
        f'blocked={len(blocked)}, skipped={len(skipped)}'
        + (f', backup={backup_parent}' if backup_parent else ''))
    if fatal:
        raise SystemExit(1)


def register_subcommands(sub):
    p = sub.add_parser('propose-figures', help='List spec-driven figure candidates')
    p.set_defaults(func=cmd_propose_figures)
    p = sub.add_parser('render-figures',
                       help='Write .dot/.mmd sources and render via Graphviz/mermaid-cli')
    p.add_argument('--figure-id')
    p.add_argument('--formats', default='svg,png')
    p.add_argument('--strict', action='store_true',
                   help='exit 1 when a renderer (dot/mmdc) is missing instead of '
                        'degrading to sources-only output')
    p.set_defaults(func=cmd_render_figures)
    p = sub.add_parser('figure-placeholder-preview',
                       help='Guarded preview for inserting figure references')
    p.add_argument('--figure-id')
    p.add_argument('--output-prefix')
    p.set_defaults(func=cmd_figure_placeholder_preview)
    p = sub.add_parser('apply-figure-placeholder',
                       help='Apply figure references from an approved preview')
    p.add_argument('--from-preview', required=True)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--apply', action='store_true')
    p.set_defaults(func=cmd_apply_figure_placeholder)
