# -*- coding: utf-8 -*-
"""Tests for scripts/paperops_draft_audit.py (audit-manuscript-draft).

Run from the repo root with:  python -m pytest tests/test_draft_audit.py
Stdlib + pytest only; no network. The module is imported via importlib from
scripts/ (no package install). All reports/logs are redirected to tmp_path so
the repo's data/, logs/ and reports/ directories are never touched.
"""
from __future__ import annotations

import argparse
import importlib.util
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / 'scripts' / 'paperops_draft_audit.py'
NO_CONFIG = '/nonexistent/draft_audit_config_for_tests.yaml'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

_MOD = None


def load_module():
    global _MOD
    if _MOD is None:
        spec = importlib.util.spec_from_file_location(
            'paperops_draft_audit_under_test', MODULE_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD


def neutral_detectors(mod):
    return mod.build_detectors(mod.load_config(NO_CONFIG))


@pytest.fixture
def env(tmp_path, monkeypatch):
    mod = load_module()
    # Never append to the repo's logs/ACTIVITY_LOG.md from tests.
    monkeypatch.setattr(mod, 'LOG', tmp_path / 'ACTIVITY_LOG.md')

    def run(draft_path, **kw):
        kw.setdefault('report_dir', str(tmp_path / 'reports'))
        kw.setdefault('experiment_data', str(tmp_path / 'no_experiment_data'))
        kw.setdefault('config_path', NO_CONFIG)
        return mod.audit_draft(Path(draft_path), **kw)

    def write_draft(name, text, encoding='utf-8'):
        p = tmp_path / name
        p.write_bytes(text.encode(encoding))
        return p

    return SimpleNamespace(mod=mod, run=run, tmp=tmp_path,
                           write_draft=write_draft)


def finding_types(result):
    return [f[1] for f in result['findings']]


# ---------------------------------------------------------------------------
# docx fixture helpers (built in-test with zipfile, stdlib only)
# ---------------------------------------------------------------------------

def w_text_p(text, style=None):
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ''
    return (f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'
            '</w:p>')


def make_docx(path, body_paras, footnotes_xml=None, endnotes_xml=None):
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           f'<w:document xmlns:w="{W}"><w:body>'
           + ''.join(body_paras) + '</w:body></w:document>')
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('word/document.xml', doc)
        if footnotes_xml:
            z.writestr('word/footnotes.xml', footnotes_xml)
        if endnotes_xml:
            z.writestr('word/endnotes.xml', endnotes_xml)
    return path


FOOTNOTES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:footnotes xmlns:w="{W}">'
    '<w:footnote w:type="separator" w:id="-1">'
    '<w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
    '<w:footnote w:id="2"><w:p><w:r>'
    '<w:t>이 각주의 방법은 완벽하다.</w:t>'
    '</w:r></w:p></w:footnote>'
    '</w:footnotes>')


# ---------------------------------------------------------------------------
# 4a. Citations count as sources (Korean, English, bracket forms)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('cite', [
    '(김철수, 2020)',
    '(김철수 외, 2020)',
    '(김철수 등, 2020)',
    '(Kim et al., 2020)',
    '(Noy, 2001)',
    '[1]',
    '[1,2]',
    '[1-3]',
])
def test_citation_counts_as_source(cite):
    mod = load_module()
    det = neutral_detectors(mod)
    sent = f'이 접근은 항상 우수한 결과를 낸다 {cite}.'
    assert mod.sentence_has_source(sent, det), cite


def test_uncited_strong_claim_is_flagged(env):
    draft = env.write_draft('a.md', '이 접근은 항상 우수한 결과를 낸다.\n')
    result = env.run(draft)
    assert 'strong_claim_no_source' in finding_types(result)


def test_cited_strong_claim_not_flagged(env):
    draft = env.write_draft(
        'b.md', '이 접근은 항상 우수한 결과를 낸다 (김철수 외, 2020).\n')
    result = env.run(draft)
    assert 'strong_claim_no_source' not in finding_types(result)


# ---------------------------------------------------------------------------
# 4b. English strong-claim and overclaim detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('sent', [
    'This proves the theorem in all cases.',
    'Our approach guarantees convergence.',
    'The method always improves accuracy.',
    'The system never fails under load.',
    'This design ensures correctness.',
    'The evaluation demonstrates conclusively the benefit.',
])
def test_english_strong_claims(sent):
    mod = load_module()
    det = neutral_detectors(mod)
    assert mod.find_strong_claim(sent, det) is not None, sent


@pytest.mark.parametrize('sent', [
    'Our system is state-of-the-art.',
    'Our system is State-of-the-Art in this domain.',
    'The model achieves SOTA performance.',
    'This is a world-first implementation.',
    'It is a best-in-class solution.',
    'The results are perfect.',
    'We propose a novel framework for governance.',
])
def test_english_overclaims(sent):
    mod = load_module()
    det = neutral_detectors(mod)
    assert mod.find_overclaim(sent, det) is not None, sent


def test_lowercase_sota_not_overclaim():
    # SOTA is matched case-sensitively so prose words are not caught.
    mod = load_module()
    det = neutral_detectors(mod)
    assert mod.find_overclaim('the sota of this village is old.', det) is None


def test_korean_detectors_still_work(env):
    draft = env.write_draft(
        'k.md',
        '본 시스템은 완벽한 해결책이다.\n\n'
        '이 구조는 성능을 극대화한다.\n')
    result = env.run(draft)
    types = finding_types(result)
    assert 'overclaim' in types
    assert 'strong_claim_no_source' in types


# ---------------------------------------------------------------------------
# 4c. Hypothetical suppression fixes
# ---------------------------------------------------------------------------

def test_damyeonjeok_is_not_hypothetical(env):
    mod = env.mod
    sent = '다면적 특성을 고려하여 정확도 95.5%를 기록했다.'
    assert not mod.is_hypothetical(sent)
    draft = env.write_draft('h1.md', sent + '\n')
    result = env.run(draft)
    assert 'numeric_needs_data' in finding_types(result)


def test_true_conditional_is_hypothetical(env):
    mod = env.mod
    sent = '만약 정확도가 99%가 된다면 상용화가 가능할 것이다.'
    assert mod.is_hypothetical(sent)
    draft = env.write_draft('h2.md', sent + '\n')
    result = env.run(draft)
    types = finding_types(result)
    assert 'numeric_hypothetical_ok' in types
    assert 'numeric_needs_data' not in types


def test_iramyeon_is_hypothetical():
    mod = load_module()
    assert mod.is_hypothetical('전문가가 아닌 학생이라면 어려울 것이다.')


def test_ramyeon_noun_is_not_hypothetical():
    # '라면' as a standalone noun (ramen) is not a conditional ending.
    mod = load_module()
    assert not mod.is_hypothetical('라면 시장은 2조 원 규모로 성장했다.')


def test_hal_su_itda_is_not_hypothetical(env):
    # Capability claims still need sources / data.
    mod = env.mod
    sent = '제안 기법은 95% 정확도를 달성할 수 있다.'
    assert not mod.is_hypothetical(sent)
    draft = env.write_draft('h3.md', sent + '\n')
    result = env.run(draft)
    assert 'numeric_needs_data' in finding_types(result)


# ---------------------------------------------------------------------------
# 4d. Numeric extraction: integers with units, year/list-index exclusions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('sent,expected_raw', [
    ('정확도는 95%로 향상되었다.', '95%'),
    ('데이터 1000개를 수집했다.', '1000개'),
    ('오류 건수는 12건이었다.', '12건'),
    ('속도가 3배 개선되었다.', '3배'),
    ('평가에서 10점을 받았다.', '10점'),
    ('처리량이 2x 증가했다.', '2x'),
    ('throughput improved 5 times overall.', '5times'),
    ('전체 1,234개를 분석했다.', '1,234개'),
    ('F1 = 0.87', '0.87'),
    ('정확도는 95.5%로 나타났다.', '95.5%'),
])
def test_numeric_tokens_extracted(sent, expected_raw):
    mod = load_module()
    raws = [t['raw'].replace(' ', '') for t in mod.extract_numeric_tokens(sent)]
    assert expected_raw in raws, (sent, raws)


@pytest.mark.parametrize('sent', [
    '본 연구는 2020년에 시작되었다.',           # standalone year
    '1995 이후의 문헌을 검토했다.',             # bare year, no unit
    '1. 서론',                               # list index / numbering
    '1.2 연구 방법',                          # leading section number
    '그림 2.1 참조',                          # figure reference
    '표 4.2에서 제시한다.',                    # table reference
    '3.2절에서 상세히 설명한다.',               # section reference
    'Python v3.10 환경에서 실행했다.',          # version number
])
def test_numeric_non_results_excluded(sent):
    mod = load_module()
    assert mod.extract_numeric_tokens(sent) == [], sent


def test_integer_percent_flagged_in_audit(env):
    draft = env.write_draft('n1.md', '정확도는 95%로 향상되었다.\n')
    result = env.run(draft)
    assert 'numeric_needs_data' in finding_types(result)


def test_integer_count_flagged_in_audit(env):
    draft = env.write_draft('n2.md', '데이터 1000개를 수집했다.\n')
    result = env.run(draft)
    assert 'numeric_needs_data' in finding_types(result)


# ---------------------------------------------------------------------------
# 4e. Structure check is heading-anchored
# ---------------------------------------------------------------------------

def test_structure_keyword_in_body_prose_does_not_count(env):
    draft = env.write_draft(
        's1.md',
        '서론에서 논의한 바와 같이 연구의 배경을 상세히 설명하며 '
        '결론 부분의 시사점도 함께 검토한다.\n')
    result = env.run(draft)
    structure = dict(result['structure'])
    assert structure['서론'] is False
    assert structure['결론'] is False


def test_structure_md_heading_counts(env):
    draft = env.write_draft(
        's2.md',
        '# 서론\n\n연구 배경을 설명한다.\n\n# 결론\n\n요약한다.\n')
    result = env.run(draft)
    structure = dict(result['structure'])
    assert structure['서론'] is True
    assert structure['결론'] is True


def test_structure_korean_chapter_line_fallback(env):
    # md without '#' headings falls back to short 제N장-style lines.
    draft = env.write_draft(
        's3.md', '제1장 서론\n\n연구 배경을 설명한다.\n')
    result = env.run(draft)
    structure = dict(result['structure'])
    assert structure['서론'] is True


def test_structure_docx_heading_style(env):
    path = env.tmp / 's4.docx'
    make_docx(path, [
        w_text_p('서론', style='Heading1'),
        w_text_p('여기 본문에는 결론이라는 단어가 들어 있지만 이것은 장 제목이 아니라 긴 본문 문장이다.'),
    ])
    result = env.run(path)
    structure = dict(result['structure'])
    assert structure['서론'] is True
    assert structure['결론'] is False


# ---------------------------------------------------------------------------
# 4f. Minimum sentence length dropped to 4 chars
# ---------------------------------------------------------------------------

def test_short_overclaim_sentence_is_seen(env):
    draft = env.write_draft('short.md', '완벽하다.\n')
    result = env.run(draft)
    assert 'overclaim' in finding_types(result)
    assert result['sentences'] >= 1


# ---------------------------------------------------------------------------
# 3. docx parsing: tabs/breaks, footnotes, footnote refs, BadZipFile
# ---------------------------------------------------------------------------

def test_docx_tab_and_br_become_spaces(env):
    path = env.tmp / 'tab.docx'
    tab_para = (f'<w:p><w:r><w:t>12</w:t></w:r><w:r><w:tab/></w:r>'
                '<w:r><w:t>34</w:t></w:r></w:p>')
    br_para = ('<w:p><w:r><w:t>56</w:t></w:r><w:r><w:br/></w:r>'
               '<w:r><w:t>78</w:t></w:r></w:p>')
    make_docx(path, [tab_para, br_para])
    paras, _headings, _notes = env.mod.load_draft(path)
    texts = [p['text'] for p in paras]
    assert '12 34' in texts
    assert '56 78' in texts
    assert not any('1234' in t or '5678' in t for t in texts)


def test_docx_footnote_text_is_scanned_with_location(env):
    path = env.tmp / 'foot.docx'
    make_docx(path, [w_text_p('본문 단락이다. 여기에는 문제가 없다.')],
              footnotes_xml=FOOTNOTES_XML)
    result = env.run(path)
    over = [f for f in result['findings'] if f[1] == 'overclaim']
    assert over, result['findings']
    assert any(f[4] == 'footnote:2' for f in over)


def test_docx_footnote_reference_counts_as_source(env):
    path = env.tmp / 'ref.docx'
    with_ref = ('<w:p><w:r><w:t>본 시스템의 성능은 탁월하다.</w:t></w:r>'
                '<w:r><w:footnoteReference w:id="2"/></w:r></w:p>')
    without_ref = w_text_p('이 구조의 성능은 월등하다.')
    make_docx(path, [with_ref, without_ref], footnotes_xml=FOOTNOTES_XML)
    result = env.run(path)
    flagged = [f[3] for f in result['findings']
               if f[1] == 'strong_claim_no_source']
    assert not any('탁월하다' in s for s in flagged)
    assert any('월등하다' in s for s in flagged)


def test_docx_bad_zip_raises_clean_systemexit(env):
    path = env.tmp / 'broken.docx'
    path.write_bytes(b'this is definitely not a zip archive')
    with pytest.raises(SystemExit) as exc:
        env.mod.load_draft(path)
    msg = str(exc.value)
    assert 'zip' in msg.lower()
    assert 'broken.docx' in msg


# ---------------------------------------------------------------------------
# 2. Encoding safety
# ---------------------------------------------------------------------------

def test_cp949_draft_is_read_correctly(env):
    draft = env.write_draft(
        'cp949.md', '# 서론\n\n본 시스템은 완벽하다.\n', encoding='cp949')
    result = env.run(draft)
    over = [f for f in result['findings'] if f[1] == 'overclaim']
    assert over
    assert '완벽하다' in over[0][3]  # Korean survived the decode
    assert any('cp949' in n for n in result['notes'])


def test_undecodable_draft_warns_prominently(env):
    p = env.tmp / 'garbage.md'
    p.write_bytes(b'\xff\xff\xff broken bytes but 95% still visible.\n')
    result = env.run(p)
    md_text = result['md'].read_text(encoding='utf-8')
    assert 'WARNING' in md_text
    assert 'garbage.md' in md_text
    assert 'replace' in md_text
    warnings = [n for n in result['notes'] if n.startswith('WARNING')]
    assert warnings


def test_cp949_experiment_file_is_indexed(env):
    exp = env.tmp / 'exp_kr'
    exp.mkdir()
    (exp / 'kor.csv').write_bytes('항목,값\n정확도,95.5\n'.encode('cp949'))
    draft = env.write_draft('enc.md', '정확도는 95.5%로 나타났다.\n')
    result = env.run(draft, experiment_data=str(exp))
    assert result['numeric_summary']['matched_exact'] == 1


def test_csv_reports_are_utf8_sig(env):
    exp = env.tmp / 'exp_bom'
    exp.mkdir()
    (exp / 'r.csv').write_text('95.5\n', encoding='utf-8')
    draft = env.write_draft(
        'bom.md', '정확도는 95.5%이고 완벽하다.\n')
    result = env.run(draft, experiment_data=str(exp))
    assert result['csv'].read_bytes().startswith(b'\xef\xbb\xbf')
    assert result['numeric_csv'].read_bytes().startswith(b'\xef\xbb\xbf')


# ---------------------------------------------------------------------------
# 1. Numeric cross-check against experiment outputs
# ---------------------------------------------------------------------------

@pytest.fixture
def crosscheck(env):
    exp = env.tmp / 'experiment_outputs'
    exp.mkdir()
    (exp / 'results.csv').write_text(
        'metric,value\naccuracy,95.5\ncount,1000\nf1,0.874\n',
        encoding='utf-8')
    draft = env.write_draft(
        'cc.md',
        '# 결과\n\n'
        '정확도는 95.5%로 나타났다.\n\n'
        '전체 데이터는 1,000개였다.\n\n'
        'F1 = 0.87 수준이었다.\n\n'
        '오류율은 77.7%로 관찰되었다.\n')
    result = env.run(draft, experiment_data=str(exp))
    return SimpleNamespace(env=env, result=result)


def test_crosscheck_statuses(crosscheck):
    rows = {r[0]: r[2] for r in crosscheck.result['numeric_rows']}
    assert rows['95.5%'] == 'matched_exact'
    assert rows['1,000개'] == 'matched_exact'
    assert rows['0.87'] == 'matched_rounded'
    assert rows['77.7%'] == 'not_found_in_data'


def test_crosscheck_summary_and_report(crosscheck):
    summary = crosscheck.result['numeric_summary']
    assert summary == {'checked': 4, 'matched_exact': 2,
                       'matched_rounded': 1, 'not_found_in_data': 1}
    md_text = crosscheck.result['md'].read_text(encoding='utf-8')
    assert 'numeric_checked: 4' in md_text
    assert 'matched_exact: 2' in md_text
    assert 'matched_rounded: 1' in md_text
    assert 'not_found_in_data: 1' in md_text
    assert 'alignment' in md_text  # governance: alignment, not truth


def test_crosscheck_matching_file_recorded(crosscheck):
    matched = [r for r in crosscheck.result['numeric_rows']
               if r[2] == 'matched_exact']
    assert all(r[3] == 'results.csv' for r in matched)
    missing = [r for r in crosscheck.result['numeric_rows']
               if r[2] == 'not_found_in_data']
    assert all(r[3] == '' for r in missing)


def test_crosscheck_never_marks_verified(crosscheck):
    numeric_csv = crosscheck.result['numeric_csv'].read_text(
        encoding='utf-8-sig')
    assert 'verified' not in numeric_csv.lower()
    statuses = {r[2] for r in crosscheck.result['numeric_rows']}
    assert statuses <= {'matched_exact', 'matched_rounded',
                        'not_found_in_data'}


def test_crosscheck_missing_number_becomes_finding(crosscheck):
    types = finding_types(crosscheck.result)
    assert 'numeric_not_found_in_data' in types
    assert 'numeric_needs_data' not in types  # data present


def test_rounded_match_uses_draft_precision(env):
    exp = env.tmp / 'exp_round'
    exp.mkdir()
    (exp / 'nums.txt').write_text('999.6\n', encoding='utf-8')
    draft = env.write_draft('round.md', '총 1000개를 처리했다.\n')
    result = env.run(draft, experiment_data=str(exp))
    rows = result['numeric_rows']
    assert len(rows) == 1
    assert rows[0][2] == 'matched_rounded'


def test_experiment_dir_absent_falls_back_to_flags(env):
    draft = env.write_draft('nodata.md', '정확도는 95.5%로 나타났다.\n')
    result = env.run(draft, experiment_data=str(env.tmp / 'does_not_exist'))
    assert 'numeric_needs_data' in finding_types(result)
    md_text = result['md'].read_text(encoding='utf-8')
    assert ('experiment data dir not found — '
            'numeric values flagged for manual check') in md_text
    assert result['numeric_csv'] is None


def test_experiment_dir_empty_falls_back_to_flags(env):
    empty = env.tmp / 'empty_exp'
    empty.mkdir()
    draft = env.write_draft('empty.md', '정확도는 95.5%로 나타났다.\n')
    result = env.run(draft, experiment_data=str(empty))
    assert 'numeric_needs_data' in finding_types(result)
    assert not result['have_data']


# ---------------------------------------------------------------------------
# 5. Config externalization
# ---------------------------------------------------------------------------

def test_missing_config_uses_neutral_defaults():
    mod = load_module()
    cfg = mod.load_config(NO_CONFIG)
    assert cfg['source_mentions'] == []
    names = [ch['name'] for ch in cfg['required_chapters']]
    assert '서론' in names and '결론' in names


def test_custom_config_overrides(env):
    pytest.importorskip('yaml')
    cfg_path = env.tmp / 'my_audit.yaml'
    cfg_path.write_text(
        "source_mentions: ['MySpecialCorpus']\n"
        'required_chapters:\n'
        '  - name: 고유챕터\n'
        "    patterns: ['고유\\s*챕터']\n"
        "overclaim_extra: ['초울트라']\n"
        "strong_claim_extra: ['궁극의 해법']\n",
        encoding='utf-8')
    draft = env.write_draft(
        'cfg.md',
        '# 고유챕터\n\n'
        'MySpecialCorpus 기반으로 항상 우수한 결과를 낸다.\n\n'
        '이 방식은 초울트라 성능을 보인다.\n\n'
        '이것이 궁극의 해법 이라 주장한다.\n')
    result = env.run(draft, config_path=str(cfg_path))
    structure = dict(result['structure'])
    assert structure == {'고유챕터': True}
    types = finding_types(result)
    assert 'overclaim' in types              # via overclaim_extra
    assert 'strong_claim_no_source' in types  # via strong_claim_extra
    flagged = [f[3] for f in result['findings']
               if f[1] == 'strong_claim_no_source']
    # the whitelisted source suppressed the '항상' strong claim
    assert not any('MySpecialCorpus' in s for s in flagged)


def test_shipped_config_preserves_author_values():
    pytest.importorskip('yaml')
    mod = load_module()
    cfg = mod.load_config(REPO / 'config' / 'draft_audit.yaml')
    assert 'FEEKG' in cfg['source_mentions']
    names = [ch['name'] for ch in cfg['required_chapters']]
    assert '아티팩트/온톨로지 설계' in names
    assert len(names) == 7
    det = mod.build_detectors(cfg)
    assert mod.sentence_has_source('FEEKG 접근을 따라 항상 우수하다.', det)
    assert mod.find_overclaim('본 시스템은 hallucination을 제거한다.', det)


# ---------------------------------------------------------------------------
# 6. Relative --input resolves against the caller's CWD
# ---------------------------------------------------------------------------

def test_relative_input_resolves_against_cwd(env, monkeypatch):
    monkeypatch.chdir(env.tmp)
    env.write_draft('mydraft.md', '# 서론\n\n배경을 설명한다.\n')
    args = argparse.Namespace(
        input='mydraft.md', output_prefix='relcheck',
        experiment_data=str(env.tmp / 'no_data'),
        report_dir=str(env.tmp / 'rep'), config=NO_CONFIG)
    env.mod.cmd_audit_manuscript_draft(args)
    assert (env.tmp / 'rep' / 'relcheck.md').exists()
    assert (env.tmp / 'rep' / 'relcheck.csv').exists()


def test_absolute_input_still_works(env):
    draft = env.write_draft('abs.md', '# 서론\n\n배경을 설명한다.\n')
    result = env.run(draft.resolve())
    assert result['md'].exists()


def test_missing_input_raises_systemexit(env, monkeypatch):
    monkeypatch.chdir(env.tmp)
    args = argparse.Namespace(
        input='never_there.md', output_prefix=None,
        experiment_data=None, report_dir=str(env.tmp / 'rep'),
        config=NO_CONFIG)
    with pytest.raises(SystemExit) as exc:
        env.mod.cmd_audit_manuscript_draft(args)
    assert 'input not found' in str(exc.value)


# ---------------------------------------------------------------------------
# CLI integration: register_subcommands keeps command name and flags
# ---------------------------------------------------------------------------

def test_register_subcommands_end_to_end(env, monkeypatch):
    monkeypatch.chdir(env.tmp)
    exp = env.tmp / 'exp_cli'
    exp.mkdir()
    (exp / 'out.csv').write_text('95.5\n', encoding='utf-8')
    draft = env.write_draft(
        'cli.md', '# 서론\n\n정확도는 95.5%로 나타났다.\n')
    before = draft.read_bytes()

    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd')
    env.mod.register_subcommands(sub)
    args = ap.parse_args([
        'audit-manuscript-draft',
        '--input', 'cli.md',
        '--output-prefix', 'e2e',
        '--experiment-data', str(exp),
        '--report-dir', str(env.tmp / 'cli_reports'),
        '--config', NO_CONFIG,
    ])
    args.func(args)

    out = env.tmp / 'cli_reports'
    assert (out / 'e2e.md').exists()
    assert (out / 'e2e.csv').exists()
    assert (out / 'e2e_numeric.csv').exists()
    # the audit never edits the draft
    assert draft.read_bytes() == before


def test_legacy_flags_still_parse(env):
    # pre-v0.2 invocation shape must keep working (parse only)
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd')
    env.mod.register_subcommands(sub)
    args = ap.parse_args(['audit-manuscript-draft',
                          '--input', 'x.docx', '--output-prefix', 'p'])
    assert args.input == 'x.docx'
    assert args.output_prefix == 'p'
    assert args.func is env.mod.cmd_audit_manuscript_draft


def test_audit_never_modifies_docx_draft(env):
    path = env.tmp / 'keep.docx'
    make_docx(path, [w_text_p('본 시스템은 완벽하다.')],
              footnotes_xml=FOOTNOTES_XML)
    before = path.read_bytes()
    env.run(path)
    assert path.read_bytes() == before
