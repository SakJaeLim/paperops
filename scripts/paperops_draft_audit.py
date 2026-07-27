# -*- coding: utf-8 -*-
"""PaperOps draft audit.

Audits a thesis draft (docx/txt/md/qmd) for evidence accountability:
structure, source mentions, unsupported strong claims, overclaim language,
numeric claims cross-checked against experiment output files, and Evidence
Matrix coverage.

This is a *flagging* tool, not a truth validator: it tells the author which
sentences need sources, verification, or replacement with real results.
It never modifies the draft and never marks anything as verified.
Numeric matches against experiment outputs are *alignment*, not truth
validation (see project governance rules).

Detector defaults (Korean + English) are built in; author-/topic-specific
values (source-name whitelist, chapter structure, extra claim patterns) live
in config/draft_audit.yaml and are merged over neutral built-in defaults.
Works without PyYAML (falls back to the neutral defaults).
"""
from __future__ import annotations
import argparse
import copy
import csv
import io
import re
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / 'logs/ACTIVITY_LOG.md'
W_NS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

DEFAULT_CONFIG_PATH = ROOT / 'config/draft_audit.yaml'
DEFAULT_EXPERIMENT_DATA = 'data/experiment_outputs'
DEFAULT_REPORT_SUBDIR = 'reports/draft_audit'
EXPERIMENT_SUFFIXES = ('.csv', '.tsv', '.json', '.txt', '.md')
# Skip absurdly large experiment files (regex scan would stall); noted in report.
EXPERIMENT_MAX_BYTES = 20 * 1024 * 1024

MISSING_DATA_LINE = ('experiment data dir not found — '
                     'numeric values flagged for manual check')


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


# ---------------------------------------------------------------------------
# Encoding-safe text reading
# ---------------------------------------------------------------------------

def read_text_safe(path):
    """Read a text file without silently destroying non-UTF-8 content.

    Order: utf-8-sig strict -> cp949 strict -> euc-kr strict ->
    utf-8 errors='replace' (last resort, with a prominent WARNING).
    Never uses errors='ignore'.

    Returns (text, note) where note is None, or a 'NOTE: ...' string for a
    successful non-UTF-8 decode, or a 'WARNING: ...' string for the lossy
    replace fallback. Notes/warnings are surfaced in the audit report.
    """
    raw = Path(path).read_bytes()
    for enc in ('utf-8-sig', 'cp949', 'euc-kr'):
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
        if enc == 'utf-8-sig':
            return text, None
        return text, f'NOTE: {path} was decoded as {enc} (not UTF-8)'
    text = raw.decode('utf-8', errors='replace')
    warning = (f'WARNING: {path} could not be decoded strictly '
               '(tried utf-8-sig, cp949, euc-kr); read with utf-8 '
               "errors='replace' — some characters were replaced with U+FFFD; "
               'verify this file manually')
    return text, warning


# ---------------------------------------------------------------------------
# Configuration (neutral built-in defaults + config/draft_audit.yaml overlay)
# ---------------------------------------------------------------------------

# Neutral defaults: generic thesis structure, no topic-specific source names.
# The shipped config/draft_audit.yaml carries the author's current values so
# behavior is preserved for existing users.
BUILTIN_CONFIG = {
    'source_mentions': [],
    'required_chapters': [
        {'name': '서론', 'patterns': ['서\\s*론', 'Introduction']},
        {'name': '관련연구', 'patterns': ['관련\\s*연구', '선행\\s*연구',
                                      'Related\\s*Work', 'Literature\\s*Review']},
        {'name': '연구방법', 'patterns': ['연구\\s*설계', '연구\\s*방법', '방법론',
                                      'Method(?:s|ology)?']},
        {'name': '평가/검증', 'patterns': ['평가', '검증', '결과', 'Evaluation',
                                       'Validation', 'Results?', 'Experiments?']},
        {'name': '결론', 'patterns': ['결\\s*론', 'Conclusion']},
        {'name': '참고문헌', 'patterns': ['참고\\s*문헌', 'References', 'Bibliography']},
    ],
    'overclaim_extra': [],
    'strong_claim_extra': [],
}

CONFIG_LIST_KEYS = ('source_mentions', 'required_chapters',
                    'overclaim_extra', 'strong_claim_extra')


def load_config(config_path=None):
    """Load config/draft_audit.yaml if present, merged over neutral defaults.

    Degrades gracefully: missing file, missing PyYAML, or a broken YAML all
    fall back to the built-in defaults (same guarded-import pattern as
    load_yaml() in scripts/paperops.py).
    """
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    cfg = copy.deepcopy(BUILTIN_CONFIG)
    if not path.exists():
        return cfg
    try:
        import yaml
    except Exception:
        return cfg
    try:
        text, _note = read_text_safe(path)
        loaded = yaml.safe_load(text) or {}
    except Exception:
        return cfg
    if isinstance(loaded, dict):
        for key in CONFIG_LIST_KEYS:
            value = loaded.get(key)
            if isinstance(value, list):
                cfg[key] = value
    return cfg


def _compile_fragment(fragment, flags=0):
    """Compile a config-supplied regex fragment; fall back to literal."""
    try:
        return re.compile(fragment, flags)
    except re.error:
        return re.compile(re.escape(str(fragment)), flags)


def _compile_alternation(fragments, flags=0):
    if not fragments:
        return None
    parts = []
    for frag in sorted((str(f) for f in fragments), key=len, reverse=True):
        try:
            re.compile(frag)
            parts.append(f'(?:{frag})')
        except re.error:
            parts.append(f'(?:{re.escape(frag)})')
    return re.compile('|'.join(parts), flags)


# ---------------------------------------------------------------------------
# Heuristic patterns (built-in detector defaults, Korean + English)
# ---------------------------------------------------------------------------

# A sentence "mentions a source" if it carries a citation marker, an
# author-year citation (Korean or English), or an explicit reference phrase.
SOURCE_GENERIC = re.compile(
    # [1], [1,2], [1-3], [1–3], [@citekey]
    r'\[\s*(?:\d{1,3}(?:\s*[,–—-]\s*\d{1,3})*|@[A-Za-z][\w:.-]*)\s*\]'
    # (Noy, 2001), (Kim et al., 2020), (Kim & Lee, 2020), (Kim and Lee 2020)
    r'|\(\s*[A-Z][A-Za-z-]+(?:\s+(?:et\s+al\.?|and\s+[A-Z][A-Za-z-]+|&\s*[A-Z][A-Za-z-]+))?\s*,?\s*(?:19|20)\d{2}[a-z]?\s*\)'
    # (김철수, 2020), (김철수 외, 2020), (김철수 등, 2020), (김철수 외 2020)
    r'|\(\s*[가-힣]+(?:\s*(?:외|등))?\s*,?\s*(?:19|20)\d{2}[a-z]?\s*\)'
    r'|\b(19|20)\d{2}년\b.{0,30}(연구|논문|표준|보고)'
    r'|선행연구|기존 연구|문헌에서|에 따르면|보고되었|제안되었|알려져',
    re.IGNORECASE)

STRONG_CLAIM_KO = re.compile(
    r'증명(한다|되었|했다)|입증(한다|되었)|보장(한다|된다)'
    r'|항상\s|반드시.{0,8}(향상|개선|성공)'
    r'|모든\s.{0,20}(가능하다|해결한다)'
    r'|유일(한|하게)|최초(로|의)'
    r'|획기적|혁신적|압도적'
    r'|(성능|정확도|효율).{0,10}(탁월|월등|극대화)')

STRONG_CLAIM_EN = re.compile(
    r'\bprove(?:s|d|n)?\b|\bguarantee(?:s|d)?\b|\balways\b'
    r'|\bnever\s+fails?\b|\bensures?\b|\bdemonstrates?\s+conclusively\b',
    re.IGNORECASE)

OVERCLAIM_KO = re.compile(
    r'완벽(하|한)|완전히\s+해결|100\s*%\s*(보장|정확)|오류가\s*없')

OVERCLAIM_EN = re.compile(
    r'state[-\s]of[-\s]the[-\s]art|world[-\s]?first|best[-\s]in[-\s]class'
    r'|\bperfect(?:ly)?\b|\bflawless\b|\bunprecedented\b'
    r'|\bgroundbreaking\b|\brevolutionary\b'
    r'|\bnovel\s+(?:framework|method|approach|architecture|paradigm|system)\b',
    re.IGNORECASE)

# SOTA kept case-sensitive so ordinary words/names are not caught.
OVERCLAIM_SOTA = re.compile(r'\bSOTA\b')

# Hypothetical/example context suppresses numeric flags.
# Conditional-mood suppression matches only a *true* conditional verb ending:
# a hangul syllable + 다면/라면 (한다면, 된다면, 좋다면, 이라면, 아니라면)
# followed by whitespace/punctuation/end of sentence. This fixes two bugs:
#  - '다면' no longer matches inside words like '다면적' (blocked by the
#    trailing-boundary lookahead);
#  - the noun '라면' (standalone, e.g. '라면 시장') no longer matches because
#    the ending must be attached to a preceding hangul syllable.
# '할 수 있다' was removed entirely: capability claims still need sources.
HYPOTHETICAL_MARKER = re.compile(r'예를 들어|예컨대|가령|예시')
CONDITIONAL_ENDING = re.compile(
    r'(?:[가-힣]다면|이라면|아니라면)(?=[\s,.!?;:)\]”’]|$)')


def is_hypothetical(sentence):
    if HYPOTHETICAL_MARKER.search(sentence):
        return True
    return bool(CONDITIONAL_ENDING.search(sentence))


# ---------------------------------------------------------------------------
# Numeric extraction heuristic
# ---------------------------------------------------------------------------
# What counts as a checkable numeric result in the draft:
#   - comma-grouped thousands (1,234 / 12,345.6) with or without a unit;
#   - decimals (95.5 / 0.87), with or without % or a unit;
#   - plain integers ONLY when they carry a result-like unit suffix
#     (%, 퍼센트, 개, 건, 배, 점, x, times) or sit in a measurement context
#     (precision/recall/accuracy/F1/AUC/정확도/정밀도/재현율/점수 = N, 'N개 중 M').
# What is deliberately excluded (heuristic, documented per spec):
#   - standalone years: bare integers such as 1990–2035 never match because a
#     bare integer without a unit/context is not extracted at all, and '년' is
#     not an accepted unit ('2020년' is a date, not a result);
#   - list indices / section numbering: '1.' list markers are bare integers
#     (not extracted); leading section numbers like '1.2 연구 방법' are dropped
#     by the start-of-sentence guard; multi-dot tokens (2.1.3, v3.10) are
#     dropped as version/section identifiers;
#   - figure/table/section/version references: a decimal directly preceded by
#     그림/표/Fig/Table/Section/버전/v or followed by 절/장/항/조 is dropped.
NUM_TOKEN = re.compile(
    r'-?\d{1,3}(?:,\d{3})+(?:\.\d+)?'   # comma-grouped thousands
    r'|-?\d+\.\d+'                      # decimal
    r'|-?\d+'                           # integer (kept only with unit/context)
)
UNIT_AFTER = re.compile(r'^(?:\s*(?:%|퍼센트|개|건|배|점)|x\b|\s*times\b)',
                        re.IGNORECASE)
METRIC_BEFORE = re.compile(
    r'(?:precision|recall|accuracy|f1(?:[-\s]?score)?|auc|bleu|rouge'
    r'|정확도|정밀도|재현율|점수|[개건명]\s*중)\s*(?:[=:]|는|은|이|가|의)?\s*$',
    re.IGNORECASE)
REF_BEFORE = re.compile(
    r'(?:그림|표|도표|Fig(?:ure)?\.?|Table|Tab\.?|Section|Sec\.?|버전'
    r'|ver(?:sion)?\.?|\bv)\s*$', re.IGNORECASE)
SECTION_AFTER = re.compile(r'^\s*(?:절|장|항|조)')
YEAR_MIN, YEAR_MAX = 1990, 2035


def extract_numeric_tokens(sentence):
    """Extract checkable numeric tokens from a draft sentence.

    Returns a list of dicts: {raw, norm, value, decimals}.
    norm is the string-normalized form (commas/%/units stripped) used for
    exact matching against experiment outputs.
    """
    tokens = []
    for m in NUM_TOKEN.finditer(sentence):
        tok = m.group(0)
        before = sentence[:m.start()]
        after = sentence[m.end():]
        unit_m = UNIT_AFTER.match(after)
        has_comma = ',' in tok
        has_dot = '.' in tok
        # multi-dot sequences (2.1.3, v3.10.1) are section/version identifiers
        if after[:1] == '.' and after[1:2].isdigit():
            continue
        if before[-1:] == '.' and before[-2:-1].isdigit():
            continue
        if not has_comma and not has_dot:
            # plain integer: needs a unit suffix or a measurement context
            if not unit_m and not METRIC_BEFORE.search(before):
                continue
            value = float(tok)
            # standalone-year guard (defensive; bare years carry no unit)
            if not unit_m and YEAR_MIN <= value <= YEAR_MAX and after[:1] == '년':
                continue
        elif has_dot and not unit_m:
            # bare decimal: drop figure/table/section/version references
            if REF_BEFORE.search(before) or SECTION_AFTER.match(after):
                continue
            if m.start() == 0 and re.match(r'-?\d+(?:\.\d+)+\s+\S', sentence):
                continue  # leading section numbering like '1.2 연구 방법'
        raw = tok + (unit_m.group(0).strip() if unit_m else '')
        norm = tok.replace(',', '')
        decimals = len(norm.split('.')[1]) if '.' in norm else 0
        try:
            value = float(norm)
        except ValueError:
            continue
        tokens.append({'raw': raw, 'norm': norm,
                       'value': value, 'decimals': decimals})
    return tokens


# ---------------------------------------------------------------------------
# Experiment-output index (numeric cross-check)
# ---------------------------------------------------------------------------

# Broad numeric scan for data files: over-inclusion on the data side is safe
# (it only means more candidates a draft number could align with).
DATA_NUM_TOKEN = re.compile(r'-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?')


class ExperimentIndex:
    """Numeric index over experiment output files.

    Matching semantics (alignment, never verification):
      matched_exact   - string-normalized equality (commas/% stripped)
      matched_rounded - a data number equals the draft number when rounded
                        to the draft number's decimal precision
      not_found_in_data - no alignment found
    """

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.files_scanned = []
        self.numbers_indexed = 0
        self.notes = []
        self.exact = {}    # norm string -> relative file name
        self.values = []   # (float value, relative file name)
        self._rounded = {}  # precision -> {formatted string -> file}

    def scan(self):
        if not self.data_dir.is_dir():
            return self
        for path in sorted(self.data_dir.rglob('*')):
            if not path.is_file():
                continue
            if path.suffix.lower() not in EXPERIMENT_SUFFIXES:
                continue
            rel = str(path.relative_to(self.data_dir))
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > EXPERIMENT_MAX_BYTES:
                self.notes.append(
                    f'NOTE: experiment file skipped (>20MB): {rel}')
                continue
            text, note = read_text_safe(path)
            if note:
                self.notes.append(note)
            self.files_scanned.append(rel)
            for m in DATA_NUM_TOKEN.finditer(text):
                norm = m.group(0).replace(',', '')
                try:
                    value = float(norm)
                except ValueError:
                    continue
                self.exact.setdefault(norm, rel)
                self.values.append((value, rel))
                self.numbers_indexed += 1
        return self

    @property
    def usable(self):
        return bool(self.files_scanned)

    def _rounded_map(self, precision):
        if precision not in self._rounded:
            table = {}
            for value, rel in self.values:
                key = f'{round(value, precision):.{precision}f}'
                table.setdefault(key, rel)
            self._rounded[precision] = table
        return self._rounded[precision]

    def check(self, token):
        """Return (status, matched_file) for one draft numeric token."""
        hit = self.exact.get(token['norm'])
        if hit is not None:
            return 'matched_exact', hit
        precision = token['decimals']
        key = f"{token['value']:.{precision}f}"
        hit = self._rounded_map(precision).get(key)
        if hit is not None:
            return 'matched_rounded', hit
        return 'not_found_in_data', ''


def resolve_experiment_dir(raw):
    """Resolve the experiment-data dir.

    Explicit relative paths resolve against the caller's CWD (like --input).
    The built-in default additionally falls back to the repo root so running
    the CLI from anywhere still finds the repo's data/experiment_outputs.
    """
    if raw:
        p = Path(raw)
        if p.is_absolute():
            return p
        return Path.cwd() / p
    cwd_p = Path.cwd() / DEFAULT_EXPERIMENT_DATA
    if cwd_p.is_dir():
        return cwd_p
    root_p = ROOT / DEFAULT_EXPERIMENT_DATA
    if root_p.is_dir():
        return root_p
    return cwd_p


# ---------------------------------------------------------------------------
# Draft loading (docx via stdlib zipfile + ElementTree; text via safe read)
# ---------------------------------------------------------------------------

HEADING_STYLE = re.compile(r'(?i)heading|title|제목')
# Fallback heading heuristic (docx without styles / plain text / md without
# '#'): a short line (<40 chars) that starts like a chapter/section number.
HEADING_FALLBACK = re.compile(r'^(제\s*\d+\s*장|\d+(\.\d+)*\s)')
MD_HEADING = re.compile(r'^\s{0,3}#{1,6}\s')


def _docx_para(p_el):
    """Flatten one w:p element: text runs with w:tab / w:br / w:cr mapped to
    a single space so table/box numbers do not concatenate."""
    parts = []
    has_footnote_ref = False
    has_outline_lvl = False
    style = None
    for node in p_el.iter():
        tag = node.tag
        if tag == f'{W_NS}t':
            parts.append(node.text or '')
        elif tag in (f'{W_NS}tab', f'{W_NS}br', f'{W_NS}cr'):
            parts.append(' ')
        elif tag in (f'{W_NS}footnoteReference', f'{W_NS}endnoteReference'):
            has_footnote_ref = True
        elif tag == f'{W_NS}pStyle':
            style = node.get(f'{W_NS}val') or ''
        elif tag == f'{W_NS}outlineLvl':
            has_outline_lvl = True
    text = re.sub(r'\s+', ' ', ''.join(parts)).strip()
    is_heading = has_outline_lvl or bool(style and HEADING_STYLE.search(style))
    return text, has_footnote_ref, is_heading


def _docx_notes_paras(z, member, kind):
    """Parse word/footnotes.xml / word/endnotes.xml paragraphs, tagged with a
    location like 'footnote:2' so findings cite them."""
    paras = []
    if member not in z.namelist():
        return paras
    root = ET.fromstring(z.read(member))
    note_tag = f'{W_NS}footnote' if kind == 'footnote' else f'{W_NS}endnote'
    for note in root.iter(note_tag):
        ntype = note.get(f'{W_NS}type') or ''
        if ntype in ('separator', 'continuationSeparator'):
            continue
        note_id = note.get(f'{W_NS}id') or '?'
        for p_el in note.iter(f'{W_NS}p'):
            text, has_ref, _heading = _docx_para(p_el)
            if text:
                paras.append({'text': text,
                              'location': f'{kind}:{note_id}',
                              'has_footnote_ref': has_ref,
                              'is_heading': False})
    return paras


def load_docx_draft(path):
    try:
        with zipfile.ZipFile(path) as z:
            try:
                xml = z.read('word/document.xml')
            except KeyError:
                raise SystemExit(
                    f'not a Word document (word/document.xml missing): {path}')
            try:
                root = ET.fromstring(xml)
            except ET.ParseError as e:
                raise SystemExit(f'cannot parse docx XML ({e}): {path}')
            paras = []
            for i, p_el in enumerate(root.iter(f'{W_NS}p')):
                text, has_ref, is_heading = _docx_para(p_el)
                if text:
                    paras.append({'text': text,
                                  'location': f'body:{i}',
                                  'has_footnote_ref': has_ref,
                                  'is_heading': is_heading})
            paras.extend(_docx_notes_paras(z, 'word/footnotes.xml', 'footnote'))
            paras.extend(_docx_notes_paras(z, 'word/endnotes.xml', 'endnote'))
    except zipfile.BadZipFile:
        raise SystemExit(f'cannot read docx (not a valid zip archive): {path}')
    # Structure check is heading-anchored: use styled headings when the
    # document has any; otherwise fall back to short chapter-numbered lines.
    styled = [p['text'] for p in paras
              if p['is_heading'] and p['location'].startswith('body')]
    if styled:
        headings = styled
    else:
        headings = [p['text'] for p in paras
                    if p['location'].startswith('body')
                    and len(p['text']) < 40
                    and HEADING_FALLBACK.match(p['text'])]
    return paras, headings, []


def load_text_draft(path):
    text, note = read_text_safe(path)
    notes = [note] if note else []
    paras = []
    for i, chunk in enumerate(re.split(r'\n\s*\n', text)):
        chunk = chunk.strip()
        if chunk:
            paras.append({'text': chunk,
                          'location': f'body:{i}',
                          'has_footnote_ref': False,
                          'is_heading': False})
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if path.suffix.lower() in ('.md', '.qmd', '.markdown'):
        headings = [ln for ln in lines if MD_HEADING.match(ln)]
        if not headings:  # Korean drafts often use 제N장 without '#'
            headings = [ln for ln in lines
                        if len(ln) < 40 and HEADING_FALLBACK.match(ln)]
    else:
        headings = [ln for ln in lines
                    if len(ln) < 40 and HEADING_FALLBACK.match(ln)]
    return paras, headings, notes


def load_draft(path):
    path = Path(path)
    if path.suffix.lower() == '.docx':
        return load_docx_draft(path)
    return load_text_draft(path)


def split_sentences(para):
    # Korean sentences mostly end with '다.' / '함.' etc.; keep simple+robust.
    # Minimum length 4 so short overclaims like '완벽하다.' are still seen.
    parts = re.split(r'(?<=[.!?。])\s+', para)
    return [s.strip() for s in parts if len(s.strip()) >= 4]


# ---------------------------------------------------------------------------
# Detectors assembled from config
# ---------------------------------------------------------------------------

def build_detectors(cfg):
    named = cfg.get('source_mentions') or []
    strong_extra = cfg.get('strong_claim_extra') or []
    over_extra = cfg.get('overclaim_extra') or []
    chapters = []
    for ch in cfg.get('required_chapters') or []:
        if not isinstance(ch, dict):
            continue
        name = str(ch.get('name') or '').strip() or '(unnamed)'
        pats = [_compile_fragment(str(p)) for p in (ch.get('patterns') or [])]
        if pats:
            chapters.append((name, pats))
    return {
        # source-name whitelist: IGNORECASE for source detection,
        # case-sensitive for the named-source inventory (legacy behavior)
        'source_named': _compile_alternation(named, re.IGNORECASE),
        'source_named_cs': _compile_alternation(named),
        'strong_extra': [_compile_fragment(p, re.IGNORECASE)
                         for p in strong_extra],
        'over_extra': [_compile_fragment(p, re.IGNORECASE)
                       for p in over_extra],
        'chapters': chapters,
    }


def sentence_has_source(sentence, det, has_footnote_ref=False):
    # A footnote/endnote reference on the paragraph counts as having a source.
    if has_footnote_ref:
        return True
    if SOURCE_GENERIC.search(sentence):
        return True
    named = det.get('source_named')
    return bool(named and named.search(sentence))


def find_strong_claim(sentence, det):
    m = STRONG_CLAIM_KO.search(sentence) or STRONG_CLAIM_EN.search(sentence)
    if m:
        return m
    for pat in det.get('strong_extra') or []:
        m = pat.search(sentence)
        if m:
            return m
    return None


def find_overclaim(sentence, det):
    m = (OVERCLAIM_KO.search(sentence) or OVERCLAIM_EN.search(sentence)
         or OVERCLAIM_SOTA.search(sentence))
    if m:
        return m
    for pat in det.get('over_extra') or []:
        m = pat.search(sentence)
        if m:
            return m
    return None


# ---------------------------------------------------------------------------
# Evidence Matrix coverage
# ---------------------------------------------------------------------------

def read_evidence_matrix_sources():
    path = ROOT / 'matrices/evidence_matrix.csv'
    if not path.exists():
        return set()
    keys = set()
    try:
        text, _note = read_text_safe(path)
        for row in csv.DictReader(io.StringIO(text)):
            for field in ('citekey', 'paper_id', 'title'):
                v = (row.get(field) or '').strip().lower()
                if v:
                    keys.add(v)
    except Exception:
        return set()
    return keys


# ---------------------------------------------------------------------------
# Main audit
# ---------------------------------------------------------------------------

def resolve_input_path(raw):
    """Relative --input resolves against the caller's CWD; absolute paths are
    kept as-is. If the CWD-relative path does not exist but the legacy
    repo-root-relative one does, fall back to it (pre-v0.2 invocations)."""
    p = Path(raw)
    if p.is_absolute():
        return p
    cwd_p = Path.cwd() / p
    if cwd_p.exists():
        return cwd_p
    root_p = ROOT / p
    if root_p.exists():
        return root_p
    return cwd_p


def audit_draft(input_path, output_prefix=None, experiment_data=None,
                report_dir=None, config_path=None):
    """Run the audit. Read-only with respect to the draft: this function
    never modifies the input file and never marks anything as verified."""
    input_path = Path(input_path)
    cfg = load_config(config_path)
    det = build_detectors(cfg)
    paras, headings, notes = load_draft(input_path)
    full_text = '\n'.join(p['text'] for p in paras)

    data_dir = resolve_experiment_dir(experiment_data)
    index = ExperimentIndex(data_dir).scan()
    have_data = index.usable
    notes.extend(index.notes)

    findings = []   # (para_index, finding_type, trigger, sentence, location)
    numeric_rows = []  # (value, sentence excerpt, status, matched file, location)
    numeric_summary = {'checked': 0, 'matched_exact': 0,
                       'matched_rounded': 0, 'not_found_in_data': 0}
    n_sentences = 0
    for i, para in enumerate(paras):
        loc = para['location']
        for sent in split_sentences(para['text']):
            n_sentences += 1
            has_source = sentence_has_source(sent, det,
                                             para['has_footnote_ref'])
            strong = find_strong_claim(sent, det)
            over = find_overclaim(sent, det)
            hypo = is_hypothetical(sent)
            if over:
                findings.append((i, 'overclaim', over.group(0), sent, loc))
            elif strong and not has_source:
                findings.append((i, 'strong_claim_no_source',
                                 strong.group(0), sent, loc))
            tokens = extract_numeric_tokens(sent)
            if not tokens:
                continue
            if hypo:
                findings.append((i, 'numeric_hypothetical_ok',
                                 tokens[0]['raw'], sent, loc))
                continue
            if not have_data:
                findings.append((i, 'numeric_needs_data',
                                 tokens[0]['raw'], sent, loc))
                continue
            missing = []
            for tok in tokens:
                status, matched_file = index.check(tok)
                numeric_summary['checked'] += 1
                numeric_summary[status] += 1
                numeric_rows.append((tok['raw'], sent[:120], status,
                                     matched_file, loc))
                if status == 'not_found_in_data':
                    missing.append(tok['raw'])
            if missing:
                findings.append((i, 'numeric_not_found_in_data',
                                 '; '.join(missing), sent, loc))

    # Structure check: heading-anchored — a chapter counts as present only if
    # one of its patterns matches a heading line, never body prose.
    structure = []
    for name, pats in det['chapters']:
        ok = any(p.search(h) for h in headings for p in pats)
        structure.append((name, ok))

    # Named-source inventory: things the draft cites informally
    named_cs = det.get('source_named_cs')
    named_sources = sorted(set(
        m.group(0) for m in named_cs.finditer(full_text))) if named_cs else []

    # Evidence Matrix coverage of named sources
    matrix_keys = read_evidence_matrix_sources()
    coverage = []
    for s in named_sources:
        hit = any(s.lower() in k for k in matrix_keys)
        coverage.append((s, hit))

    # Output (reports only; the draft itself is never touched)
    prefix = output_prefix or f'draft_audit_{today()}'
    if report_dir:
        rd = Path(report_dir)
        out_dir = rd if rd.is_absolute() else (Path.cwd() / rd)
    else:
        out_dir = ROOT / DEFAULT_REPORT_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f'{prefix}.csv'
    md_path = out_dir / f'{prefix}.md'
    numeric_csv_path = out_dir / f'{prefix}_numeric.csv'

    # CSVs are utf-8-sig (BOM) so Excel opens Korean text correctly.
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['para_index', 'finding_type', 'trigger', 'sentence',
                    'location'])
        for row in findings:
            w.writerow(row)

    wrote_numeric_csv = False
    if have_data:
        with open(numeric_csv_path, 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(['value', 'sentence_excerpt', 'status',
                        'matched_file', 'location'])
            for row in numeric_rows:
                w.writerow(row)
        wrote_numeric_csv = True

    by_type = {}
    for _, t, _, _, _ in findings:
        by_type[t] = by_type.get(t, 0) + 1

    lines = [f'# Draft Audit {today()}\n\n',
             f'- input: `{input_path}`\n',
             f'- paragraphs: {len(paras)}\n',
             f'- sentences_scanned: {n_sentences}\n',
             f'- findings_total: {len(findings)}\n\n']
    if notes:
        lines.append('## Encoding warnings\n\n')
        for note in notes:
            lines.append(f'- {note}\n')
        lines.append('\n')
    lines.append('## Finding counts\n\n')
    for t, c in sorted(by_type.items()):
        lines.append(f'- {t}: {c}\n')
    lines.append('\n## Numeric cross-check vs experiment outputs\n\n')
    if have_data:
        lines.append(f'- experiment_data_dir: `{data_dir}` '
                     f'(files_scanned={len(index.files_scanned)}, '
                     f'numbers_indexed={index.numbers_indexed})\n')
        lines.append(f"- numeric_checked: {numeric_summary['checked']}\n")
        lines.append(f"- matched_exact: {numeric_summary['matched_exact']}\n")
        lines.append(
            f"- matched_rounded: {numeric_summary['matched_rounded']}\n")
        lines.append(
            f"- not_found_in_data: {numeric_summary['not_found_in_data']}\n")
        lines.append(f'- per-number rows: `{numeric_csv_path.name}`\n')
        lines.append('- 일치(match)는 실험 산출 파일과의 정렬(alignment)이며 '
                     '진실 검증이 아니다. 어떤 수치도 verified로 표시되지 않는다. '
                     '(Matching is alignment with experiment output files, '
                     'not truth validation; nothing is marked verified.)\n')
    else:
        lines.append(f'- {MISSING_DATA_LINE}\n')
        lines.append(f'- looked for: `{data_dir}`\n')
    lines.append('\n## Structure check\n\n')
    for name, ok in structure:
        lines.append(f'- {"[OK]" if ok else "[MISSING]"} {name}\n')
    lines.append('\n## Named sources mentioned in draft\n\n')
    for s, hit in coverage:
        mark = 'in Evidence Matrix' if hit else 'NOT in Evidence Matrix (수집/검증 필요)'
        lines.append(f'- {s}: {mark}\n')
    lines.append('\n## Top findings\n\n')
    shown = 0
    for idx, t, trig, sent, loc in findings:
        if t == 'numeric_hypothetical_ok':
            continue
        where = f'문단 {idx}' if loc.startswith('body') else f'문단 {idx}, {loc}'
        lines.append(f'- [{t}] ({where}, trigger: `{trig}`)\n  > {sent[:200]}\n')
        shown += 1
        if shown >= 40:
            lines.append('- ... (전체는 CSV 참조)\n')
            break
    lines.append('\n## Disclaimer\n\n')
    lines.append('이 감사는 휴리스틱 플래깅이며 진실 검증이 아니다. '
                 '플래그된 문장은 사람이 원문/데이터로 확인해야 한다. '
                 '수치 대조 결과(matched_*)는 실험 산출물과의 정렬일 뿐 '
                 'verified 상태가 아니다. '
                 '이 도구는 초안을 수정하지 않으며 verified 상태를 만들지 않는다.\n')
    with open(md_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(''.join(lines))

    print(csv_path)
    print(md_path)
    if wrote_numeric_csv:
        print(numeric_csv_path)
    print(f'paragraphs={len(paras)}')
    print(f'sentences={n_sentences}')
    for t, c in sorted(by_type.items()):
        print(f'{t}={c}')
    if have_data:
        print(f"numeric_checked={numeric_summary['checked']}")
        print(f"matched_exact={numeric_summary['matched_exact']}")
        print(f"matched_rounded={numeric_summary['matched_rounded']}")
        print(f"not_found_in_data={numeric_summary['not_found_in_data']}")
    else:
        print(MISSING_DATA_LINE)
    missing_sections = [name for name, ok in structure if not ok]
    print(f'missing_sections={",".join(missing_sections) if missing_sections else "(none)"}')
    log(f'audit-manuscript-draft 실행: input={input_path}, findings={len(findings)}')
    return {
        'csv': csv_path,
        'md': md_path,
        'numeric_csv': numeric_csv_path if wrote_numeric_csv else None,
        'findings': findings,
        'numeric_rows': numeric_rows,
        'numeric_summary': numeric_summary,
        'structure': structure,
        'headings': headings,
        'notes': notes,
        'paragraphs': len(paras),
        'sentences': n_sentences,
        'have_data': have_data,
        'experiment_dir': data_dir,
    }


def cmd_audit_manuscript_draft(args):
    input_path = resolve_input_path(args.input)
    if not input_path.exists():
        raise SystemExit(f'input not found: {input_path}')
    audit_draft(input_path,
                output_prefix=getattr(args, 'output_prefix', None),
                experiment_data=getattr(args, 'experiment_data', None),
                report_dir=getattr(args, 'report_dir', None),
                config_path=getattr(args, 'config', None))


def register_subcommands(sub):
    p = sub.add_parser('audit-manuscript-draft',
                       help='Audit a draft (docx/txt/md/qmd) for evidence accountability')
    p.add_argument('--input', required=True)
    p.add_argument('--output-prefix')
    p.add_argument('--experiment-data', default=None,
                   help='dir with experiment output files to cross-check '
                        'numbers against (default: data/experiment_outputs)')
    p.add_argument('--report-dir', default=None,
                   help='where to write the MD/CSV reports '
                        '(default: <repo>/reports/draft_audit)')
    p.add_argument('--config', default=None,
                   help='path to draft_audit.yaml '
                        '(default: <repo>/config/draft_audit.yaml)')
    p.set_defaults(func=cmd_audit_manuscript_draft)
