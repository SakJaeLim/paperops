# -*- coding: utf-8 -*-
"""PaperOps figure pipeline.

Generates reproducible, spec-driven figures for the thesis manuscript.

Design principles (mirrors PaperOps governance):
- Figures are generated from explicit specs, never imagined content.
- Sources (.dot / .mmd) are always saved so every figure is reproducible.
- Rendering uses Graphviz `dot` (primary). Mermaid sources are emitted for
  GitHub-native display; rendering them is optional (npx mermaid-cli).
- Inserting figure references into the manuscript follows the guarded
  preview -> human approval -> apply flow. No silent manuscript edits.
- Quantitative/result charts are intentionally NOT generated here unless an
  actual data CSV exists; this module never fabricates results.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIG_DIR = ROOT / 'reports/figures'
FIG_SRC_DIR = FIG_DIR / 'src'
MANUSCRIPT_FIG_DIR = ROOT / '05_manuscript/figures'
LOG = ROOT / 'logs/ACTIVITY_LOG.md'


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


# ---------------------------------------------------------------------------
# Figure spec registry
# ---------------------------------------------------------------------------
# Each spec is grounded in the actual PaperOps architecture and CLI commands.
# No spec may claim performance results or external-system equivalence.

GV_STYLE = (
    '  graph [fontname="Helvetica", fontsize=11, rankdir=%s, splines=ortho, '
    'nodesep=0.45, ranksep=0.55, pad=0.2];\n'
    '  node [fontname="Helvetica", fontsize=11, shape=box, style="rounded,filled", '
    'fillcolor="#F4F4F2", color="#555555", margin="0.18,0.10"];\n'
    '  edge [fontname="Helvetica", fontsize=9, color="#555555", arrowsize=0.7];\n'
)

HUMAN_NODE = 'fillcolor="#FFE9C7"'
GUARD_NODE = 'fillcolor="#DCE9F7"'
DATA_NODE = 'shape=cylinder, fillcolor="#EAF4EA"'

FIGURE_SPECS = {
    'fig_pipeline': {
        'title': 'PaperOps end-to-end pipeline',
        'caption': (
            'PaperOps end-to-end pipeline. Literature collection, parsing, and '
            'evidence extraction are automated, while review, verification, and '
            'manuscript changes pass through explicit human approval gates.'
        ),
        'target_file': '05_manuscript/chapters/ch3_method.qmd',
        'target_heading': '## PaperOps Architecture',
        'dot': (
            'digraph fig_pipeline {\n' + GV_STYLE % 'TB' +
            '  collect [label="Collect\\n(arXiv / S2 / OpenAlex)"];\n'
            '  screen [label="Score & Screen"];\n'
            '  pdf [label="PDF Download"];\n'
            '  grobid [label="GROBID Parse"];\n'
            '  extract [label="Evidence Candidate\\nExtraction"];\n'
            '  review [label="Human Review\\n(review queue)", ' + HUMAN_NODE + '];\n'
            '  matrix [label="Evidence Matrix\\n(promoted rows)", ' + DATA_NODE + '];\n'
            '  preview [label="Manuscript Patch\\nPreview + Diff"];\n'
            '  approve [label="Human Approval", ' + HUMAN_NODE + '];\n'
            '  apply [label="Guarded Apply\\n(backup + LF write)"];\n'
            '  guard [label="Guards\\n(no-auto-verified, smoke-test)", ' + GUARD_NODE + '];\n'
            '  thesis [label="Thesis Manuscript\\n(Quarto)", ' + DATA_NODE + '];\n'
            '  subgraph cluster_auto {\n'
            '    label="Automated collection & extraction"; style=dashed; '
            'color="#999999";\n'
            '    collect -> screen -> pdf -> grobid -> extract;\n'
            '  }\n'
            '  subgraph cluster_gov {\n'
            '    label="Governed review & writing"; style=dashed; color="#999999";\n'
            '    review -> matrix [label="promote"];\n'
            '    matrix -> preview -> approve -> apply -> thesis;\n'
            '  }\n'
            '  extract -> review;\n'
            '  apply -> guard [style=dashed, label="post-check"];\n'
            '  guard -> matrix [style=dashed, label="audit", constraint=false];\n'
            '}\n'
        ),
        'mermaid': (
            'flowchart LR\n'
            '  A[Collect<br/>arXiv / Semantic Scholar / OpenAlex] --> B[Score & Screen]\n'
            '  B --> C[PDF Download]\n'
            '  C --> D[GROBID Parse]\n'
            '  D --> E[Evidence Candidate Extraction]\n'
            '  E --> F{{Human Review}}\n'
            '  F -->|promote| G[(Evidence Matrix)]\n'
            '  G --> H[Manuscript Patch Preview + Diff]\n'
            '  H --> I{{Human Approval}}\n'
            '  I --> J[Guarded Apply<br/>backup + LF write]\n'
            '  J --> K[(Thesis Manuscript)]\n'
            '  J -.post-check.-> L[Guards<br/>no-auto-verified, smoke-test]\n'
            '  L -.audit.-> G\n'
        ),
    },
    'fig_evidence_flow': {
        'title': 'Evidence governance flow',
        'caption': (
            'Evidence governance flow. Quote matching and page location are '
            'treated as source-alignment checks; the verified state is reachable '
            'only through an explicit human verification gate.'
        ),
        'target_file': '05_manuscript/chapters/ch3_method.qmd',
        'target_heading': '## Evidence-first Workflow',
        'dot': (
            'digraph fig_evidence_flow {\n' + GV_STYLE % 'TB' +
            '  cand [label="Evidence Candidate\\n(claim + quote + location)"];\n'
            '  valid [label="Structural Validation\\n(schema, citekey)"];\n'
            '  align [label="Source Alignment\\n(quote match, page locate)"];\n'
            '  queue [label="Review Queue", ' + DATA_NODE + '];\n'
            '  human [label="Human Decision\\n(keep / revise / reject)", ' + HUMAN_NODE + '];\n'
            '  promoted [label="Promoted Row\\nverified=false", ' + DATA_NODE + '];\n'
            '  pdfcheck [label="PDF Page Check\\n(required for high-risk)"];\n'
            '  verify [label="Human Verification\\nGate", ' + HUMAN_NODE + '];\n'
            '  verified [label="verified=true\\n(manual only)", ' + GUARD_NODE + '];\n'
            '  align_note [label="alignment != truth validation", shape=note, '
            'fillcolor="#FFF7D6"];\n'
            '  { rank=same; cand; valid; align; queue; human; }\n'
            '  { rank=same; align_note; verified; verify; pdfcheck; promoted; }\n'
            '  cand -> valid -> align -> queue -> human [constraint=false];\n'
            '  // invisible vertical pins keep row 2 folded under row 1\n'
            '  cand -> align_note [style=invis];\n'
            '  valid -> verified [style=invis];\n'
            '  align -> verify [style=invis];\n'
            '  queue -> pdfcheck [style=invis];\n'
            '  human -> promoted [label="promote"];\n'
            '  promoted -> pdfcheck [constraint=false];\n'
            '  pdfcheck -> verify [constraint=false];\n'
            '  verify -> verified [constraint=false];\n'
            '}\n'
        ),
        'mermaid': (
            'flowchart LR\n'
            '  A[Evidence Candidate<br/>claim + quote + location] --> B[Structural Validation]\n'
            '  B --> C[Source Alignment<br/>quote match, page locate]\n'
            '  C --> D[(Review Queue)]\n'
            '  D --> E{{Human Decision}}\n'
            '  E --> F[(Promoted Row<br/>verified=false)]\n'
            '  F --> G[PDF Page Check]\n'
            '  G --> H{{Human Verification Gate}}\n'
            '  H --> I[verified=true<br/>manual only]\n'
        ),
    },
    'fig_guarded_apply': {
        'title': 'Guarded manuscript apply workflow',
        'caption': (
            'Guarded manuscript apply workflow. Every manuscript change is '
            'previewed as a diff, requires human approval, is applied against a '
            'backup, and is followed by automated guard and smoke-test checks; '
            'failures roll back from the backup.'
        ),
        'target_file': '05_manuscript/chapters/ch3_method.qmd',
        'target_heading': '## Human Verification Policy',
        'dot': (
            'digraph fig_guarded_apply {\n' + GV_STYLE % 'TB' +
            '  preview [label="Patch Preview\\n(CSV + MD + diff)"];\n'
            '  review [label="Human Diff Review", ' + HUMAN_NODE + '];\n'
            '  backup [label="Backup Chapters"];\n'
            '  apply [label="Apply\\n(SHA-checked, LF write)"];\n'
            '  postguard [label="guard-no-auto-verified\\n+ smoke-test", ' + GUARD_NODE + '];\n'
            '  report [label="Apply Report", ' + DATA_NODE + '];\n'
            '  rollback [label="Rollback from Backup", fillcolor="#F7DCDC"];\n'
            '  preview -> review;\n'
            '  review -> backup [label="approved"];\n'
            '  review -> preview [label="rejected / revise", style=dashed];\n'
            '  backup -> apply -> postguard;\n'
            '  postguard -> report [label="pass"];\n'
            '  postguard -> rollback [label="fail", style=dashed];\n'
            '  rollback -> preview [style=dashed];\n'
            '}\n'
        ),
        'mermaid': (
            'flowchart TB\n'
            '  A[Patch Preview<br/>CSV + MD + diff] --> B{{Human Diff Review}}\n'
            '  B -->|approved| C[Backup Chapters]\n'
            '  B -.rejected / revise.-> A\n'
            '  C --> D[Apply<br/>SHA-checked, LF write]\n'
            '  D --> E[guard-no-auto-verified<br/>+ smoke-test]\n'
            '  E -->|pass| F[(Apply Report)]\n'
            '  E -.fail.-> G[Rollback from Backup]\n'
            '  G -.-> A\n'
        ),
    },
    'fig_architecture': {
        'title': 'PaperOps system architecture',
        'caption': (
            'PaperOps system architecture. A single CLI orchestrates external '
            'services (GROBID, Zotero/Better BibTeX) and local stores (paper DB, '
            'matrices, manuscript), with audit reports produced at each guarded step.'
        ),
        'target_file': '05_manuscript/chapters/ch4_system.qmd',
        'target_heading': '## Data Model',
        'dot': (
            'digraph fig_architecture {\n' + GV_STYLE % 'TB' +
            '  cli [label="PaperOps CLI\\n(scripts/paperops.py)"];\n'
            '  subgraph cluster_ext {\n'
            '    label="External services"; style=dashed; color="#999999";\n'
            '    grobid [label="GROBID\\n(Docker)"];\n'
            '    zotero [label="Zotero +\\nBetter BibTeX"];\n'
            '    apis [label="Paper APIs\\n(arXiv, S2, OpenAlex)"];\n'
            '  }\n'
            '  subgraph cluster_store {\n'
            '    label="Local stores"; style=dashed; color="#999999";\n'
            '    db [label="papers.sqlite", ' + DATA_NODE + '];\n'
            '    matrices [label="matrices/\\n(evidence, screening, gap)", ' + DATA_NODE + '];\n'
            '    manuscript [label="05_manuscript/\\n(Quarto)", ' + DATA_NODE + '];\n'
            '    reports [label="reports/\\n(audit, review, figures)", ' + DATA_NODE + '];\n'
            '  }\n'
            '  config [label="config/\\n(sources, scoring, prompts)", shape=folder];\n'
            '  cli -> apis [dir=both];\n'
            '  cli -> grobid [dir=both];\n'
            '  cli -> zotero [dir=both];\n'
            '  cli -> db [dir=both];\n'
            '  cli -> matrices [dir=both];\n'
            '  cli -> manuscript [label="guarded\\napply only"];\n'
            '  cli -> reports;\n'
            '  config -> cli;\n'
            '}\n'
        ),
        'mermaid': (
            'flowchart TB\n'
            '  CLI[PaperOps CLI<br/>scripts/paperops.py]\n'
            '  subgraph External services\n'
            '    G[GROBID Docker]\n'
            '    Z[Zotero + Better BibTeX]\n'
            '    A[Paper APIs<br/>arXiv, S2, OpenAlex]\n'
            '  end\n'
            '  subgraph Local stores\n'
            '    DB[(papers.sqlite)]\n'
            '    M[(matrices/)]\n'
            '    MS[(05_manuscript/)]\n'
            '    R[(reports/)]\n'
            '  end\n'
            '  CFG[config/] --> CLI\n'
            '  CLI <--> A\n'
            '  CLI <--> G\n'
            '  CLI <--> Z\n'
            '  CLI <--> DB\n'
            '  CLI <--> M\n'
            '  CLI -->|guarded apply only| MS\n'
            '  CLI --> R\n'
        ),
    },
    'fig_verification_states': {
        'title': 'Evidence verification state transitions',
        'caption': (
            'Evidence verification state transitions. There is no automated '
            'transition into the verified state; only a human reviewer can mark '
            'evidence as verified, and guards enforce this invariant.'
        ),
        'target_file': '05_manuscript/chapters/ch5_evaluation.qmd',
        'target_heading': '## Metrics',
        'dot': (
            'digraph fig_verification_states {\n' + GV_STYLE % 'LR' +
            '  extracted [label="extracted"];\n'
            '  validated [label="candidate\\nvalidated"];\n'
            '  in_review [label="in review", ' + HUMAN_NODE + '];\n'
            '  promoted [label="promoted\\n(verified=false)", ' + DATA_NODE + '];\n'
            '  pdf_check [label="pdf check\\nrequired"];\n'
            '  verified [label="verified=true", ' + GUARD_NODE + '];\n'
            '  rejected [label="rejected", fillcolor="#F7DCDC"];\n'
            '  extracted -> validated -> in_review;\n'
            '  in_review -> promoted [label="human keep"];\n'
            '  in_review -> rejected [label="human reject"];\n'
            '  promoted -> pdf_check;\n'
            '  pdf_check -> verified [label="human only", penwidth=2];\n'
            '  promoted -> verified [style=invis];\n'
            '  noauto [label="no automated edge\\ninto verified", shape=note, '
            'fillcolor="#FFF7D6"];\n'
            '}\n'
        ),
        'mermaid': (
            'stateDiagram-v2\n'
            '  [*] --> extracted\n'
            '  extracted --> validated\n'
            '  validated --> in_review\n'
            '  in_review --> promoted : human keep\n'
            '  in_review --> rejected : human reject\n'
            '  promoted --> pdf_check\n'
            '  pdf_check --> verified : human only\n'
            '  note right of verified : no automated transition\n'
        ),
    },
}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def dot_available():
    return shutil.which('dot') is not None


def render_figure(fig_id, formats=('svg', 'png')):
    """Write sources and render via Graphviz. Returns result dict."""
    spec = FIGURE_SPECS[fig_id]
    FIG_SRC_DIR.mkdir(parents=True, exist_ok=True)
    dot_path = FIG_SRC_DIR / f'{fig_id}.dot'
    mmd_path = FIG_SRC_DIR / f'{fig_id}.mmd'
    write_lf(dot_path, spec['dot'])
    write_lf(mmd_path, spec['mermaid'])
    result = {'figure_id': fig_id, 'dot': str(dot_path), 'mmd': str(mmd_path),
              'rendered': [], 'errors': []}
    if not dot_available():
        result['errors'].append('graphviz `dot` not found in PATH; sources written only')
        return result
    for fmt in formats:
        out = FIG_DIR / f'{fig_id}.{fmt}'
        cmd = ['dot', f'-T{fmt}', str(dot_path), '-o', str(out)]
        if fmt == 'png':
            cmd = ['dot', f'-T{fmt}', '-Gdpi=200', str(dot_path), '-o', str(out)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if proc.returncode != 0:
                result['errors'].append(f'{fmt}: {proc.stderr.strip()[:300]}')
            elif not out.exists() or out.stat().st_size == 0:
                result['errors'].append(f'{fmt}: output missing or empty')
            else:
                result['rendered'].append(str(out))
        except Exception as e:
            result['errors'].append(f'{fmt}: {type(e).__name__}: {e}')
    return result


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_propose_figures(args):
    out = FIG_DIR / f'figure_proposals_{today()}.md'
    lines = [f'# Figure Proposals {today()}\n\n',
             'Spec-driven figure candidates. Sources are always saved for '
             'reproducibility; rendering uses Graphviz (primary) with Mermaid '
             'sources for GitHub display.\n\n']
    for fig_id, spec in FIGURE_SPECS.items():
        target = ROOT / spec['target_file']
        placed = False
        if target.exists():
            placed = f'#{fig_id.replace("_", "-")}' in target.read_text(encoding='utf-8')
        lines.append(f'## {fig_id}\n\n')
        lines.append(f'- title: {spec["title"]}\n')
        lines.append(f'- target: `{spec["target_file"]}` / `{spec["target_heading"]}`\n')
        lines.append(f'- already_placed: {str(placed).lower()}\n')
        lines.append(f'- caption: {spec["caption"]}\n\n')
    lines.append('## Out of scope by design\n\n')
    lines.append('- Quantitative result charts are not proposed without an actual '
                 'experiment CSV. This module never fabricates results.\n')
    write_lf(out, ''.join(lines))
    print(out)
    print(f'figure_count={len(FIGURE_SPECS)}')
    log(f'propose-figures 실행: {len(FIGURE_SPECS)}개 후보 제안')


def cmd_render_figures(args):
    fig_ids = [args.figure_id] if getattr(args, 'figure_id', None) else list(FIGURE_SPECS)
    unknown = [f for f in fig_ids if f not in FIGURE_SPECS]
    if unknown:
        raise SystemExit(f'unknown figure id(s): {", ".join(unknown)}; '
                         f'known: {", ".join(FIGURE_SPECS)}')
    formats = tuple((getattr(args, 'formats', None) or 'svg,png').split(','))
    results = [render_figure(f, formats) for f in fig_ids]
    ok = [r for r in results if not r['errors']]
    failed = [r for r in results if r['errors']]
    out = FIG_DIR / f'figure_render_report_{today()}.md'
    lines = [f'# Figure Render Report {today()}\n\n']
    for r in results:
        lines.append(f'## {r["figure_id"]}\n\n')
        lines.append(f'- dot: `{Path(r["dot"]).relative_to(ROOT)}`\n')
        lines.append(f'- mmd: `{Path(r["mmd"]).relative_to(ROOT)}`\n')
        for p in r['rendered']:
            rp = Path(p)
            lines.append(f'- rendered: `{rp.relative_to(ROOT)}` ({rp.stat().st_size} bytes)\n')
        for e in r['errors']:
            lines.append(f'- ERROR: {e}\n')
        lines.append('\n')
    lines.append(f'- rendered_ok: {len(ok)}\n- failed: {len(failed)}\n')
    write_lf(out, ''.join(lines))
    print(out)
    print(f'rendered_ok={len(ok)}')
    print(f'failed={len(failed)}')
    log(f'render-figures 실행: ok={len(ok)}, failed={len(failed)}')
    if failed:
        raise SystemExit(1)


def figure_block(fig_id, spec):
    label = fig_id.replace('_', '-')
    rel = f'figures/{fig_id}.svg'
    return f'![{spec["caption"]}]({rel}){{#{label}}}\n'


def cmd_figure_placeholder_preview(args):
    fig_ids = [args.figure_id] if getattr(args, 'figure_id', None) else list(FIGURE_SPECS)
    unknown = [f for f in fig_ids if f not in FIGURE_SPECS]
    if unknown:
        raise SystemExit(f'unknown figure id(s): {", ".join(unknown)}')
    prefix = getattr(args, 'output_prefix', None) or f'figure_placeholder_preview_{today()}'
    rows = []
    md = [f'# Figure Placeholder Preview {today()}\n\n']
    for fig_id in fig_ids:
        spec = FIGURE_SPECS[fig_id]
        target = ROOT / spec['target_file']
        status, reason = 'ready', ''
        if not target.exists():
            status, reason = 'blocked', 'target file missing'
        else:
            text = target.read_text(encoding='utf-8')
            if spec['target_heading'] not in text:
                status, reason = 'blocked', f'heading not found: {spec["target_heading"]}'
            elif f'#{fig_id.replace("_", "-")}' in text:
                status, reason = 'skipped', 'placeholder already present'
        rows.append({
            'figure_id': fig_id,
            'target_file': spec['target_file'],
            'target_heading': spec['target_heading'],
            'target_sha256': file_sha256(target) if target.exists() else '',
            'insert_block': figure_block(fig_id, spec).strip(),
            'status': status,
            'reason': reason,
        })
        md.append(f'## {fig_id}\n\n- target: `{spec["target_file"]}` / '
                  f'`{spec["target_heading"]}`\n- status: {status}'
                  + (f' ({reason})' if reason else '') + '\n\n```\n'
                  + figure_block(fig_id, spec) + '```\n\n')
    csv_path = ROOT / f'reports/review/{prefix}.csv'
    md_path = ROOT / f'reports/review/{prefix}.md'
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
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


def cmd_apply_figure_placeholder(args):
    preview = ROOT / args.from_preview
    if not preview.exists():
        raise SystemExit(f'preview not found: {preview}')
    do_apply = bool(getattr(args, 'apply', False))
    dry_run = bool(getattr(args, 'dry_run', False)) or not do_apply
    with open(preview, encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    ready = [r for r in rows if r.get('status') == 'ready']
    errors = []
    applied = []
    # SHA precheck on all targets before touching anything
    for r in ready:
        target = ROOT / r['target_file']
        if not target.exists():
            errors.append(f'{r["figure_id"]}: target missing')
            continue
        if file_sha256(target) != r['target_sha256']:
            errors.append(f'{r["figure_id"]}: target SHA mismatch (regenerate preview)')
    if errors:
        for e in errors:
            print(f'ERROR: {e}')
        raise SystemExit(1)
    if dry_run:
        for r in ready:
            print(f'DRY-RUN would insert {r["figure_id"]} into {r["target_file"]} '
                  f'after "{r["target_heading"]}"')
        print(f'ready_rows={len(ready)}')
        log(f'apply-figure-placeholder dry-run: ready={len(ready)}')
        return
    # Backup all target chapters first
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_dir = ROOT / f'05_manuscript/backups/manuscript_before_figure_apply_{stamp}/chapters'
    backup_dir.mkdir(parents=True, exist_ok=True)
    for tf in sorted({r['target_file'] for r in ready}):
        shutil.copy2(ROOT / tf, backup_dir / Path(tf).name)
    # Copy rendered SVGs into manuscript figures dir
    MANUSCRIPT_FIG_DIR.mkdir(parents=True, exist_ok=True)
    for r in ready:
        svg = FIG_DIR / f'{r["figure_id"]}.svg'
        if not svg.exists():
            raise SystemExit(f'{r["figure_id"]}: rendered SVG missing; run render-figures first')
        shutil.copy2(svg, MANUSCRIPT_FIG_DIR / svg.name)
    # Insert blocks (one file may receive multiple figures)
    by_file = {}
    for r in ready:
        by_file.setdefault(r['target_file'], []).append(r)
    for tf, frs in by_file.items():
        target = ROOT / tf
        text = target.read_text(encoding='utf-8')
        for r in frs:
            heading = r['target_heading']
            idx = text.index(heading)
            # insert after the heading's paragraph block (after heading line + blank)
            line_end = text.index('\n', idx)
            insertion = '\n' + r['insert_block'] + '\n'
            text = text[:line_end + 1] + insertion + text[line_end + 1:]
            applied.append(r['figure_id'])
        write_lf(target, text)
    report = ROOT / f'reports/review/figure_apply_{today()}.md'
    lines = [f'# Figure Apply Report {today()}\n\n## Summary\n',
             '- mode: apply\n', '- applied: true\n',
             f'- applied_rows: {len(applied)}\n',
             f'- blocked_rows: 0\n',
             f'- backup: `{backup_dir.parent.relative_to(ROOT)}`\n',
             '\n## Applied figures\n']
    lines += [f'- {fid}\n' for fid in applied]
    lines += ['\n## Errors\n- none\n']
    write_lf(report, ''.join(lines))
    print(report)
    print(f'applied_rows={len(applied)}')
    print('blocked_rows=0')
    log(f'apply-figure-placeholder apply: applied={len(applied)}, backup={backup_dir}')


def register_subcommands(sub):
    p = sub.add_parser('propose-figures', help='List spec-driven figure candidates')
    p.set_defaults(func=cmd_propose_figures)
    p = sub.add_parser('render-figures', help='Write .dot/.mmd sources and render via Graphviz')
    p.add_argument('--figure-id')
    p.add_argument('--formats', default='svg,png')
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
