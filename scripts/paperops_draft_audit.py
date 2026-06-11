# -*- coding: utf-8 -*-
"""PaperOps draft audit.

Audits a thesis draft (docx/txt/md/qmd) for evidence accountability:
structure, source mentions, unsupported strong claims, overclaim language,
unverified numeric claims, and Evidence Matrix coverage.

This is a *flagging* tool, not a truth validator: it tells the author which
sentences need sources, verification, or replacement with real results.
It never modifies the draft and never marks anything as verified.
"""
from __future__ import annotations
import argparse
import csv
import re
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / 'logs/ACTIVITY_LOG.md'
W_NS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


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


def extract_docx_paragraphs(path):
    with zipfile.ZipFile(path) as z:
        xml = z.read('word/document.xml')
    root = ET.fromstring(xml)
    paras = []
    for p in root.iter(f'{W_NS}p'):
        text = ''.join(t.text or '' for t in p.iter(f'{W_NS}t'))
        if text.strip():
            paras.append(text.strip())
    return paras


def load_paragraphs(path):
    path = Path(path)
    if path.suffix.lower() == '.docx':
        return extract_docx_paragraphs(path)
    text = path.read_text(encoding='utf-8', errors='ignore')
    return [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]


def split_sentences(para):
    # Korean sentences mostly end with '다.' / '함.' etc.; keep simple+robust.
    parts = re.split(r'(?<=[.!?])\s+', para)
    return [s.strip() for s in parts if len(s.strip()) >= 10]


# ---------------------------------------------------------------------------
# Heuristic patterns
# ---------------------------------------------------------------------------

# A sentence "mentions a source" if it names a standard, a cited work, a
# venue+year, or an explicit reference marker.
SOURCE_MENTION = re.compile(
    r'\[(?:\d{1,3}|@[A-Za-z])'                       # [1], [@key]
    r'|\(\s*[A-Z][A-Za-z-]+(?:\s+et\s+al\.?)?,?\s*\d{4}\s*\)'  # (Noy, 2001)
    r'|\b(19|20)\d{2}년\b.{0,30}(연구|논문|표준|보고)'
    r'|Ontology Development 101|FEEKG|FinCaKG|FintechKG'
    r'|W3C|SHACL Recommendation|ISO/IEC\s*\d+|Neo4j|Cypher Manual'
    r'|OPEN DART|KRX|FRED|ECOS|Data\.go\.kr'
    r'|선행연구|기존 연구|문헌에서|에 따르면|보고되었|제안되었|알려져',
    re.IGNORECASE)

STRONG_CLAIM = re.compile(
    r'증명(한다|되었|했다)|입증(한다|되었)|보장(한다|된다)'
    r'|항상\s|반드시.{0,8}(향상|개선|성공)'
    r'|모든\s.{0,20}(가능하다|해결한다)'
    r'|유일(한|하게)|최초(로|의)'
    r'|획기적|혁신적|압도적'
    r'|(성능|정확도|효율).{0,10}(탁월|월등|극대화)')

OVERCLAIM = re.compile(
    r'완벽(하|한)|완전히\s+해결|100\s*%\s*(보장|정확)'
    r'|hallucination[을를]?\s*제거|오류가\s*없'
    r'|state[- ]of[- ]the[- ]art|SOTA')

# Specific numeric results that must come from actual experiments.
NUMERIC_RESULT = re.compile(
    r'\d{1,3}(,\d{3})+\s*개|\d+\.\d+\s*%|precision\s*[=:]\s*\d|recall\s*[=:]\s*\d'
    r'|F1\s*[=:]\s*\d|\d+\s*개\s*중\s*\d+')

HYPOTHETICAL = re.compile(r'예를 들어|예컨대|가령|예시|라면|다면|할 수 있다')

# Expected thesis structure markers (Korean).
EXPECTED_SECTIONS = [
    ('서론', r'서\s*론|Introduction'),
    ('관련연구', r'관련\s*연구|선행\s*연구|Related Work'),
    ('연구설계/방법', r'연구\s*설계|연구\s*방법|방법론|Method'),
    ('아티팩트/온톨로지 설계', r'온톨로지\s*설계|시스템\s*설계|Ontology|Design'),
    ('평가/검증', r'평가|검증|Evaluation|Validation'),
    ('결론', r'결\s*론|Conclusion'),
    ('참고문헌', r'참고\s*문헌|References|원문\s*확인'),
]


def read_evidence_matrix_sources():
    path = ROOT / 'matrices/evidence_matrix.csv'
    if not path.exists():
        return set()
    keys = set()
    with open(path, encoding='utf-8') as f:
        for row in csv.DictReader(f):
            for field in ('citekey', 'paper_id', 'title'):
                v = (row.get(field) or '').strip().lower()
                if v:
                    keys.add(v)
    return keys


def audit_draft(input_path, output_prefix=None):
    paras = load_paragraphs(input_path)
    full_text = '\n'.join(paras)
    findings = []
    n_sentences = 0
    for i, para in enumerate(paras):
        for sent in split_sentences(para):
            n_sentences += 1
            mentions = bool(SOURCE_MENTION.search(sent))
            strong = STRONG_CLAIM.search(sent)
            over = OVERCLAIM.search(sent)
            numeric = NUMERIC_RESULT.search(sent)
            hypo = bool(HYPOTHETICAL.search(sent))
            if over:
                findings.append((i, 'overclaim', over.group(0), sent))
            elif strong and not mentions:
                findings.append((i, 'strong_claim_no_source', strong.group(0), sent))
            if numeric and not hypo:
                findings.append((i, 'numeric_needs_data', numeric.group(0), sent))
            elif numeric and hypo:
                findings.append((i, 'numeric_hypothetical_ok', numeric.group(0), sent))

    # Structure check
    structure = []
    for name, pat in EXPECTED_SECTIONS:
        structure.append((name, bool(re.search(pat, full_text))))

    # Named-source inventory: things the draft cites informally
    named_sources = sorted(set(
        m.group(0) for m in re.finditer(
            r'Ontology Development 101|FEEKG|FinCaKG-?Onto|FintechKG'
            r'|ISO/IEC\s*39075(:2024)?|SHACL|GQL|Neo4j|Cypher'
            r'|OPEN DART|KRX|FRED|ECOS', full_text)))

    # Evidence Matrix coverage of named sources
    matrix_keys = read_evidence_matrix_sources()
    coverage = []
    for s in named_sources:
        hit = any(s.lower() in k for k in matrix_keys)
        coverage.append((s, hit))

    # Output
    prefix = output_prefix or f'draft_audit_{today()}'
    csv_path = ROOT / f'reports/review/{prefix}.csv'
    md_path = ROOT / f'reports/review/{prefix}.md'
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['para_index', 'finding_type', 'trigger', 'sentence'])
        for row in findings:
            w.writerow(row)

    by_type = {}
    for _, t, _, _ in findings:
        by_type[t] = by_type.get(t, 0) + 1

    lines = [f'# Draft Audit {today()}\n\n',
             f'- input: `{input_path}`\n',
             f'- paragraphs: {len(paras)}\n',
             f'- sentences_scanned: {n_sentences}\n',
             f'- findings_total: {len(findings)}\n\n',
             '## Finding counts\n\n']
    for t, c in sorted(by_type.items()):
        lines.append(f'- {t}: {c}\n')
    lines.append('\n## Structure check\n\n')
    for name, ok in structure:
        lines.append(f'- {"[OK]" if ok else "[MISSING]"} {name}\n')
    lines.append('\n## Named sources mentioned in draft\n\n')
    for s, hit in coverage:
        mark = 'in Evidence Matrix' if hit else 'NOT in Evidence Matrix (수집/검증 필요)'
        lines.append(f'- {s}: {mark}\n')
    lines.append('\n## Top findings\n\n')
    shown = 0
    for idx, t, trig, sent in findings:
        if t == 'numeric_hypothetical_ok':
            continue
        lines.append(f'- [{t}] (문단 {idx}, trigger: `{trig}`)\n  > {sent[:200]}\n')
        shown += 1
        if shown >= 40:
            lines.append(f'- ... (전체는 CSV 참조)\n')
            break
    lines.append('\n## Disclaimer\n\n')
    lines.append('이 감사는 휴리스틱 플래깅이며 진실 검증이 아니다. '
                 '플래그된 문장은 사람이 원문/데이터로 확인해야 한다. '
                 '이 도구는 초안을 수정하지 않으며 verified 상태를 만들지 않는다.\n')
    with open(md_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(''.join(lines))
    print(csv_path)
    print(md_path)
    print(f'paragraphs={len(paras)}')
    print(f'sentences={n_sentences}')
    for t, c in sorted(by_type.items()):
        print(f'{t}={c}')
    missing = [name for name, ok in structure if not ok]
    print(f'missing_sections={",".join(missing) if missing else "(none)"}')
    log(f'audit-manuscript-draft 실행: input={input_path}, findings={len(findings)}')


def cmd_audit_manuscript_draft(args):
    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = ROOT / args.input
    if not input_path.exists():
        raise SystemExit(f'input not found: {input_path}')
    audit_draft(input_path, getattr(args, 'output_prefix', None))


def register_subcommands(sub):
    p = sub.add_parser('audit-manuscript-draft',
                       help='Audit a draft (docx/txt/md/qmd) for evidence accountability')
    p.add_argument('--input', required=True)
    p.add_argument('--output-prefix')
    p.set_defaults(func=cmd_audit_manuscript_draft)
