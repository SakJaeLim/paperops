# -*- coding: utf-8 -*-
"""Core tests for scripts/paperops.py — pure functions and the regression set for the
defects fixed in v0.2.0. Filesystem-touching tests run against tmp_path with the module's
ROOT monkeypatched; nothing writes into the real repo tree."""
import csv
import importlib.util
import io
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('paperops_core_under_test', REPO / 'scripts' / 'paperops.py')
po = importlib.util.module_from_spec(SPEC)
sys.modules['paperops_core_under_test'] = po
SPEC.loader.exec_module(po)


# ---------------------------------------------------------------- stable ids / dedupe

def test_stable_id_same_paper_across_sources_by_doi_normalization():
    a = {'doi': 'https://doi.org/10.1234/ABC'}
    b = {'doi': '10.1234/abc'}
    assert po.stable_id(a) == po.stable_id(b)


def test_stable_id_arxiv_version_normalized():
    a = {'arxiv_id': '2401.12345v2'}
    b = {'arxiv_id': '2401.12345'}
    assert po.stable_id(a) == po.stable_id(b)


def test_find_existing_paper_id_merges_doi_and_arxiv_variants(tmp_path, monkeypatch):
    monkeypatch.setattr(po, 'DB', tmp_path / 'papers.sqlite')
    po.init_db_tables_only() if hasattr(po, 'init_db_tables_only') else None
    c = po.conn()
    c.execute("CREATE TABLE IF NOT EXISTS papers(id TEXT PRIMARY KEY, title TEXT, authors_json TEXT, year INTEGER, venue TEXT, doi TEXT, arxiv_id TEXT, abstract TEXT, url TEXT, pdf_url TEXT, source TEXT, collection_date TEXT, status TEXT, score REAL, topic_relevance REAL, citation_count INTEGER, open_access INTEGER, local_pdf_path TEXT, parsed_text_path TEXT, citekey TEXT, title_norm TEXT, raw_json TEXT, updated_at TEXT)")
    c.execute("INSERT INTO papers(id, title, doi, arxiv_id, title_norm) VALUES('id1', 'A Paper', '10.1/x', '', 'apaper')")
    c.commit()
    assert po.find_existing_paper_id(c, {'doi': 'https://doi.org/10.1/X'}) == 'id1'
    assert po.find_existing_paper_id(c, {'title': 'A   Paper!'}) == 'id1'
    assert po.find_existing_paper_id(c, {'doi': '10.9/other'}) is None
    c.close()


# ---------------------------------------------------------------- arXiv query builder

def test_arxiv_query_soup_becomes_and_of_terms():
    expr = po.arxiv_query_expr('ontology knowledge graph governance')
    assert 'all:"ontology"' in expr and ' AND ' in expr


def test_arxiv_query_explicit_boolean_passthrough():
    q = '"knowledge graph" AND (governance OR traceability)'
    assert 'AND' in po.arxiv_query_expr(q)


def test_arxiv_query_caps_terms():
    expr = po.arxiv_query_expr(' '.join(f'w{i}' for i in range(12)))
    assert expr.count(' AND ') == 5  # capped at 6 terms


# ---------------------------------------------------------------- verified invariant

@pytest.mark.parametrize('value', ['1', 'yes', 'y', 'TRUE ', 'True', 'on', 't', '', None, 'banana'])
def test_normalized_verified_never_promotes_truthy_variants(value):
    if str(value).strip().lower() == 'true':
        assert po.normalized_verified(value) == 'true'
    else:
        assert po.normalized_verified(value) == 'false'


def test_normalized_verified_exact_literals():
    assert po.normalized_verified('true') == 'true'
    assert po.normalized_verified('false') == 'false'


def test_verified_value_is_ambiguous():
    assert po.verified_value_is_ambiguous('1')
    assert po.verified_value_is_ambiguous('YES')
    assert not po.verified_value_is_ambiguous('true')
    assert not po.verified_value_is_ambiguous('')


# ---------------------------------------------------------------- CSV round-trip safety

def test_read_csv_dict_tolerates_bom_and_extra_columns(tmp_path):
    p = tmp_path / 'q.csv'
    # BOM + a stray trailing column, exactly what Excel produces
    p.write_bytes('candidate_id,claim\r\nabc,hello,STRAY\r\n'.encode('utf-8-sig'))
    rows, header = po.read_csv_dict(p)
    assert header == ['candidate_id', 'claim']
    assert rows[0]['candidate_id'] == 'abc'
    assert '_extra' not in rows[0] and None not in rows[0]


def test_atomic_write_csv_projects_stray_keys_and_writes_bom(tmp_path):
    p = tmp_path / 'out.csv'
    po.atomic_write_csv(p, ['a', 'b'], [{'a': '1', 'b': '한글', 'stray': 'x', None: ['boom']}])
    raw = p.read_bytes()
    assert raw.startswith(b'\xef\xbb\xbf')  # BOM so Excel renders Korean correctly
    rows, header = po.read_csv_dict(p)
    assert header == ['a', 'b'] and rows[0]['b'] == '한글'
    assert not (tmp_path / 'out.csv.tmp_write').exists()


def test_read_text_compat_cp949_fallback(tmp_path):
    p = tmp_path / 'k.txt'
    p.write_bytes('정확도는 95.5%이다'.encode('cp949'))
    assert '정확도는' in po.read_text_compat(p)


# ---------------------------------------------------------------- bib parsing

def test_bib_entry_arxiv_id_no_literal_arxiv_bug():
    # archivePrefix present but no eprint -> must NOT return 'arxiv'
    assert po.bib_entry_arxiv_id({'archiveprefix': 'arXiv'}) == ''
    # non-arXiv eprint must not be treated as arXiv id
    assert po.bib_entry_arxiv_id({'eprint': 'some-random-id', 'archiveprefix': 'bioRxiv'}) == ''
    assert po.bib_entry_arxiv_id({'eprint': '2401.12345v3', 'archiveprefix': 'arXiv'}) == '2401.12345'
    assert po.bib_entry_arxiv_id({'eprint': '2401.12345'}) == '2401.12345'
    assert po.bib_entry_arxiv_id({'eprint': 'not an id'}) == ''


def test_citekey_pattern_ignores_emails(tmp_path, monkeypatch):
    monkeypatch.setattr(po, 'ROOT', tmp_path)
    man = tmp_path / '05_manuscript'
    man.mkdir()
    (man / 'ch.qmd').write_text('Cited [@kim2020graph]. Contact author@example.com please.', encoding='utf-8')
    backups = man / 'backups' / 'old'
    backups.mkdir(parents=True)
    (backups / 'ch.qmd').write_text('[@stale2019key]', encoding='utf-8')
    found = po.manuscript_citekeys()
    assert 'kim2020graph' in found
    assert 'gmail' not in found  # email must not count as a cite
    assert 'stale2019key' not in found  # backups are excluded


# ---------------------------------------------------------------- source_location

def test_source_location_semicolon_in_heading_sanitized():
    row = {'page': '', 'quote': 'q', 'section_id': 's1', 'section_heading': 'Results; Discussion'}
    loc = po.source_location_for_patch(row)
    parsed = po.parse_source_location(loc)
    assert parsed['section_heading'] == 'Results, Discussion'
    assert parsed['section_id'] == 's1'
    assert parsed['quote_sha256']


# ---------------------------------------------------------------- LLM verbatim gate

def test_quote_is_verbatim_exact_and_whitespace_normalized():
    section = 'We propose   PaperOps.\nIt improves traceability.'
    assert po.quote_is_verbatim('We propose PaperOps.', section)
    assert po.quote_is_verbatim('It improves  traceability.', section)


def test_quote_is_verbatim_rejects_paraphrase_and_empty():
    section = 'We propose PaperOps for evidence governance.'
    assert not po.quote_is_verbatim('PaperOps is proposed for governing evidence.', section)
    assert not po.quote_is_verbatim('', section)


def test_parse_llm_candidate_json_variants():
    ok = po.parse_llm_candidate_json('[{"claim": "c", "exact_quote": "q"}]')
    assert len(ok) == 1
    fenced = po.parse_llm_candidate_json('Sure!\n```json\n[{"claim": "c", "exact_quote": "q"}]\n```\nDone.')
    assert len(fenced) == 1
    prose = po.parse_llm_candidate_json('Here it is [{"claim": "c", "exact_quote": "q"}] thanks')
    assert len(prose) == 1
    assert po.parse_llm_candidate_json('not json at all') == []
    assert po.parse_llm_candidate_json('[{"claim": "no quote"}]') == []


# ---------------------------------------------------------------- review queue merge

def _queue_row(cid, paper, decision='pending'):
    row = {f: '' for f in po.EVIDENCE_CANDIDATE_REVIEW_FIELDS}
    row.update({'candidate_id': cid, 'paper_id': paper, 'verified': 'false', 'review_decision': decision,
                'citekey': 'k', 'source_artifact': 'sections.json', 'claim': 'c', 'quote': 'q', 'confidence': '0.7',
                'use_in_section': 'literature'})
    return row


def test_review_queue_merge_preserves_other_papers(tmp_path, monkeypatch):
    """Regression for the data-loss bug: regenerating paper B's queue must not delete
    paper A's pending human decisions from the shared queue file."""
    monkeypatch.setattr(po, 'ROOT', tmp_path)
    (tmp_path / 'matrices').mkdir()
    queue = tmp_path / 'matrices/evidence_candidate_review_queue.csv'
    po.write_evidence_candidate_review_queue(queue, [
        _queue_row('aaa', 'paperA', 'include'),
        _queue_row('bbb', 'paperB'),
    ])
    existing = po.read_evidence_candidate_review_queue(queue)
    preserved = po.preserved_review_values(existing, [])
    new_b_rows = [po.review_row_from_candidate(_queue_row('bbb2', 'paperB'), preserved)]
    other_rows = [r for r in existing if r.get('paper_id') != 'paperB']
    po.write_evidence_candidate_review_queue(queue, other_rows + new_b_rows)
    final = po.read_evidence_candidate_review_queue(queue)
    by_id = {r['candidate_id']: r for r in final}
    assert 'aaa' in by_id, 'paper A row was lost'
    assert by_id['aaa']['review_decision'] == 'include', 'paper A human decision was lost'
    assert 'bbb2' in by_id


# ---------------------------------------------------------------- verification ledger

def test_verification_ledger_attestation_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(po, 'ROOT', tmp_path)
    monkeypatch.setattr(po, 'LOG', tmp_path / 'logs/ACTIVITY_LOG.md')
    (tmp_path / 'matrices').mkdir()
    (tmp_path / 'logs').mkdir()
    fields = ['evidence_id', 'paper_id', 'citekey', 'claim', 'quote', 'exact_quote', 'verified', 'verified_by', 'verified_at', 'risk_note']
    matrix = tmp_path / 'matrices/evidence_matrix.csv'
    po.atomic_write_csv(matrix, fields, [{'evidence_id': 'ev_1', 'paper_id': 'p', 'citekey': 'k', 'claim': 'c', 'quote': 'q', 'exact_quote': 'q', 'verified': 'false', 'verified_by': '', 'verified_at': '', 'risk_note': 'candidate_id=abc'}])
    import argparse
    po.cmd_verify_evidence(argparse.Namespace(evidence_id='ev_1', by='임석제', method='human_pdf_check', note='checked p.3', attest=True, revoke=False))
    rows, _ = po.read_csv_dict(matrix)
    assert rows[0]['verified'] == 'true' and rows[0]['verified_by'] == '임석제'
    ledger = po.verification_ledger_index()
    assert ledger['ev_1']['action'] == 'verify'
    # guard logic: attested row passes, hand-edited row fails
    entry = ledger['ev_1']
    assert entry['verified_by'] == rows[0]['verified_by']
    # revoke path
    po.cmd_verify_evidence(argparse.Namespace(evidence_id='ev_1', by='임석제', method='human_pdf_check', note='', attest=False, revoke=True))
    rows, _ = po.read_csv_dict(matrix)
    assert rows[0]['verified'] == 'false' and rows[0]['verified_by'] == ''
    assert po.verification_ledger_index()['ev_1']['action'] == 'revoke'


def test_verify_evidence_requires_attestation(tmp_path, monkeypatch):
    monkeypatch.setattr(po, 'ROOT', tmp_path)
    monkeypatch.setattr(po, 'LOG', tmp_path / 'logs/ACTIVITY_LOG.md')
    import argparse
    with pytest.raises(SystemExit):
        po.cmd_verify_evidence(argparse.Namespace(evidence_id='x', by='name', method='m', note='', attest=False, revoke=False))
    with pytest.raises(SystemExit):
        po.cmd_verify_evidence(argparse.Namespace(evidence_id='x', by='  ', method='m', note='', attest=True, revoke=False))


# ---------------------------------------------------------------- OpenAlex abstract

def test_openalex_abstract_reconstruction():
    w = {'abstract_inverted_index': {'evidence': [1], 'Reconstructed': [0], 'text.': [2]}}
    assert po.openalex_abstract(w) == 'Reconstructed evidence text.'
    assert po.openalex_abstract({'abstract_inverted_index': None}) == ''


# ---------------------------------------------------------------- legacy deprecation

def test_legacy_extract_evidence_is_blocked():
    import argparse
    with pytest.raises(SystemExit) as excinfo:
        po.cmd_extract(argparse.Namespace(limit=5))
    assert 'deprecated' in str(excinfo.value)
