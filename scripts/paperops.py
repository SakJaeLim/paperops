# -*- coding: utf-8 -*-
"""PaperOps MVP CLI: collect, score, digest, pdf, parse, cards, evidence, outline, audit."""
from __future__ import annotations
import argparse, csv, difflib, hashlib, json, math, os, re, sqlite3, sys, time, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'data/metadata/papers.sqlite'
LOG = ROOT / 'logs/ACTIVITY_LOG.md'


def now(): return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
def today(): return datetime.now().strftime('%Y-%m-%d')

def log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if not LOG.exists(): LOG.write_text('# 논문AGENT 활동 로그\n\n', encoding='utf-8')
    with LOG.open('a', encoding='utf-8') as f: f.write(f'- [{now()}] {msg}\n')


def load_yaml(path):
    try:
        import yaml
        return yaml.safe_load(Path(path).read_text(encoding='utf-8')) or {}
    except Exception:
        return {}


def slug(s, n=80):
    s = re.sub(r'[^A-Za-z0-9가-힣]+', '_', s or '').strip('_').lower()
    return s[:n] or 'untitled'


def norm_title(s): return re.sub(r'\W+', '', (s or '').lower())
def stable_id(p):
    if p.get('doi'): key='doi:'+p['doi'].lower()
    elif p.get('arxiv_id'): key='arxiv:'+p['arxiv_id'].lower()
    else: key='title:'+norm_title(p.get('title',''))
    return hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]


def citekey(p):
    year = str(p.get('year') or 'nd')
    first = 'paper'
    authors = p.get('authors') or []
    if authors: first = re.split(r'\s+', authors[0].replace(',', ' '))[0].lower()
    word = re.findall(r'[A-Za-z]{4,}', p.get('title',''))
    tail = word[0].lower() if word else 'study'
    return slug(f'{first}{year}{tail}', 40)


def conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    c = conn()
    c.execute('''CREATE TABLE IF NOT EXISTS papers(
        id TEXT PRIMARY KEY, title TEXT, authors_json TEXT, year INTEGER, venue TEXT,
        doi TEXT, arxiv_id TEXT, abstract TEXT, url TEXT, pdf_url TEXT, source TEXT,
        collection_date TEXT, status TEXT DEFAULT 'new', score REAL DEFAULT 0,
        topic_relevance REAL DEFAULT 0, citation_count INTEGER DEFAULT 0,
        open_access INTEGER DEFAULT 0, local_pdf_path TEXT, parsed_text_path TEXT,
        citekey TEXT, title_norm TEXT, raw_json TEXT, updated_at TEXT
    )''')
    c.commit(); c.close()
    for d in ['data/incoming','data/pdfs','data/parsed','notes/papers','reports/daily_digest','reports/survey_reports','reports/audit_reports','matrices']:
        (ROOT/d).mkdir(parents=True, exist_ok=True)
    ev = ROOT/'matrices/evidence_matrix.csv'
    if not ev.exists():
        ev.write_text('paper_id,citekey,claim_type,claim,quote,page,section,confidence,use_in_section,my_comment,verified,source_file,created_at,updated_at\n', encoding='utf-8')
    log('init 실행: DB/폴더/Evidence Matrix 확인 완료')


def upsert(p):
    p['id'] = p.get('id') or stable_id(p); p['citekey'] = p.get('citekey') or citekey(p)
    c = conn()
    c.execute('''INSERT INTO papers(id,title,authors_json,year,venue,doi,arxiv_id,abstract,url,pdf_url,source,collection_date,status,score,topic_relevance,citation_count,open_access,local_pdf_path,parsed_text_path,citekey,title_norm,raw_json,updated_at)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(id) DO UPDATE SET title=excluded.title, authors_json=excluded.authors_json, year=excluded.year, venue=excluded.venue,
    abstract=excluded.abstract, url=COALESCE(excluded.url,papers.url), pdf_url=COALESCE(excluded.pdf_url,papers.pdf_url), citation_count=MAX(papers.citation_count, excluded.citation_count), open_access=MAX(papers.open_access, excluded.open_access), raw_json=excluded.raw_json, updated_at=excluded.updated_at''',
    (p['id'],p.get('title'),json.dumps(p.get('authors') or [],ensure_ascii=False),p.get('year'),p.get('venue'),p.get('doi'),p.get('arxiv_id'),p.get('abstract'),p.get('url'),p.get('pdf_url'),p.get('source'),p.get('collection_date') or now(),'new',p.get('score',0),p.get('topic_relevance',0),p.get('citation_count',0),1 if p.get('open_access') else 0,p.get('local_pdf_path'),p.get('parsed_text_path'),p['citekey'],norm_title(p.get('title','')),json.dumps(p,ensure_ascii=False),now()))
    c.commit(); c.close(); return p['id']


def queries():
    prof = load_yaml(ROOT/'config/topic_profile.yaml')
    qs = [x.get('query') for x in prof.get('query_groups',[]) if isinstance(x,dict) and x.get('query')]
    return qs or ['ontology knowledge graph semantic web', 'automated literature review research assistant']


def fetch_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {'User-Agent':'PaperOps/0.1'})
    with urllib.request.urlopen(req, timeout=30) as r: return json.loads(r.read().decode('utf-8','ignore'))


def collect_arxiv(q, limit):
    url='https://export.arxiv.org/api/query?search_query=all:'+urllib.parse.quote(q)+'&start=0&max_results='+str(limit)+'&sortBy=submittedDate&sortOrder=descending'
    data=urllib.request.urlopen(url, timeout=30).read()
    root=ET.fromstring(data); ns={'a':'http://www.w3.org/2005/Atom'}; out=[]
    for e in root.findall('a:entry',ns):
        title=' '.join((e.findtext('a:title','',ns) or '').split())
        aid=(e.findtext('a:id','',ns) or '').rsplit('/',1)[-1]
        pdf=None
        for l in e.findall('a:link',ns):
            if l.attrib.get('title')=='pdf': pdf=l.attrib.get('href')
        out.append(dict(title=title, authors=[a.findtext('a:name','',ns) for a in e.findall('a:author',ns)], year=int((e.findtext('a:published','0000',ns) or '0000')[:4] or 0), venue='arXiv', doi=None, arxiv_id=aid, abstract=' '.join((e.findtext('a:summary','',ns) or '').split()), url=e.findtext('a:id','',ns), pdf_url=pdf, source='arxiv', open_access=True))
    return out


def collect_openalex(q, limit):
    url='https://api.openalex.org/works?search='+urllib.parse.quote(q)+'&per-page='+str(limit)
    js=fetch_json(url); out=[]
    for w in js.get('results',[]):
        authors=[a.get('author',{}).get('display_name','') for a in w.get('authorships',[])]
        loc=w.get('primary_location') or {}; src=loc.get('source') or {}; oa=w.get('open_access') or {}
        out.append(dict(title=w.get('title'), authors=authors, year=w.get('publication_year'), venue=src.get('display_name'), doi=(w.get('doi') or '').replace('https://doi.org/',''), arxiv_id=None, abstract='', url=w.get('id'), pdf_url=loc.get('pdf_url') or oa.get('oa_url'), source='openalex', citation_count=w.get('cited_by_count') or 0, open_access=bool(oa.get('is_oa'))))
    return out


def collect_crossref(q, limit):
    url='https://api.crossref.org/works?query='+urllib.parse.quote(q)+'&rows='+str(limit)
    js=fetch_json(url); out=[]
    for w in js.get('message',{}).get('items',[]):
        title=(w.get('title') or [''])[0]
        authors=[(' '.join([a.get('given',''),a.get('family','')]).strip()) for a in w.get('author',[])]
        year=None
        try: year=w.get('published-print',w.get('published-online',{})).get('date-parts',[[None]])[0][0]
        except Exception: pass
        out.append(dict(title=title, authors=authors, year=year, venue=(w.get('container-title') or [''])[0], doi=w.get('DOI'), abstract=re.sub('<[^>]+>','',w.get('abstract','')), url=w.get('URL'), pdf_url=None, source='crossref', citation_count=w.get('is-referenced-by-count') or 0, open_access=False))
    return out


def cmd_collect(args):
    init_db(); allp=[]; raw=ROOT/f"data/incoming/collection_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    for q in queries():
        for name,fn in [('arxiv',collect_arxiv),('openalex',collect_openalex),('crossref',collect_crossref)]:
            try:
                ps=fn(q,args.limit); allp.extend(ps); time.sleep(0.5)
                print(name, q, len(ps))
            except Exception as e: print('WARN', name, e)
    with raw.open('w',encoding='utf-8') as f:
        for p in allp:
            p['collection_date']=now(); upsert(p); f.write(json.dumps(p,ensure_ascii=False)+'\n')
    log(f'collect 실행: {len(allp)}건 수집, raw={raw.name}')


def keyword_lists():
    p=load_yaml(ROOT/'config/topic_profile.yaml'); kw=p.get('keywords',{}) if isinstance(p,dict) else {}
    pos=[]; neg=[]
    for k,v in kw.items():
        if k=='exclude': neg+=v or []
        elif isinstance(v,list): pos+=v
    venues=p.get('preferred_venues',[]) if isinstance(p,dict) else []
    return [x.lower() for x in pos], [x.lower() for x in neg], [x.lower() for x in venues]


def score_config():
    cfg = load_yaml(ROOT/'config/scoring.yaml')
    weights = cfg.get('weights', {}) if isinstance(cfg, dict) else {}
    thresholds = cfg.get('thresholds', {}) if isinstance(cfg, dict) else {}
    recency = cfg.get('recency', {}) if isinstance(cfg, dict) else {}
    return {
        'weights': {
            'topic_relevance': float(weights.get('topic_relevance', 0.35)),
            'title_signal': float(weights.get('title_signal', 0.20)),
            'recency': float(weights.get('recency', 0.15)),
            'citation_signal': float(weights.get('citation_signal', 0.15)),
            'venue_signal': float(weights.get('venue_signal', 0.10)),
            'open_access_signal': float(weights.get('open_access_signal', 0.05)),
            'exclusion_penalty': float(weights.get('exclusion_penalty', 0.25)),
        },
        'thresholds': {
            'candidate': float(thresholds.get('candidate', 0.35)),
            'to_read': float(thresholds.get('to_read', 0.55)),
            'important': float(thresholds.get('important', 0.75)),
        },
        'current_year': int(recency.get('current_year') or datetime.now().year),
        'half_life_years': max(1, int(recency.get('half_life_years') or 8)),
    }


def cmd_score(args):
    pos,neg,venues=keyword_lists(); cfg=score_config(); year_now=cfg['current_year']; w=cfg['weights']; th=cfg['thresholds']; c=conn(); rows=c.execute('SELECT * FROM papers').fetchall(); n=0
    for r in rows:
        text=((r['title'] or '')+' '+(r['abstract'] or '')).lower(); title=(r['title'] or '').lower(); venue=(r['venue'] or '').lower()
        hits=sum(1 for k in pos if k in text); title_hits=sum(1 for k in pos if k in title)
        topic=min(1,hits/max(4,len(pos)/6 or 1)); title_sig=min(1,title_hits/3)
        y=r['year'] or 0; rec=0 if not y else max(0,min(1,1-(year_now-y)/(cfg['half_life_years']*2)))
        cit=min(1, math.log10((r['citation_count'] or 0)+1)/4)
        ven=1 if any(v and v in venue for v in venues) else 0
        oa=1 if r['open_access'] or r['pdf_url'] else 0
        pen=min(1,sum(1 for k in neg if k in text)/2)
        score=max(0,w['topic_relevance']*topic+w['title_signal']*title_sig+w['recency']*rec+w['citation_signal']*cit+w['venue_signal']*ven+w['open_access_signal']*oa-w['exclusion_penalty']*pen)
        status='important' if score>=th['important'] else 'to_read' if score>=th['to_read'] else 'candidate' if score>=th['candidate'] else 'screened'
        c.execute('UPDATE papers SET score=?, topic_relevance=?, status=?, updated_at=? WHERE id=?',(score,topic,status,now(),r['id'])); n+=1
    c.commit(); c.close(); log(f'score 실행: {n}건 점수화')


def top_rows(limit=20, where='1=1'):
    c=conn(); rows=c.execute(f'SELECT * FROM papers WHERE {where} ORDER BY score DESC, year DESC LIMIT ?', (limit,)).fetchall(); c.close(); return rows


def cmd_digest(args):
    rows=top_rows(args.top); p=ROOT/f'reports/daily_digest/digest_{today()}.md'
    lines=[f'# Daily Research Digest {today()}\n']
    for i,r in enumerate(rows,1):
        lines.append(f"## {i}. {r['title']}\n")
        lines.append(f"- score: {r['score']:.3f} / status: {r['status']} / year: {r['year']} / venue: {r['venue']}\n")
        lines.append(f"- citekey: `{r['citekey']}`\n- url: {r['url']}\n- pdf: {r['pdf_url']}\n")
        abs=(r['abstract'] or '')[:800]; lines.append(f"\n{abs}\n")
    p.write_text('\n'.join(lines), encoding='utf-8'); log(f'digest 생성: {p}')
    print(p)


def cmd_download(args):
    rows=top_rows(args.limit, "pdf_url IS NOT NULL AND pdf_url != ''")
    done=0; c=conn()
    for r in rows:
        if r['score'] < args.min_score: continue
        dest=ROOT/'data/pdfs'/f"{r['id']}_{slug(r['title'],50)}.pdf"
        if dest.exists() and not args.overwrite: continue
        try:
            req=urllib.request.Request(r['pdf_url'], headers={'User-Agent':'PaperOps/0.1'})
            with urllib.request.urlopen(req, timeout=60) as resp: data=resp.read()
            if len(data)>1000:
                dest.write_bytes(data); c.execute('UPDATE papers SET local_pdf_path=?, updated_at=? WHERE id=?',(str(dest.relative_to(ROOT)),now(),r['id'])); done+=1
        except Exception as e: print('WARN pdf', r['id'], e)
    c.commit(); c.close(); log(f'download-pdfs 실행: {done}개 PDF 저장')


def cmd_parse(args):
    try: import fitz
    except Exception:
        print('PyMuPDF가 없습니다. pip install -r requirements.txt 실행 필요'); return
    c=conn(); rows=c.execute("SELECT * FROM papers WHERE local_pdf_path IS NOT NULL AND local_pdf_path != ''").fetchall(); done=0
    for r in rows:
        pdf=ROOT/r['local_pdf_path']; out=ROOT/'data/parsed'/f"{r['id']}.txt"
        if not pdf.exists(): continue
        try:
            doc=fitz.open(str(pdf)); pages=[]
            for i,page in enumerate(doc):
                if i>=args.max_pages: break
                pages.append(f'\n\n[PAGE {i+1}]\n'+page.get_text())
            out.write_text('\n'.join(pages), encoding='utf-8', errors='ignore')
            c.execute('UPDATE papers SET parsed_text_path=?, updated_at=? WHERE id=?',(str(out.relative_to(ROOT)),now(),r['id'])); done+=1
        except Exception as e: print('WARN parse', pdf, e)
    c.commit(); c.close(); log(f'parse 실행: {done}개 PDF 파싱')


def cmd_cards(args):
    rows=top_rows(args.limit); done=0
    fmt = getattr(args, 'format', 'markdown')
    if fmt == 'yaml':
        outdir = ROOT/'notes/papers_yaml'
        outdir.mkdir(parents=True, exist_ok=True)
        for r in rows:
            path=outdir/f"{r['citekey']}_{r['id']}_{slug(r['title'],50)}.paper.md"
            parsed=''
            if r['parsed_text_path'] and (ROOT/r['parsed_text_path']).exists(): parsed=(ROOT/r['parsed_text_path']).read_text(encoding='utf-8',errors='ignore')[:1200]
            summary=(r['abstract'] or parsed or '[TODO: PDF parsing or abstract required]')[:1200]
            data=paper_card_yaml_data(r)
            body=f"""# {r['title']}

## Summary

{summary}

## Evidence Workbench

- research_problem: TODO
- prior_limitations: TODO
- contribution: TODO
- method: TODO
- evaluation: TODO

## Verification Notes

All extracted fields start as `verified: false`. Promote evidence only after human review of exact quote, page, and section.
"""
            path.write_text(yaml_frontmatter(data)+body, encoding='utf-8')
            done+=1
        log(f'cards 실행: {done}개 YAML Paper Card 생성')
        print(ROOT/'notes/papers_yaml')
        return
    for r in rows:
        path=ROOT/'notes/papers'/f"{r['citekey']}_{slug(r['title'],50)}.md"
        parsed=''
        if r['parsed_text_path'] and (ROOT/r['parsed_text_path']).exists(): parsed=(ROOT/r['parsed_text_path']).read_text(encoding='utf-8',errors='ignore')[:1200]
        summary=(r['abstract'] or parsed or '[TODO: PDF 파싱/초록 필요]')[:1200]
        text=f"""# {r['title']}\n\n- Paper ID: `{r['id']}`\n- Citekey: `{r['citekey']}`\n- Year: {r['year']}\n- Venue: {r['venue']}\n- DOI: {r['doi']}\n- arXiv: {r['arxiv_id']}\n- URL: {r['url']}\n- PDF: {r['local_pdf_path']}\n- Status: {r['status']}\n- Score: {r['score']:.3f}\n\n## 1. 한 문장 요약\n\n{summary}\n\n## 2. 연구 질문\n\n[TODO]\n\n## 3. 핵심 기여\n\n- [TODO]\n\n## 4. 방법론\n\n[TODO]\n\n## 5. 내 연구와의 관련성\n\nScore {r['score']:.3f}. Evidence Matrix에 쓸 claim을 확인하세요.\n\n## 6. 인용 가능한 주장\n\n| claim_type | claim | quote | page | section | confidence | use_in_section |\n|---|---|---|---|---|---|---|\n| background | [TODO] | [TODO] |  |  | low | related_work |\n\n## 7. 한계 / 연구 공백\n\n[TODO]\n"""
        path.write_text(text, encoding='utf-8'); done+=1
    log(f'cards 실행: {done}개 Paper Card 생성')


def evidence_block():
    return {'text': '', 'exact_quote': '', 'page': '', 'section': '', 'verified': False}


def paper_card_yaml_data(row):
    return {
        'paper_id': row['id'],
        'citekey': row['citekey'],
        'title': row['title'] or '',
        'year': row['year'],
        'venue': row['venue'] or '',
        'doi': row['doi'] or '',
        'arxiv_id': row['arxiv_id'] or '',
        'url': row['url'] or '',
        'status': row['status'] or '',
        'score': round(float(row['score'] or 0), 6),
        'topic_tags': axis_tags_for(row),
        'research_problem': evidence_block(),
        'prior_limitations': evidence_block(),
        'contribution': evidence_block(),
        'method': {'artifact_type': '', 'dataset': '', 'baseline': '', 'metric': ''},
        'evaluation': {'metrics': [], 'baselines': [], 'results': ''},
        'relevance_to_my_thesis': {'score': '', 'reason': ''},
        'decision': {'status': row['status'] or '', 'reason': ''},
    }


def yaml_frontmatter(data):
    try:
        import yaml
        dumped = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    except Exception:
        dumped = json.dumps(data, ensure_ascii=False, indent=2)
    return f'---\n{dumped}---\n\n'


def cmd_extract(args):
    ev=ROOT/'matrices/evidence_matrix.csv'; init_db(); rows=top_rows(args.limit); existing=ev.read_text(encoding='utf-8') if ev.exists() else ''
    with ev.open('a',encoding='utf-8',newline='') as f:
        w=csv.writer(f); n=0
        for r in rows:
            if r['id'] in existing: continue
            quote, page, section = evidence_quote_candidate(r)
            claim=(quote or r['abstract'] or r['title'] or '')[:300].replace('\n',' ')
            confidence='medium' if quote and page else 'low'
            comment='파싱 본문에서 자동 추출한 후보 문장: 원문 검증 필요' if quote else '자동 초벌 행: 사람이 검증 필요'
            w.writerow([r['id'],r['citekey'],'background',claim,quote,page,section,confidence,'related_work',comment,'false',r['parsed_text_path'] or '',datetime.now().isoformat(),datetime.now().isoformat()]); n+=1
    log(f'extract-evidence 실행: 초벌 evidence {n}행 추가')


def evidence_quote_candidate(row):
    if not row['parsed_text_path']:
        return '', '', 'abstract'
    path = ROOT / row['parsed_text_path']
    if not path.exists():
        return '', '', 'abstract'
    text = path.read_text(encoding='utf-8', errors='ignore')
    chunks = re.split(r'\n\s*\[PAGE\s+(\d+)\]\s*\n', text)
    pos, _, _ = keyword_lists()
    keywords = [k for k in pos if len(k) >= 4][:30] or ['ontology', 'knowledge graph', 'semantic web']
    best = ('', '', 0)
    for i in range(1, len(chunks), 2):
        page = chunks[i]
        body = chunks[i+1] if i+1 < len(chunks) else ''
        sentences = re.split(r'(?<=[.!?])\s+', re.sub(r'\s+', ' ', body))
        for sentence in sentences:
            clean = sentence.strip()
            if len(clean) < 80 or len(clean) > 700:
                continue
            lower = clean.lower()
            score = sum(1 for k in keywords if k.lower() in lower)
            if score > best[2]:
                best = (clean, page, score)
    if best[2] == 0:
        return '', '', 'full_text'
    return best[0], best[1], 'full_text'


def cmd_outline(args):
    rows=top_rows(20); p=ROOT/f'reports/survey_reports/outline_{today()}.md'
    lines=['# Manuscript Outline Draft\n','## Title candidates\n','- Ontology-based Research Automation: An Evidence-first Workflow\n','## Core papers\n']
    for r in rows[:10]: lines.append(f"- {r['citekey']}: {r['title']} ({r['year']}) score={r['score']:.3f}\n")
    lines += ['\n## 1. Introduction\n- Problem\n- Gap\n- Contribution\n','## 2. Related Work\n- Ontology/Knowledge Graph\n- Literature Review Automation\n- Research Agents\n','## 3. Method\n- PaperOps pipeline\n- Evidence Matrix\n- Audit loop\n','## 4. Evaluation\n- Case study\n- Coverage\n- Citation correctness\n','## 5. Discussion\n- Limits\n- Future work\n']
    p.write_text('\n'.join(lines), encoding='utf-8'); log(f'outline 생성: {p}')
    print(p)


def read_evidence_rows():
    ev = ROOT/'matrices/evidence_matrix.csv'
    if not ev.exists():
        return []
    with ev.open(encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def axis_tags_for(row):
    text = ' '.join([row['title'] or '', row['abstract'] or '', row['venue'] or '']).lower()
    axes = {
        'ontology_core': ['ontology', 'ontologies', 'semantic web', 'rdf', 'owl', 'sparql', 'shacl', 'skos', 'linked data'],
        'graphrag_llm': ['graphrag', 'rag', 'retrieval augmented', 'llm', 'language model', 'hallucination', 'faithfulness'],
        'research_agent': ['research agent', 'scientific discovery', 'literature review', 'academic writing', 'citation', 'paper recommendation'],
        'scholarly_kg': ['scholarly knowledge graph', 'scientific knowledge graph', 'bibliographic', 'citation network'],
        'evaluation': ['evaluation', 'benchmark', 'ablation', 'metric', 'human evaluation', 'precision', 'recall'],
        'operational_domain': ['enterprise', 'manufacturing', 'finance', 'governance', 'risk', 'process', 'event'],
    }
    matched = [name for name, terms in axes.items() if any(t in text for t in terms)]
    return matched or ['unclassified']


def screen_decision(row, axes):
    if row['status'] in ('important', 'to_read'):
        return 'keep'
    if row['score'] >= 0.35 and any(a in axes for a in ('ontology_core', 'graphrag_llm', 'research_agent', 'scholarly_kg')):
        return 'watch'
    return 'defer'


def cmd_screen(args):
    init_db()
    rows = top_rows(args.limit)
    out = ROOT/'matrices/screening_matrix.csv'
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['paper_id','citekey','title','year','venue','score','status','axes','decision','reason','url','pdf_url'])
        for r in rows:
            axes = axis_tags_for(r)
            decision = screen_decision(r, axes)
            reason = f"score={r['score']:.3f}; axes={','.join(axes)}; status={r['status']}"
            w.writerow([r['id'], r['citekey'], r['title'], r['year'], r['venue'], f"{r['score']:.3f}", r['status'], ';'.join(axes), decision, reason, r['url'], r['pdf_url']])
    log(f'screen 실행: {out}, {len(rows)}건')
    print(out)


def cmd_gap(args):
    init_db()
    rows = top_rows(args.limit)
    ev_rows = read_evidence_rows()
    verified_by_id = defaultdict(int)
    for e in ev_rows:
        if (e.get('verified') or '').lower() == 'true':
            verified_by_id[e.get('paper_id')] += 1
    groups = defaultdict(list)
    for r in rows:
        axes = axis_tags_for(r)
        for axis in axes:
            groups[axis].append(r)
    csv_out = ROOT/'matrices/gap_matrix.csv'
    with csv_out.open('w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['axis','paper_count','top_citekeys','evidence_verified_count','gap_hypothesis','next_action'])
        for axis, items in sorted(groups.items()):
            verified = sum(verified_by_id.get(r['id'], 0) for r in items)
            citekeys = ', '.join(r['citekey'] for r in items[:8])
            if axis == 'graphrag_llm':
                gap = 'GraphRAG and ontology papers often discuss retrieval quality, but fewer papers connect evidence traceability to thesis-writing workflows.'
            elif axis == 'research_agent':
                gap = 'Research-agent tools automate pieces of discovery and drafting, but need stronger citation-grounding and human approval checkpoints.'
            elif axis == 'evaluation':
                gap = 'Evaluation metrics are fragmented; a thesis-ready system needs citation correctness, evidence coverage, and workflow-efficiency metrics together.'
            else:
                gap = 'Current corpus needs stronger mapping from foundational concepts to the proposed engineering artifact.'
            action = 'verify quote/page evidence for top papers and convert into chapter-specific claims'
            w.writerow([axis, len(items), citekeys, verified, gap, action])
    md_out = ROOT/f'reports/survey_reports/gap_map_{today()}.md'
    lines = [f'# Gap Map {today()}\n', '## Axis Summary\n']
    for axis, items in sorted(groups.items()):
        lines.append(f"- **{axis}**: {len(items)} papers; top: {', '.join(r['citekey'] for r in items[:5])}\n")
    lines.append('\n## Thesis Candidate\n')
    lines.append('Ontology-enhanced GraphRAG 기반 연구문헌 근거 추적 및 논문작성 지원 파이프라인의 설계와 평가\n')
    lines.append('\n## Immediate Gap Claims To Verify\n')
    lines.append('- Research support agents need stronger source-linked evidence matrices before their output can be trusted in thesis writing. [NEEDS_VERIFICATION]\n')
    lines.append('- Ontology/GraphRAG can be framed as a traceability layer that connects literature claims, paper cards, evaluation logs, and manuscript citations. [NEEDS_VERIFICATION]\n')
    md_out.write_text(''.join(lines), encoding='utf-8')
    log(f'gap 실행: {csv_out}, {md_out}')
    print(csv_out)
    print(md_out)


def cmd_research_design(args):
    outdir = ROOT/'research_design'
    outdir.mkdir(parents=True, exist_ok=True)
    files = {
        'problem_definition.md': """# Problem Definition

## Problem
AI-assisted research tools can collect, summarize, and draft text, but thesis writing needs source-grounded traceability from paper discovery to final citations.

## Baseline
- Manual Zotero/PDF workflow
- Generic PDF chat or summarization tools
- Ad hoc literature-review prompts

## Proposed Artifact
PaperOps: an evidence-first research operating system for ontology/GraphRAG-based thesis work.

## Failure Criteria
- Claims cannot be traced to paper_id/citekey/quote/page.
- Generated chapter text contains unsupported strong claims.
- Screening decisions cannot be reproduced from metadata and scoring rules.
""",
        'research_questions.md': """# Research Questions

RQ1. Can an evidence-first PaperOps pipeline improve traceability between literature discovery, paper notes, evidence rows, and manuscript citations?

RQ2. Which ontology/GraphRAG topic axes produce the strongest thesis-ready research gap for the current corpus?

RQ3. How should citation correctness, evidence coverage, and workflow efficiency be evaluated in an AI-assisted thesis-writing system?
""",
        'artifact_definition.md': """# Artifact Definition

## System
PaperOps CLI and folder-based research OS.

## Core Components
- Multi-source paper collection
- Scoring and screening matrix
- PDF parsing and paper cards
- Evidence Matrix with quote/page fields
- Gap matrix and manuscript audit reports
- Reviewer-style critique loop

## Non-goals
- Fully automatic thesis writing
- Unverified citation generation
- Heavy LangGraph orchestration before the CLI workflow is stable
""",
        'evaluation_plan.md': """# Evaluation Plan

## Metrics
- Evidence coverage: share of manuscript citations represented in Evidence Matrix
- Quote completeness: share of evidence rows with quote and page
- Screening reproducibility: share of keep/watch/defer decisions explainable by score and axis tags
- Citation audit defects: missing citekeys and unsupported claims

## Baselines
- Manual folder/Zotero workflow
- Generic PDF summarization workflow

## Ablation
- Without axis-based screening
- Without page-level quote extraction
- Without citation audit
""",
    }
    for name, content in files.items():
        path = outdir/name
        if not path.exists() or args.overwrite:
            path.write_text(content, encoding='utf-8')
    log(f'research-design 초기화: {outdir}')
    print(outdir)


def cmd_brief(args):
    init_db()
    rows = top_rows(args.top)
    ev = read_evidence_rows()
    out = ROOT/f'reports/survey_reports/thesis_brief_{today()}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# Thesis Brief {today()}\n']
    lines.append('## Working Topic\nOntology-enhanced GraphRAG 기반 연구문헌 근거 추적 및 논문작성 지원 파이프라인의 설계와 평가\n')
    lines.append('## Current Corpus State\n')
    lines.append(f'- Top papers reviewed for this brief: {len(rows)}\n')
    lines.append(f'- Evidence Matrix rows: {len(ev)}\n')
    lines.append('## Top Papers\n')
    for r in rows:
        lines.append(f"- `{r['citekey']}` {r['title']} ({r['year']}), score={r['score']:.3f}, axes={';'.join(axis_tags_for(r))}\n")
    lines.append('\n## Next Meeting Decisions\n')
    lines.append('- Confirm whether PaperOps itself is the engineering artifact or only a support tool for another ontology/GraphRAG artifact.\n')
    lines.append('- Select one evaluation target: citation correctness, evidence coverage, or workflow efficiency.\n')
    lines.append('- Verify top evidence rows with quote/page before drafting Chapter 2.\n')
    out.write_text(''.join(lines), encoding='utf-8')
    log(f'thesis brief 생성: {out}')
    print(out)


def cmd_audit(args):
    man=ROOT/'manuscript/main.md'; ev=ROOT/'matrices/evidence_matrix.csv'; out=ROOT/f'reports/audit_reports/audit_{today()}.md'
    text=man.read_text(encoding='utf-8',errors='ignore') if man.exists() else ''
    cites=set(re.findall(r'@([A-Za-z0-9_:-]+)', text))
    evtext=ev.read_text(encoding='utf-8',errors='ignore') if ev.exists() else ''
    missing=[c for c in sorted(cites) if c not in evtext]
    out.write_text('# Citation Audit\n\n' + f'- manuscript cites: {len(cites)}\n- missing in evidence matrix: {len(missing)}\n\n' + '\n'.join(f'- {m}' for m in missing), encoding='utf-8')
    log(f'audit 실행: {out}')
    print(out)


def cmd_status(args):
    init_db(); c=conn(); total=c.execute('SELECT COUNT(*) FROM papers').fetchone()[0]; top=c.execute('SELECT COUNT(*) FROM papers WHERE status IN ("to_read","important")').fetchone()[0]; c.close()
    print('ROOT:', ROOT); print('DB:', DB, DB.exists()); print('papers:', total); print('to_read/important:', top); print('log:', LOG)


def file_row_count(path):
    if not path.exists():
        return 0
    try:
        with path.open(encoding='utf-8', newline='') as f:
            return max(0, sum(1 for _ in csv.reader(f)) - 1)
    except Exception:
        return 0


def status_line(name, ok, detail):
    mark = 'OK' if ok else 'WARN'
    return f'[{mark}] {name}: {detail}'


def command_version(command):
    try:
        result = subprocess.run([command, '--version'], cwd=str(ROOT), capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        return False, 'not installed or not on PATH'
    except Exception as e:
        return False, str(e)
    text = (result.stdout or result.stderr or '').strip().splitlines()
    detail = text[0] if text else f'exit={result.returncode}'
    return result.returncode == 0, detail


def configured_secret(name, config_value=''):
    return bool(os.environ.get(name) or (config_value and 'your-' not in str(config_value).lower()))


def configured_grobid_url():
    pipeline = load_yaml(ROOT/'config/pipeline.yaml')
    sources = load_yaml(ROOT/'config/sources.yaml')
    raw = os.environ.get('GROBID_URL') or str(pipeline.get('grobid_url') or sources.get('grobid_url') or '')
    return raw.strip().rstrip('/')


def grobid_health_check(grobid_url=None):
    url = (grobid_url or configured_grobid_url()).strip().rstrip('/')
    if not url:
        return {'configured': False, 'ok': False, 'url': '', 'endpoint': '', 'detail': 'GROBID_URL not configured'}
    endpoint = url + '/api/isalive'
    try:
        req = urllib.request.Request(endpoint, headers={'User-Agent': 'PaperOps/0.1'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read(200).decode('utf-8', 'ignore').strip()
            ok = 200 <= resp.status < 300
            detail = body or f'HTTP {resp.status}'
            return {'configured': True, 'ok': ok, 'url': url, 'endpoint': endpoint, 'detail': detail}
    except Exception as e:
        return {'configured': True, 'ok': False, 'url': url, 'endpoint': endpoint, 'detail': str(e)}


def write_grobid_status_report(status):
    out = ROOT/f'reports/audit_reports/grobid_status_{today()}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# GROBID Status {today()}\n']
    lines.append('## Summary\n')
    lines.append(f"- configured: {str(status['configured']).lower()}\n")
    lines.append(f"- health_ok: {str(status['ok']).lower()}\n")
    lines.append(f"- grobid_url: `{status['url'] or '(not configured)'}`\n")
    lines.append(f"- endpoint: `{status['endpoint'] or '(not checked)'}`\n")
    lines.append(f"- detail: {status['detail']}\n")
    lines.append('\n## Policy\n')
    lines.append('- GROBID health failures are WARN, not FAIL, during readiness mode.\n')
    lines.append('- This command does not install Docker, parse PDFs, or write TEI XML.\n')
    out.write_text(''.join(lines), encoding='utf-8')
    return out


def cmd_grobid_status(args):
    status = grobid_health_check()
    out = write_grobid_status_report(status)
    mark = 'OK' if status['ok'] else 'WARN'
    print(status_line('GROBID_URL', status['configured'], status['url'] or 'not configured; set GROBID_URL'))
    print(status_line('GROBID health', status['ok'], f"{status['endpoint'] or 'not checked'} / {status['detail']}"))
    print(out)
    log(f'grobid-status 실행: {mark}, report={out.name}')


def cmd_doctor(args):
    init_db()
    lines = ['# PaperOps Doctor\n']
    ok_all = True

    try:
        c = conn()
        paper_count = c.execute('SELECT COUNT(*) FROM papers').fetchone()[0]
        c.close()
        ok = DB.exists()
        lines.append(status_line('DB', ok, f'{DB} / papers={paper_count}'))
        ok_all = ok_all and ok
    except Exception as e:
        lines.append(status_line('DB', False, str(e)))
        ok_all = False

    evidence = ROOT/'matrices/evidence_matrix.csv'
    ok = evidence.exists()
    lines.append(status_line('Evidence Matrix', ok, f'{evidence} / rows={file_row_count(evidence)}'))
    ok_all = ok_all and ok

    screening = ROOT/'matrices/screening_matrix.csv'
    ok = screening.exists()
    lines.append(status_line('screening_matrix', ok, f'{screening} / rows={file_row_count(screening)}'))
    ok_all = ok_all and ok

    refs = ROOT/'manuscript/references.bib'
    ok = refs.exists() and refs.stat().st_size > 0
    lines.append(status_line('references.bib', ok, f'{refs} / bytes={refs.stat().st_size if refs.exists() else 0}'))
    ok_all = ok_all and ok

    canonical_bib = ROOT/'05_manuscript/references.bib'
    legacy_bib = ROOT/'manuscript/references.bib'
    canonical_count = len(parse_bib_entries(canonical_bib))
    legacy_count = len(parse_bib_entries(legacy_bib))
    canonical_ok = canonical_bib.exists() and canonical_count > 0
    legacy_ok = legacy_bib.exists() and legacy_count > 0
    lines.append(status_line('Canonical bibliography', canonical_ok, f'{canonical_bib} / entries={canonical_count}'))
    lines.append(status_line('Legacy bibliography', legacy_ok, f'{legacy_bib} / entries={legacy_count}'))
    if not canonical_ok and legacy_ok:
        lines.append(status_line('Bibliography action', False, 'configure Zotero Better BibTeX export to 05_manuscript/references.bib; do not copy legacy automatically'))

    pdf_dir = ROOT/'data/pdfs'
    pdf_count = len(list(pdf_dir.glob('*.pdf'))) if pdf_dir.exists() else 0
    ok = pdf_dir.exists()
    lines.append(status_line('PDF folder', ok, f'{pdf_dir} / pdfs={pdf_count}'))
    ok_all = ok_all and ok

    quarto_ok, quarto_detail = command_version('quarto')
    lines.append(status_line('Quarto', quarto_ok, quarto_detail))

    sources = load_yaml(ROOT/'config/sources.yaml')
    grobid_status = grobid_health_check()
    if grobid_status['configured']:
        lines.append(status_line('GROBID setting', True, f"{grobid_status['url']}"))
        lines.append(status_line('GROBID health', grobid_status['ok'], f"{grobid_status['endpoint']} / {grobid_status['detail']}"))
    else:
        lines.append(status_line('GROBID setting', False, 'not configured; set GROBID_URL or config grobid_url when needed'))

    s2_cfg = sources.get('semantic_scholar_api_key') if isinstance(sources, dict) else ''
    api_checks = {
        'OPENAI_API_KEY': configured_secret('OPENAI_API_KEY'),
        'SEMANTIC_SCHOLAR_API_KEY': configured_secret('SEMANTIC_SCHOLAR_API_KEY', s2_cfg),
        'CROSSREF_MAILTO': configured_secret('CROSSREF_MAILTO', sources.get('openalex_mailto') if isinstance(sources, dict) else ''),
    }
    for key, ok in api_checks.items():
        lines.append(status_line(key, ok, 'present' if ok else 'missing'))

    print('\n'.join(lines))
    log('doctor 실행: 환경 점검 완료')
    if args.strict and not ok_all:
        raise SystemExit(1)


def list_grobid_pdf_targets(limit):
    targets = []
    try:
        c = conn()
        rows = c.execute("SELECT id, title, citekey, local_pdf_path FROM papers WHERE local_pdf_path IS NOT NULL AND local_pdf_path != '' ORDER BY score DESC, year DESC").fetchall()
        c.close()
    except Exception:
        rows = []
    seen = set()
    for r in rows:
        pdf = ROOT/r['local_pdf_path']
        if pdf.exists() and pdf.suffix.lower() == '.pdf':
            key = str(pdf.resolve()).lower()
            if key not in seen:
                targets.append({'paper_id': r['id'], 'citekey': r['citekey'], 'title': r['title'], 'pdf': pdf})
                seen.add(key)
        if len(targets) >= limit:
            return targets
    for pdf in sorted((ROOT/'data/pdfs').glob('*.pdf')):
        key = str(pdf.resolve()).lower()
        if key in seen:
            continue
        targets.append({'paper_id': pdf.stem.split('_', 1)[0], 'citekey': '', 'title': pdf.stem, 'pdf': pdf})
        seen.add(key)
        if len(targets) >= limit:
            break
    return targets


def path_for_report(path):
    try:
        return str(path.relative_to(ROOT))
    except Exception:
        return str(path)


def grobid_target_from_paper_id(paper_id):
    c = conn()
    row = c.execute('SELECT id, title, citekey, local_pdf_path FROM papers WHERE id=?', (paper_id,)).fetchone()
    c.close()
    if not row:
        raise FileNotFoundError(f'paper_id not found in DB: {paper_id}')
    if not row['local_pdf_path']:
        raise FileNotFoundError(f'paper_id has no local_pdf_path: {paper_id}')
    pdf = ROOT/row['local_pdf_path']
    if not pdf.exists():
        raise FileNotFoundError(f'PDF does not exist: {pdf}')
    return {'paper_id': row['id'], 'citekey': row['citekey'], 'title': row['title'], 'pdf': pdf}


def grobid_target_from_pdf(pdf_arg):
    pdf = Path(pdf_arg)
    if not pdf.is_absolute():
        pdf = ROOT/pdf
    pdf = pdf.resolve()
    if not pdf.exists() or pdf.suffix.lower() != '.pdf':
        raise FileNotFoundError(f'PDF does not exist: {pdf}')
    rel = ''
    try:
        rel = str(pdf.relative_to(ROOT)).replace('\\', '/')
    except Exception:
        pass
    c = conn()
    row = c.execute('SELECT id, title, citekey, local_pdf_path FROM papers WHERE REPLACE(local_pdf_path, "\\", "/")=?', (rel,)).fetchone() if rel else None
    c.close()
    if row:
        return {'paper_id': row['id'], 'citekey': row['citekey'], 'title': row['title'], 'pdf': pdf}
    paper_id = hashlib.sha1(str(pdf).lower().encode('utf-8')).hexdigest()[:16]
    return {'paper_id': paper_id, 'citekey': '', 'title': pdf.stem, 'pdf': pdf}


def resolve_grobid_targets(args):
    if args.paper_id:
        return [grobid_target_from_paper_id(args.paper_id)]
    if args.pdf:
        return [grobid_target_from_pdf(args.pdf)]
    return list_grobid_pdf_targets(max(0, args.limit))


def grobid_output_paths(target):
    base = ROOT/'data/parsed/grobid'/slug(target['paper_id'] or target['pdf'].stem, 80)
    return {
        'tei_xml': base/'tei.xml',
        'sections_json': base/'sections.json',
        'references_json': base/'references.json',
        'citation_contexts_json': base/'citation_contexts.json',
    }


def file_sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def local_name(el):
    return el.tag.rsplit('}', 1)[-1] if '}' in el.tag else el.tag


def element_text(el):
    return ' '.join(' '.join(el.itertext()).split())


def first_child_text(el, names):
    names = set(names)
    for child in el.iter():
        if local_name(child) in names:
            text = element_text(child)
            if text:
                return text
    return ''


def first_idno(el, id_type):
    wanted = id_type.lower()
    for child in el.iter():
        if local_name(child) == 'idno' and (child.attrib.get('type') or '').lower() == wanted:
            return element_text(child)
    return ''


def tei_sections(tei_xml, target, tei_path):
    root = ET.fromstring(tei_xml)
    body = next((el for el in root.iter() if local_name(el) == 'body'), None)
    sections = []
    if body is not None:
        divs = [el for el in body.iter() if local_name(el) == 'div']
        for div in divs:
            head = ''
            paragraphs = []
            for child in list(div):
                name = local_name(child)
                if name == 'head' and not head:
                    head = element_text(child)
                elif name == 'p':
                    text = element_text(child)
                    if text:
                        paragraphs.append(text)
            text = '\n\n'.join(paragraphs).strip()
            if text:
                order = len(sections) + 1
                sections.append({
                    'section_id': f'sec_{order:03d}',
                    'heading': head or f'Section {order}',
                    'level': 1,
                    'order': order,
                    'text': text,
                    'char_count': len(text),
                })
    if not sections and body is not None:
        text = element_text(body)
        if text:
            sections.append({
                'section_id': 'sec_001',
                'heading': 'Full Text',
                'level': 1,
                'order': 1,
                'text': text,
                'char_count': len(text),
            })
    return {
        'paper_id': target['paper_id'],
        'citekey': target.get('citekey') or '',
        'source_pdf': path_for_report(target['pdf']),
        'tei_xml': path_for_report(tei_path),
        'parser': 'grobid',
        'created_at': now(),
        'sections': sections,
    }


def tei_references(tei_xml, target):
    root = ET.fromstring(tei_xml)
    references = []
    for bibl in root.iter():
        if local_name(bibl) != 'biblStruct':
            continue
        ref_id = bibl.attrib.get('{http://www.w3.org/XML/1998/namespace}id') or bibl.attrib.get('xml:id') or f'b{len(references)}'
        authors = []
        for author in bibl.iter():
            if local_name(author) != 'author':
                continue
            name = first_child_text(author, ['persName']) or element_text(author)
            if name:
                authors.append(name)
        year = ''
        for date_el in bibl.iter():
            if local_name(date_el) == 'date':
                year = (date_el.attrib.get('when') or element_text(date_el) or '')[:4]
                if year:
                    break
        references.append({
            'reference_id': ref_id,
            'raw_text': element_text(bibl),
            'title': first_child_text(bibl, ['title']),
            'authors': authors,
            'year': year,
            'doi': first_idno(bibl, 'DOI'),
            'url': first_idno(bibl, 'URL'),
        })
    return {
        'paper_id': target['paper_id'],
        'citekey': target.get('citekey') or '',
        'parser': 'grobid',
        'created_at': now(),
        'references': references,
    }


def tei_citation_contexts(tei_xml, target):
    root = ET.fromstring(tei_xml)
    parent = {child: par for par in root.iter() for child in par}
    contexts = []
    for ref in root.iter():
        if local_name(ref) != 'ref' or (ref.attrib.get('type') or '').lower() != 'bibr':
            continue
        par = parent.get(ref)
        while par is not None and local_name(par) != 'p':
            par = parent.get(par)
        sentence = element_text(par) if par is not None else element_text(ref)
        if not sentence:
            continue
        contexts.append({
            'context_id': f'ctx_{len(contexts) + 1:03d}',
            'section_id': '',
            'section_heading': '',
            'page': None,
            'reference_id': (ref.attrib.get('target') or '').lstrip('#'),
            'marker': element_text(ref),
            'before': '',
            'sentence': sentence,
            'after': '',
        })
    return {
        'paper_id': target['paper_id'],
        'citekey': target.get('citekey') or '',
        'parser': 'grobid',
        'created_at': now(),
        'contexts': contexts,
    }


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def call_grobid_process_fulltext(grobid_url, pdf):
    try:
        import requests
    except Exception as e:
        raise RuntimeError(f'requests import failed: {e}')
    endpoint = grobid_url.rstrip('/') + '/api/processFulltextDocument'
    try:
        timeout = int(os.environ.get('GROBID_TIMEOUT_SECONDS') or os.environ.get('GROBID_TIMEOUT') or '300')
    except ValueError:
        timeout = 300
    timeout = max(30, timeout)
    with pdf.open('rb') as f:
        files = {'input': (pdf.name, f, 'application/pdf')}
        data = {'consolidateHeader': '1', 'consolidateCitations': '1'}
        resp = requests.post(endpoint, files=files, data=data, timeout=timeout)
    if resp.status_code < 200 or resp.status_code >= 300:
        raise RuntimeError(f'GROBID HTTP {resp.status_code}: {resp.text[:500]}')
    text = resp.text.strip()
    if not text:
        raise RuntimeError('GROBID returned empty TEI XML')
    return text


def write_grobid_apply_report(result):
    out = ROOT/f'reports/audit_reports/grobid_parse_apply_{today()}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# GROBID Parse Apply {today()}\n']
    lines.append('## Summary\n')
    for key in ['mode', 'success', 'paper_id', 'citekey', 'source_pdf', 'pdf_size', 'pdf_sha256', 'grobid_url', 'health_ok', 'elapsed_seconds']:
        lines.append(f"- {key}: {result.get(key)}\n")
    lines.append('\n## Artifacts\n')
    for key in ['tei_xml', 'sections_json', 'references_json', 'citation_contexts_json']:
        artifact = result.get(key) or {}
        lines.append(f"- {key}: `{artifact.get('path', '')}` / bytes={artifact.get('bytes', 0)}")
        if 'count' in artifact:
            lines.append(f" / count={artifact['count']}")
        lines.append('\n')
    lines.append('\n## Warnings\n')
    warnings = result.get('warnings') or []
    lines.extend([f"- {w}\n" for w in warnings] or ['- none\n'])
    lines.append('\n## Errors\n')
    errors = result.get('errors') or []
    lines.extend([f"- {e}\n" for e in errors] or ['- none\n'])
    out.write_text(''.join(lines), encoding='utf-8')
    return out


def cmd_parse_grobid(args):
    if args.apply and args.dry_run:
        raise SystemExit('ERROR: --apply and --dry-run cannot be used together')
    if not args.apply and not args.dry_run:
        raise SystemExit('ERROR: choose --dry-run or --apply')
    if args.paper_id and args.pdf:
        raise SystemExit('ERROR: choose only one of --paper-id or --pdf')
    one_file = bool(args.paper_id or args.pdf)
    if args.apply and not one_file:
        raise SystemExit('ERROR: --apply requires --paper-id or --pdf')
    status = grobid_health_check()
    try:
        targets = resolve_grobid_targets(args)
    except Exception as e:
        if args.apply:
            result = {'mode': 'apply', 'success': False, 'grobid_url': status['url'], 'health_ok': status['ok'], 'elapsed_seconds': 0, 'warnings': [], 'errors': [str(e)]}
            out = write_grobid_apply_report(result)
            print(out)
            raise SystemExit(1)
        raise
    if args.dry_run:
        out = ROOT/f'reports/audit_reports/grobid_parse_dry_run_{today()}.md'
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = [f'# GROBID Parse Dry Run {today()}\n']
        lines.append('## Summary\n')
        lines.append(f"- mode: dry-run\n")
        lines.append(f"- requested_limit: {args.limit}\n")
        lines.append(f"- one_file_mode: {str(one_file).lower()}\n")
        lines.append(f"- selected_pdfs: {len(targets)}\n")
        lines.append(f"- grobid_configured: {str(status['configured']).lower()}\n")
        lines.append(f"- grobid_health_ok: {str(status['ok']).lower()}\n")
        lines.append(f"- grobid_url: `{status['url'] or '(not configured)'}`\n")
        lines.append(f"- grobid_detail: {status['detail']}\n")
        lines.append('\n## Targets\n')
        if not targets:
            lines.append('- none\n')
        for i, target in enumerate(targets, 1):
            paths = grobid_output_paths(target)
            lines.append(f"\n### {i}. {target['pdf'].name}\n")
            lines.append(f"- paper_id: `{target['paper_id']}`\n")
            lines.append(f"- citekey: `{target['citekey'] or '(unknown)'}`\n")
            lines.append(f"- source_pdf: `{path_for_report(target['pdf'])}`\n")
            lines.append(f"- expected_tei_xml: `{path_for_report(paths['tei_xml'])}`\n")
            lines.append(f"- expected_sections_json: `{path_for_report(paths['sections_json'])}`\n")
            lines.append(f"- expected_references_json: `{path_for_report(paths['references_json'])}`\n")
            lines.append(f"- expected_citation_contexts_json: `{path_for_report(paths['citation_contexts_json'])}`\n")
        lines.append('\n## Policy\n')
        lines.append('- No PDF was uploaded to GROBID during this dry run.\n')
        lines.append('- No TEI XML or JSON extraction artifact was written.\n')
        lines.append('- Missing or unhealthy GROBID_URL is WARN during readiness mode.\n')
        out.write_text(''.join(lines), encoding='utf-8')
        log(f'parse-grobid dry-run 실행: targets={len(targets)}, report={out.name}')
        print(out)
        return
    target = targets[0]
    start = time.time()
    paths = grobid_output_paths(target)
    result = {
        'mode': 'apply',
        'success': False,
        'paper_id': target['paper_id'],
        'citekey': target.get('citekey') or '',
        'source_pdf': path_for_report(target['pdf']),
        'pdf_size': target['pdf'].stat().st_size,
        'pdf_sha256': file_sha256(target['pdf']),
        'grobid_url': status['url'],
        'health_ok': status['ok'],
        'elapsed_seconds': 0,
        'warnings': [],
        'errors': [],
    }
    result['tei_xml'] = {'path': path_for_report(paths['tei_xml']), 'bytes': paths['tei_xml'].stat().st_size if paths['tei_xml'].exists() else 0}
    result['sections_json'] = {'path': path_for_report(paths['sections_json']), 'bytes': paths['sections_json'].stat().st_size if paths['sections_json'].exists() else 0, 'count': 0}
    result['references_json'] = {'path': path_for_report(paths['references_json']), 'bytes': paths['references_json'].stat().st_size if paths['references_json'].exists() else 0, 'count': 0}
    result['citation_contexts_json'] = {'path': path_for_report(paths['citation_contexts_json']), 'bytes': paths['citation_contexts_json'].stat().st_size if paths['citation_contexts_json'].exists() else 0, 'count': 0}
    try:
        if not status['configured'] or not status['ok']:
            raise RuntimeError(f"GROBID health check failed: {status['detail']}")
        tei_xml = call_grobid_process_fulltext(status['url'], target['pdf'])
        paths['tei_xml'].parent.mkdir(parents=True, exist_ok=True)
        paths['tei_xml'].write_text(tei_xml, encoding='utf-8')
        sections = tei_sections(tei_xml, target, paths['tei_xml'])
        refs = tei_references(tei_xml, target)
        contexts = tei_citation_contexts(tei_xml, target)
        write_json(paths['sections_json'], sections)
        write_json(paths['references_json'], refs)
        write_json(paths['citation_contexts_json'], contexts)
        result['success'] = len(sections.get('sections') or []) >= 1 and paths['tei_xml'].stat().st_size > 0
        if not result['success']:
            result['warnings'].append('TEI was written, but no sections were extracted')
        result['tei_xml'] = {'path': path_for_report(paths['tei_xml']), 'bytes': paths['tei_xml'].stat().st_size}
        result['sections_json'] = {'path': path_for_report(paths['sections_json']), 'bytes': paths['sections_json'].stat().st_size, 'count': len(sections.get('sections') or [])}
        result['references_json'] = {'path': path_for_report(paths['references_json']), 'bytes': paths['references_json'].stat().st_size, 'count': len(refs.get('references') or [])}
        result['citation_contexts_json'] = {'path': path_for_report(paths['citation_contexts_json']), 'bytes': paths['citation_contexts_json'].stat().st_size, 'count': len(contexts.get('contexts') or [])}
    except Exception as e:
        result['errors'].append(str(e))
    result['elapsed_seconds'] = round(time.time() - start, 2)
    out = write_grobid_apply_report(result)
    log(f"parse-grobid apply 실행: success={result['success']}, paper_id={result.get('paper_id')}, report={out.name}")
    print(out)
    if not result['success']:
        raise SystemExit(1)


def grobid_artifact_dir_from_args(args):
    if getattr(args, 'paper_id', None) and getattr(args, 'path', None):
        raise SystemExit('ERROR: choose only one of --paper-id or --path')
    if getattr(args, 'paper_id', None):
        return ROOT/'data/parsed/grobid'/slug(args.paper_id, 80)
    if getattr(args, 'path', None):
        path = Path(args.path)
        return path if path.is_absolute() else ROOT/path
    raise SystemExit('ERROR: choose --paper-id or --path')


def read_json_file(path, errors):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        errors.append(f'{path_for_report(path)} invalid JSON: {e}')
        return {}


def validate_grobid_artifact_dir(artifact_dir):
    paths = {
        'tei': artifact_dir/'tei.xml',
        'sections': artifact_dir/'sections.json',
        'references': artifact_dir/'references.json',
        'contexts': artifact_dir/'citation_contexts.json',
    }
    warnings = []
    errors = []
    sections_data = read_json_file(paths['sections'], errors) if paths['sections'].exists() else {}
    refs_data = read_json_file(paths['references'], errors) if paths['references'].exists() else {}
    contexts_data = read_json_file(paths['contexts'], errors) if paths['contexts'].exists() else {}
    for name, path in paths.items():
        if not path.exists():
            errors.append(f'{name} missing: {path_for_report(path)}')
        elif path.stat().st_size <= 0:
            errors.append(f'{name} empty: {path_for_report(path)}')
    for label, data, required in [
        ('sections.json', sections_data, ['paper_id', 'citekey', 'source_pdf', 'tei_xml', 'parser', 'created_at', 'sections']),
        ('references.json', refs_data, ['paper_id', 'citekey', 'parser', 'created_at', 'references']),
        ('citation_contexts.json', contexts_data, ['paper_id', 'citekey', 'parser', 'created_at', 'contexts']),
    ]:
        for field in required:
            if field not in data:
                errors.append(f'{label} missing required field: {field}')
    sections = sections_data.get('sections') if isinstance(sections_data.get('sections'), list) else []
    refs = refs_data.get('references') if isinstance(refs_data.get('references'), list) else []
    contexts = contexts_data.get('contexts') if isinstance(contexts_data.get('contexts'), list) else []
    metrics = {
        'paper_id': sections_data.get('paper_id') or refs_data.get('paper_id') or contexts_data.get('paper_id') or artifact_dir.name,
        'citekey': sections_data.get('citekey') or refs_data.get('citekey') or contexts_data.get('citekey') or '',
        'artifact_dir': path_for_report(artifact_dir),
        'tei_exists': paths['tei'].exists(),
        'tei_bytes': paths['tei'].stat().st_size if paths['tei'].exists() else 0,
        'section_count': len(sections),
        'empty_heading_count': sum(1 for s in sections if not str(s.get('heading') or '').strip()),
        'short_section_count': sum(1 for s in sections if int(s.get('char_count') or len(str(s.get('text') or ''))) < 200),
        'reference_count': len(refs),
        'reference_missing_title_count': sum(1 for r in refs if not str(r.get('title') or '').strip()),
        'context_count': len(contexts),
        'context_missing_reference_id_count': sum(1 for c in contexts if not str(c.get('reference_id') or '').strip()),
        'warnings': warnings,
        'errors': errors,
    }
    if metrics['section_count'] == 0:
        warnings.append('sections.json has no sections')
    if metrics['context_missing_reference_id_count']:
        warnings.append('some citation contexts are missing reference_id')
    return metrics


def write_grobid_artifact_audit(metrics):
    out = ROOT/f'reports/audit_reports/grobid_artifact_audit_{today()}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# GROBID Artifact Audit {today()}\n', '## Metrics\n']
    for key in ['paper_id', 'citekey', 'artifact_dir', 'tei_exists', 'tei_bytes', 'section_count', 'empty_heading_count', 'short_section_count', 'reference_count', 'reference_missing_title_count', 'context_count', 'context_missing_reference_id_count']:
        lines.append(f'- {key}: {metrics.get(key)}\n')
    lines.append('\n## Warnings\n')
    lines.extend([f"- {w}\n" for w in metrics.get('warnings', [])] or ['- none\n'])
    lines.append('\n## Errors\n')
    lines.extend([f"- {e}\n" for e in metrics.get('errors', [])] or ['- none\n'])
    out.write_text(''.join(lines), encoding='utf-8')
    return out


def cmd_validate_grobid_artifacts(args):
    artifact_dir = grobid_artifact_dir_from_args(args)
    metrics = validate_grobid_artifact_dir(artifact_dir)
    out = write_grobid_artifact_audit(metrics)
    print(out)
    print(f"section_count={metrics['section_count']}")
    print(f"reference_count={metrics['reference_count']}")
    print(f"context_count={metrics['context_count']}")
    log(f"validate-grobid-artifacts 실행: paper_id={metrics['paper_id']}, errors={len(metrics['errors'])}, report={out.name}")
    if metrics['errors']:
        raise SystemExit(1)


def split_sentences(text):
    text = ' '.join((text or '').split())
    if not text:
        return []
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z0-9])', text)
    return [p.strip() for p in parts if 80 <= len(p.strip()) <= 800]


def candidate_type_for(heading, sentence, source):
    h = (heading or '').lower()
    s = (sentence or '').lower()
    if source == 'citation_contexts.json':
        return 'background', 'literature'
    if any(k in h for k in ['method', 'system', 'pipeline', 'construction', 'architecture']) or any(k in s for k in ['propose', 'present', 'develop', 'implement', 'framework', 'architecture', 'pipeline']):
        return 'method', 'method'
    if any(k in h for k in ['evaluation', 'experiment', 'result', 'validation']) or any(k in s for k in ['evaluate', 'result', 'performance', 'accuracy', 'demonstrate', 'validate']):
        return 'finding', 'evaluation'
    if any(k in h for k in ['limitation', 'discussion', 'future']) or any(k in s for k in ['limitation', 'challenge', 'future work']):
        return 'limitation', 'discussion'
    if any(k in s for k in ['define', 'called', 'refers to', ' is a ', ' are a ', ' is an ', ' are an ']):
        return 'definition', 'literature'
    return 'background', 'literature'


def candidate_id_for(row):
    key = '|'.join([row.get('paper_id', ''), row.get('citekey', ''), row.get('source_artifact', ''), row.get('section_id', ''), row.get('quote', '')])
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]


def make_candidate(row):
    row['verified'] = 'false'
    row['candidate_id'] = candidate_id_for(row)
    return row


def build_evidence_candidates(paper_id):
    artifact_dir = ROOT/'data/parsed/grobid'/slug(paper_id, 80)
    sections_path = artifact_dir/'sections.json'
    contexts_path = artifact_dir/'citation_contexts.json'
    errors = []
    sections_data = read_json_file(sections_path, errors) if sections_path.exists() else {}
    contexts_data = read_json_file(contexts_path, errors) if contexts_path.exists() else {}
    if errors:
        raise RuntimeError('; '.join(errors))
    citekey = sections_data.get('citekey') or contexts_data.get('citekey') or ''
    created = now()
    rows = []
    cue = re.compile(r'\b(propose|present|develop|implement|framework|architecture|evaluate|result|performance|accuracy|demonstrate|validate|limitation|challenge|future work|define|called|refers to|is a|are a|built|provide|shows?)\b', re.I)
    for section in sections_data.get('sections') or []:
        heading = section.get('heading') or ''
        sentences = split_sentences(section.get('text') or '')
        for sentence in sentences:
            if not cue.search(sentence) and heading.lower() not in ['contributions.', 'scope of claims.']:
                continue
            claim_type, use_in_section = candidate_type_for(heading, sentence, 'sections.json')
            reason = f'section heading/cue heuristic: {heading or "no heading"}'
            confidence = '0.70' if claim_type in ['method', 'finding'] else '0.55'
            rows.append(make_candidate({
                'paper_id': paper_id,
                'citekey': citekey,
                'source_artifact': 'sections.json',
                'section_id': section.get('section_id') or '',
                'section_heading': heading,
                'claim_type': claim_type,
                'claim': sentence,
                'quote': sentence,
                'page': '',
                'confidence': confidence,
                'reason': reason,
                'use_in_section': use_in_section,
                'created_at': created,
            }))
    seen_quote = {r['quote'] for r in rows}
    for context in contexts_data.get('contexts') or []:
        sentence = (context.get('sentence') or '').strip()
        if not sentence or sentence in seen_quote:
            continue
        trimmed = split_sentences(sentence)
        quote = trimmed[0] if trimmed else sentence[:800]
        if quote in seen_quote:
            continue
        seen_quote.add(quote)
        rows.append(make_candidate({
            'paper_id': paper_id,
            'citekey': citekey,
            'source_artifact': 'citation_contexts.json',
            'section_id': context.get('section_id') or '',
            'section_heading': context.get('section_heading') or '',
            'claim_type': 'background',
            'claim': quote,
            'quote': quote,
            'page': context.get('page') or '',
            'confidence': '0.45',
            'reason': f"citation context heuristic; reference_id={context.get('reference_id') or ''}",
            'use_in_section': 'literature',
            'created_at': created,
        }))
    dedup = {}
    for row in rows:
        row['verified'] = 'false'
        dedup[row['candidate_id']] = row
    return list(dedup.values())


EVIDENCE_CANDIDATE_FIELDS = ['candidate_id', 'paper_id', 'citekey', 'source_artifact', 'section_id', 'section_heading', 'claim_type', 'claim', 'quote', 'page', 'confidence', 'reason', 'use_in_section', 'verified', 'created_at']
EVIDENCE_CANDIDATE_REQUIRED_HEADERS = list(EVIDENCE_CANDIDATE_FIELDS)
EVIDENCE_CANDIDATE_REQUIRED_VALUES = ['candidate_id', 'paper_id', 'citekey', 'source_artifact', 'claim', 'quote', 'confidence', 'use_in_section']
EVIDENCE_CANDIDATE_SOURCE_ARTIFACTS = {'sections.json', 'citation_contexts.json'}
EVIDENCE_CANDIDATE_CLAIM_TYPES = {'method', 'finding', 'limitation', 'definition', 'background', 'unknown'}
EVIDENCE_CANDIDATE_USE_SECTIONS = {'intro', 'literature', 'method', 'system', 'evaluation', 'discussion', 'unknown'}
EVIDENCE_REVIEW_DECISIONS = {'pending', 'include', 'exclude', 'revise', 'needs_pdf_check', 'duplicate', 'out_of_scope'}
EVIDENCE_CANDIDATE_REVIEW_FIELDS = ['candidate_id', 'paper_id', 'citekey', 'source_artifact', 'section_id', 'section_heading', 'claim_type', 'claim', 'quote', 'page', 'confidence', 'reason', 'use_in_section', 'verified', 'review_decision', 'reviewer', 'review_note', 'reviewed_at']
PROMOTION_CANDIDATE_FIELDS = ['candidate_id', 'paper_id', 'citekey', 'source_artifact', 'section_id', 'section_heading', 'claim_type', 'claim', 'quote', 'page', 'confidence', 'use_in_section', 'review_decision', 'reviewer', 'reviewed_at', 'promotion_ready', 'promotion_blockers', 'verified']
EVIDENCE_PATCH_PREVIEW_FIELDS = ['candidate_id', 'paper_id', 'citekey', 'claim_type', 'claim', 'quote', 'page', 'section', 'confidence', 'use_in_section', 'my_comment', 'verified', 'source_file', 'source_location', 'extraction_method', 'promotion_ready', 'promotion_blockers', 'review_decision', 'reviewer', 'reviewed_at', 'created_at']
EVIDENCE_MATRIX_PROMOTION_REQUIRED_COLUMNS = ['paper_id', 'citekey', 'claim_type', 'claim', 'quote', 'page', 'section', 'confidence', 'use_in_section', 'my_comment', 'verified', 'source_file', 'created_at', 'updated_at', 'evidence_id', 'exact_quote', 'source_location', 'extraction_method', 'risk_note']
PROMOTION_APPLY_PREVIEW_REQUIRED_VALUES = ['candidate_id', 'paper_id', 'citekey', 'claim_type', 'claim', 'quote', 'confidence', 'use_in_section', 'source_file', 'source_location', 'review_decision', 'reviewer', 'reviewed_at']


def evidence_matrix_metrics():
    path = ROOT/'matrices/evidence_matrix.csv'
    metrics = {
        'path': path,
        'row_count': 0,
        'sha256': '',
        'schema': [],
        'identity_set': set(),
        'exists': path.exists(),
    }
    if not path.exists():
        return metrics
    metrics['sha256'] = file_sha256(path)
    with path.open(encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        metrics['schema'] = list(reader.fieldnames or [])
        for row in reader:
            metrics['row_count'] += 1
            evidence_id = str(row.get('evidence_id') or '').strip()
            if evidence_id:
                identity = 'evidence_id:' + evidence_id
            else:
                parts = [
                    row.get('paper_id', ''),
                    row.get('citekey', ''),
                    row.get('claim_type', ''),
                    row.get('claim', ''),
                    row.get('quote') or row.get('exact_quote') or '',
                    row.get('page', ''),
                    row.get('section', ''),
                    row.get('source_file', ''),
                ]
                identity = 'row:' + hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()
            metrics['identity_set'].add(identity)
    return metrics


def compare_evidence_matrix_metrics(before, after):
    return {
        'row_count_changed': before.get('row_count') != after.get('row_count'),
        'sha256_changed': before.get('sha256') != after.get('sha256'),
        'schema_changed': before.get('schema') != after.get('schema'),
        'identity_set_changed': before.get('identity_set') != after.get('identity_set'),
    }


def append_evidence_matrix_protection_errors(errors, comparison):
    if comparison.get('row_count_changed'):
        errors.append('evidence_matrix.csv row count changed')
    if comparison.get('sha256_changed'):
        errors.append('evidence_matrix.csv sha256 changed')
    if comparison.get('schema_changed'):
        errors.append('evidence_matrix.csv schema changed')
    if comparison.get('identity_set_changed'):
        errors.append('evidence_matrix.csv identity set changed')


def evidence_matrix_existing_candidate_keys():
    keys = set()
    for row in read_evidence_for_audit():
        keys.add('|'.join([
            row.get('paper_id', ''),
            row.get('citekey', ''),
            row.get('claim', ''),
            row.get('quote') or row.get('exact_quote') or '',
        ]))
    return keys


def row_has_source_location(row):
    return any(str(row.get(field) or '').strip() for field in ['section_id', 'section_heading', 'page'])


def review_queue_rows_and_header(path):
    if not path.exists():
        return [], []
    with path.open(encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def validate_review_queue_data(paper_id=None, write_report=True):
    queue_path = ROOT/'matrices/evidence_candidate_review_queue.csv'
    before = evidence_matrix_metrics()
    warnings = []
    errors = []
    all_rows = []
    fieldnames = []
    if not queue_path.exists():
        errors.append(f'missing review queue file: {path_for_report(queue_path)}')
    else:
        try:
            all_rows, fieldnames = review_queue_rows_and_header(queue_path)
        except Exception as e:
            errors.append(f'failed to read review queue file: {e}')
    missing_headers = [h for h in EVIDENCE_CANDIDATE_REVIEW_FIELDS if h not in fieldnames]
    if missing_headers:
        errors.append('missing required headers: ' + ', '.join(missing_headers))
    rows = [r for r in all_rows if not paper_id or r.get('paper_id') == paper_id]
    if paper_id and queue_path.exists() and not rows:
        errors.append(f'no review queue rows found for paper_id={paper_id}')

    candidate_ids = [str(r.get('candidate_id') or '').strip() for r in rows]
    duplicate_ids = sorted(k for k, v in count_values(candidate_ids).items() if k and v > 1)
    duplicate_set = set(duplicate_ids)
    verified_false_count = 0
    mismatch_details = []
    missing_required_details = []
    invalid_confidence_details = []
    invalid_review_decision_details = []
    include_missing_reviewer = []
    include_missing_reviewed_at = []
    missing_source_location = []
    citation_context_only = []
    for index, row in enumerate(rows, 1):
        label = row.get('candidate_id') or f'row_{index}'
        if str(row.get('verified') or '').strip() == 'false':
            verified_false_count += 1
        for field in EVIDENCE_CANDIDATE_REQUIRED_VALUES:
            if not str(row.get(field) or '').strip():
                missing_required_details.append(f'{label}: missing {field}')
        raw_conf = str(row.get('confidence') or '').strip()
        if raw_conf:
            try:
                value = float(raw_conf)
                if value < 0.0 or value > 1.0:
                    invalid_confidence_details.append(f'{label}: confidence out of range `{raw_conf}`')
            except Exception:
                invalid_confidence_details.append(f'{label}: confidence is not numeric `{raw_conf}`')
        expected_id = candidate_id_for(row)
        actual_id = str(row.get('candidate_id') or '').strip()
        if actual_id != expected_id:
            mismatch_details.append(f'{label}: expected `{expected_id}`, found `{actual_id or "(blank)"}`')
        decision = str(row.get('review_decision') or '').strip()
        if decision not in EVIDENCE_REVIEW_DECISIONS:
            invalid_review_decision_details.append(f'{label}: invalid review_decision `{decision}`')
        if decision == 'include':
            if not str(row.get('reviewer') or '').strip():
                include_missing_reviewer.append(label)
            if not str(row.get('reviewed_at') or '').strip():
                include_missing_reviewed_at.append(label)
        if not row_has_source_location(row):
            missing_source_location.append(label)
        if str(row.get('source_artifact') or '').strip() == 'citation_contexts.json':
            citation_context_only.append(label)

    if rows and verified_false_count != len(rows):
        errors.append(f'verified must be string false for all rows: {verified_false_count}/{len(rows)}')
    if duplicate_ids:
        errors.append(f'duplicate candidate_id values: {len(duplicate_ids)}')
        warnings.extend([f'duplicate candidate_id: {cid}' for cid in duplicate_ids[:50]])
    if mismatch_details:
        errors.append(f'candidate_id deterministic hash mismatches: {len(mismatch_details)}')
        warnings.extend(mismatch_details[:50])
    if missing_required_details:
        errors.append(f'missing required values: {len(missing_required_details)}')
        warnings.extend(missing_required_details[:50])
    if invalid_confidence_details:
        errors.append(f'invalid confidence values: {len(invalid_confidence_details)}')
        warnings.extend(invalid_confidence_details[:50])
    if invalid_review_decision_details:
        errors.append(f'invalid review_decision values: {len(invalid_review_decision_details)}')
        warnings.extend(invalid_review_decision_details[:50])
    if include_missing_reviewer:
        warnings.extend([f'include row missing reviewer: {cid}' for cid in include_missing_reviewer[:50]])
    if include_missing_reviewed_at:
        warnings.extend([f'include row missing reviewed_at: {cid}' for cid in include_missing_reviewed_at[:50]])
    if missing_source_location:
        warnings.extend([f'missing source location promotion blocker candidate: {cid}' for cid in missing_source_location[:50]])
    if citation_context_only:
        warnings.extend([f'citation_contexts.json promotion blocker candidate: {cid}' for cid in citation_context_only[:50]])

    after = evidence_matrix_metrics()
    comparison = compare_evidence_matrix_metrics(before, after)
    append_evidence_matrix_protection_errors(errors, comparison)
    metrics = {
        'queue_file': path_for_report(queue_path),
        'paper_id_filter': paper_id or '(none)',
        'total_queue_rows': len(all_rows),
        'queue_count': len(rows),
        'verified_false_count': verified_false_count,
        'review_decision_distribution': distribution_for(rows, 'review_decision'),
        'candidate_id_duplicate_count': len(duplicate_ids),
        'candidate_id_mismatch_count': len(mismatch_details),
        'invalid_review_decision_count': len(invalid_review_decision_details),
        'include_missing_reviewer_count': len(include_missing_reviewer),
        'include_missing_reviewed_at_count': len(include_missing_reviewed_at),
        'missing_source_location_count': len(missing_source_location),
        'citation_context_only_count': len(citation_context_only),
        'evidence_matrix_row_count_before': before['row_count'],
        'evidence_matrix_row_count_after': after['row_count'],
        'evidence_matrix_sha256_before': before['sha256'],
        'evidence_matrix_sha256_after': after['sha256'],
        'evidence_matrix_schema_changed': comparison['schema_changed'],
        'evidence_matrix_identity_set_changed': comparison['identity_set_changed'],
        'warnings': warnings,
        'errors': errors,
        'rows': rows,
        'duplicate_candidate_ids': duplicate_set,
        'report': '',
    }
    if write_report:
        report = write_review_queue_validation_report(metrics)
        metrics['report'] = path_for_report(report)
    return metrics


def write_review_queue_validation_report(metrics):
    out = ROOT/f'reports/review/review_queue_validation_{today()}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# Review Queue Validation {today()}\n', '## Summary\n']
    for key in [
        'paper_id_filter', 'queue_count', 'verified_false_count',
        'candidate_id_duplicate_count', 'candidate_id_mismatch_count',
        'invalid_review_decision_count', 'include_missing_reviewer_count',
        'include_missing_reviewed_at_count', 'missing_source_location_count',
        'citation_context_only_count', 'evidence_matrix_row_count_before',
        'evidence_matrix_row_count_after', 'evidence_matrix_sha256_before',
        'evidence_matrix_sha256_after', 'evidence_matrix_schema_changed',
        'evidence_matrix_identity_set_changed',
    ]:
        lines.append(f'- {key}: {metrics.get(key)}\n')
    lines.append('\n## Review Decision Distribution\n')
    for value, count in metrics.get('review_decision_distribution', {}).items():
        lines.append(f'- {value}: {count}\n')
    lines.append('\n## Warnings\n')
    lines.extend([f"- {w}\n" for w in metrics.get('warnings', [])] or ['- none\n'])
    lines.append('\n## Errors\n')
    lines.extend([f"- {e}\n" for e in metrics.get('errors', [])] or ['- none\n'])
    out.write_text(''.join(lines), encoding='utf-8')
    return out


def cmd_validate_review_queue(args):
    metrics = validate_review_queue_data(getattr(args, 'paper_id', None), write_report=True)
    if metrics.get('report'):
        print(ROOT/metrics['report'])
    for key in [
        'queue_count', 'verified_false_count', 'candidate_id_duplicate_count',
        'candidate_id_mismatch_count', 'invalid_review_decision_count',
        'include_missing_reviewer_count', 'include_missing_reviewed_at_count',
        'missing_source_location_count', 'citation_context_only_count',
        'evidence_matrix_row_count_before', 'evidence_matrix_row_count_after',
        'evidence_matrix_sha256_before', 'evidence_matrix_sha256_after',
        'evidence_matrix_schema_changed', 'evidence_matrix_identity_set_changed',
    ]:
        print(f"{key}={metrics.get(key)}")
    log(f"validate-review-queue 실행: paper_id={getattr(args, 'paper_id', None) or '(none)'}, rows={metrics['queue_count']}, errors={len(metrics['errors'])}")
    if metrics['errors']:
        raise SystemExit(1)


def read_evidence_candidates(path):
    if not path.exists():
        return []
    with path.open(encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def write_evidence_candidates(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=EVIDENCE_CANDIDATE_FIELDS)
        writer.writeheader()
        for row in rows:
            row = {field: row.get(field, '') for field in EVIDENCE_CANDIDATE_FIELDS}
            row['verified'] = 'false'
            writer.writerow(row)


def distribution_for(rows, field):
    counts = defaultdict(int)
    for row in rows:
        value = str(row.get(field) or '').strip() or '(blank)'
        counts[value] += 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def confidence_value(row, default=-1.0):
    try:
        return float(str(row.get('confidence') or '').strip())
    except Exception:
        return default


def write_evidence_candidate_validation_report(metrics):
    out = ROOT/f'reports/audit_reports/evidence_candidate_validation_{today()}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# Evidence Candidate Validation {today()}\n', '## Summary\n']
    for key in [
        'candidate_file', 'paper_id_filter', 'total_candidate_rows', 'candidate_count',
        'verified_false_count', 'candidate_id_duplicate_count', 'candidate_id_mismatch_count',
        'missing_required_value_count', 'invalid_confidence_count', 'invalid_enum_count',
        'evidence_matrix_row_count_before', 'evidence_matrix_row_count_after',
    ]:
        lines.append(f'- {key}: {metrics.get(key)}\n')
    lines.append('\n## Source Artifact Distribution\n')
    for value, count in metrics.get('source_artifact_distribution', {}).items():
        lines.append(f'- {value}: {count}\n')
    lines.append('\n## Claim Type Distribution\n')
    for value, count in metrics.get('claim_type_distribution', {}).items():
        lines.append(f'- {value}: {count}\n')
    lines.append('\n## Use In Section Distribution\n')
    for value, count in metrics.get('use_in_section_distribution', {}).items():
        lines.append(f'- {value}: {count}\n')
    lines.append('\n## Warnings\n')
    lines.extend([f"- {w}\n" for w in metrics.get('warnings', [])] or ['- none\n'])
    lines.append('\n## Errors\n')
    lines.extend([f"- {e}\n" for e in metrics.get('errors', [])] or ['- none\n'])
    out.write_text(''.join(lines), encoding='utf-8')
    return out


def validate_evidence_candidate_data(paper_id=None, write_report=True):
    candidate_path = ROOT/'matrices/evidence_candidates.csv'
    evidence_path = ROOT/'matrices/evidence_matrix.csv'
    before = file_row_count(evidence_path)
    warnings = []
    errors = []
    fieldnames = []
    all_rows = []
    if not candidate_path.exists():
        errors.append(f'missing candidate file: {path_for_report(candidate_path)}')
    else:
        try:
            with candidate_path.open(encoding='utf-8', newline='') as f:
                reader = csv.DictReader(f)
                fieldnames = list(reader.fieldnames or [])
                all_rows = list(reader)
        except Exception as e:
            errors.append(f'failed to read candidate file: {e}')
    missing_headers = [h for h in EVIDENCE_CANDIDATE_REQUIRED_HEADERS if h not in fieldnames]
    if missing_headers:
        errors.append('missing required headers: ' + ', '.join(missing_headers))
    rows = [r for r in all_rows if not paper_id or r.get('paper_id') == paper_id]
    if paper_id and candidate_path.exists() and not rows:
        errors.append(f'no evidence candidates found for paper_id={paper_id}')

    missing_required_details = []
    invalid_confidence_details = []
    invalid_enum_details = []
    mismatch_details = []
    for index, row in enumerate(rows, 1):
        label = row.get('candidate_id') or f'row_{index}'
        for field in EVIDENCE_CANDIDATE_REQUIRED_VALUES:
            if not str(row.get(field) or '').strip():
                missing_required_details.append(f'{label}: missing {field}')
        raw_conf = str(row.get('confidence') or '').strip()
        if raw_conf:
            try:
                value = float(raw_conf)
                if value < 0.0 or value > 1.0:
                    invalid_confidence_details.append(f'{label}: confidence out of range `{raw_conf}`')
            except Exception:
                invalid_confidence_details.append(f'{label}: confidence is not numeric `{raw_conf}`')
        source_artifact = str(row.get('source_artifact') or '').strip()
        if source_artifact and source_artifact not in EVIDENCE_CANDIDATE_SOURCE_ARTIFACTS:
            invalid_enum_details.append(f'{label}: invalid source_artifact `{source_artifact}`')
        claim_type = str(row.get('claim_type') or '').strip()
        if claim_type and claim_type not in EVIDENCE_CANDIDATE_CLAIM_TYPES:
            invalid_enum_details.append(f'{label}: invalid claim_type `{claim_type}`')
        use_in_section = str(row.get('use_in_section') or '').strip()
        if use_in_section and use_in_section not in EVIDENCE_CANDIDATE_USE_SECTIONS:
            invalid_enum_details.append(f'{label}: invalid use_in_section `{use_in_section}`')
        expected_id = candidate_id_for(row)
        actual_id = str(row.get('candidate_id') or '').strip()
        if actual_id != expected_id:
            mismatch_details.append(f'{label}: expected `{expected_id}`, found `{actual_id or "(blank)"}`')

    candidate_ids = [str(r.get('candidate_id') or '').strip() for r in rows]
    duplicate_ids = sorted(k for k, v in count_values(candidate_ids).items() if k and v > 1)
    verified_false_count = sum(1 for r in rows if str(r.get('verified') or '').strip().lower() == 'false')
    if rows and verified_false_count != len(rows):
        errors.append(f'verified must be string false for all rows: {verified_false_count}/{len(rows)}')
    if duplicate_ids:
        errors.append(f'duplicate candidate_id values: {len(duplicate_ids)}')
        warnings.extend([f'duplicate candidate_id: {cid}' for cid in duplicate_ids[:50]])
    if mismatch_details:
        errors.append(f'candidate_id deterministic hash mismatches: {len(mismatch_details)}')
        warnings.extend(mismatch_details[:50])
        if len(mismatch_details) > 50:
            warnings.append(f'candidate_id mismatch details truncated: {len(mismatch_details) - 50} more')
    if missing_required_details:
        errors.append(f'missing required values: {len(missing_required_details)}')
        warnings.extend(missing_required_details[:50])
        if len(missing_required_details) > 50:
            warnings.append(f'missing required value details truncated: {len(missing_required_details) - 50} more')
    if invalid_confidence_details:
        errors.append(f'invalid confidence values: {len(invalid_confidence_details)}')
        warnings.extend(invalid_confidence_details[:50])
    if invalid_enum_details:
        errors.append(f'invalid enum values: {len(invalid_enum_details)}')
        warnings.extend(invalid_enum_details[:50])

    after = file_row_count(evidence_path)
    if before != after:
        errors.append('evidence_matrix.csv row count changed during validation')
    metrics = {
        'candidate_file': path_for_report(candidate_path),
        'paper_id_filter': paper_id or '(none)',
        'total_candidate_rows': len(all_rows),
        'candidate_count': len(rows),
        'verified_false_count': verified_false_count,
        'candidate_id_duplicate_count': len(duplicate_ids),
        'candidate_id_mismatch_count': len(mismatch_details),
        'missing_required_value_count': len(missing_required_details),
        'invalid_confidence_count': len(invalid_confidence_details),
        'invalid_enum_count': len(invalid_enum_details),
        'evidence_matrix_row_count_before': before,
        'evidence_matrix_row_count_after': after,
        'source_artifact_distribution': distribution_for(rows, 'source_artifact'),
        'claim_type_distribution': distribution_for(rows, 'claim_type'),
        'use_in_section_distribution': distribution_for(rows, 'use_in_section'),
        'warnings': warnings,
        'errors': errors,
        'rows': rows,
        'report': '',
    }
    if write_report:
        report = write_evidence_candidate_validation_report(metrics)
        metrics['report'] = path_for_report(report)
    return metrics


def cmd_validate_evidence_candidates(args):
    metrics = validate_evidence_candidate_data(getattr(args, 'paper_id', None), write_report=True)
    if metrics.get('report'):
        print(ROOT/metrics['report'])
    print(f"candidate_count={metrics['candidate_count']}")
    print(f"verified_false_count={metrics['verified_false_count']}")
    print(f"candidate_id_duplicate_count={metrics['candidate_id_duplicate_count']}")
    print(f"candidate_id_mismatch_count={metrics['candidate_id_mismatch_count']}")
    print(f"evidence_matrix_row_count_before={metrics['evidence_matrix_row_count_before']}")
    print(f"evidence_matrix_row_count_after={metrics['evidence_matrix_row_count_after']}")
    log(f"validate-evidence-candidates 실행: paper_id={getattr(args, 'paper_id', None) or '(none)'}, candidates={metrics['candidate_count']}, errors={len(metrics['errors'])}")
    if metrics['errors']:
        raise SystemExit(1)


def read_evidence_candidate_review_queue(path):
    if not path.exists():
        return []
    with path.open(encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def write_evidence_candidate_review_queue(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=EVIDENCE_CANDIDATE_REVIEW_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, '') for field in EVIDENCE_CANDIDATE_REVIEW_FIELDS})


def preserved_review_values(existing_rows, warnings):
    preserved = {}
    seen = set()
    for row in existing_rows:
        candidate_id = str(row.get('candidate_id') or '').strip()
        if not candidate_id:
            continue
        if candidate_id in seen:
            warnings.append(f'duplicate candidate_id in existing review queue ignored by last-write-wins: {candidate_id}')
        seen.add(candidate_id)
        decision = str(row.get('review_decision') or '').strip() or 'pending'
        if decision not in EVIDENCE_REVIEW_DECISIONS:
            warnings.append(f'invalid existing review_decision for {candidate_id}: `{decision}` reset to pending')
            decision = 'pending'
        preserved[candidate_id] = {
            'review_decision': decision,
            'reviewer': row.get('reviewer') or '',
            'review_note': row.get('review_note') or '',
            'reviewed_at': row.get('reviewed_at') or '',
        }
    return preserved


def review_row_from_candidate(candidate, preserved):
    candidate_id = str(candidate.get('candidate_id') or '').strip()
    keep = preserved.get(candidate_id) or {}
    row = {field: candidate.get(field, '') for field in EVIDENCE_CANDIDATE_REVIEW_FIELDS}
    row['verified'] = 'false'
    row['review_decision'] = keep.get('review_decision') or 'pending'
    row['reviewer'] = keep.get('reviewer') or ''
    row['review_note'] = keep.get('review_note') or ''
    row['reviewed_at'] = keep.get('reviewed_at') or ''
    return row


def write_evidence_candidate_review_report(result):
    out = ROOT/f'reports/review/evidence_candidate_review_{today()}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# Evidence Candidate Review {today()}\n', '## Summary\n']
    for key in [
        'paper_id', 'citekey', 'candidate_count_total', 'queue_count', 'queue_csv',
        'verified_false_count', 'candidate_id_duplicate_count',
        'evidence_matrix_row_count_before', 'evidence_matrix_row_count_after',
    ]:
        lines.append(f'- {key}: {result.get(key)}\n')
    lines.append('\n## Filters\n')
    for key, value in (result.get('filters') or {}).items():
        lines.append(f'- {key}: {value if value not in (None, "") else "(none)"}\n')
    lines.append('\n## Source Artifact Distribution\n')
    for value, count in result.get('source_artifact_distribution', {}).items():
        lines.append(f'- {value}: {count}\n')
    lines.append('\n## Claim Type Distribution\n')
    for value, count in result.get('claim_type_distribution', {}).items():
        lines.append(f'- {value}: {count}\n')
    lines.append('\n## Use In Section Distribution\n')
    for value, count in result.get('use_in_section_distribution', {}).items():
        lines.append(f'- {value}: {count}\n')
    lines.append('\n## Review Decision Distribution\n')
    for value, count in result.get('review_decision_distribution', {}).items():
        lines.append(f'- {value}: {count}\n')
    lines.append('\n## Top Candidates By Confidence\n')
    for row in result.get('top_candidates', []):
        lines.append(f"- `{row.get('candidate_id')}` {row.get('confidence')} {row.get('claim_type')} / {row.get('quote', '')[:180]}\n")
    if not result.get('top_candidates'):
        lines.append('- none\n')
    lines.append('\n## Low Confidence Candidates\n')
    for row in result.get('low_confidence_candidates', []):
        lines.append(f"- `{row.get('candidate_id')}` {row.get('confidence')} {row.get('claim_type')} / {row.get('quote', '')[:180]}\n")
    if not result.get('low_confidence_candidates'):
        lines.append('- none\n')
    lines.append('\n## Warnings\n')
    lines.extend([f"- {w}\n" for w in result.get('warnings', [])] or ['- none\n'])
    lines.append('\n## Errors\n')
    lines.extend([f"- {e}\n" for e in result.get('errors', [])] or ['- none\n'])
    out.write_text(''.join(lines), encoding='utf-8')
    return out


def cmd_review_evidence_candidates(args):
    if args.min_confidence is not None and (args.min_confidence < 0.0 or args.min_confidence > 1.0):
        raise SystemExit('ERROR: --min-confidence must be between 0.0 and 1.0')
    validation = validate_evidence_candidate_data(args.paper_id, write_report=True)
    if validation.get('report'):
        print(ROOT/validation['report'])
    if validation['errors']:
        log(f"review-evidence-candidates 중단: validation_errors={len(validation['errors'])}, paper_id={args.paper_id}")
        raise SystemExit(1)
    before = file_row_count(ROOT/'matrices/evidence_matrix.csv')
    rows = list(validation['rows'])
    if args.min_confidence is not None:
        rows = [r for r in rows if confidence_value(r) >= args.min_confidence]
    if args.use_in_section:
        rows = [r for r in rows if str(r.get('use_in_section') or '').strip() == args.use_in_section]
    if args.claim_type:
        rows = [r for r in rows if str(r.get('claim_type') or '').strip() == args.claim_type]
    rows = sorted(rows, key=lambda r: (-confidence_value(r), str(r.get('claim_type') or ''), str(r.get('candidate_id') or '')))

    queue_path = ROOT/'matrices/evidence_candidate_review_queue.csv'
    warnings = []
    errors = []
    existing_rows = read_evidence_candidate_review_queue(queue_path)
    preserved = preserved_review_values(existing_rows, warnings)
    queue_rows = [review_row_from_candidate(r, preserved) for r in rows]
    duplicate_ids = sorted(k for k, v in count_values([r.get('candidate_id') for r in queue_rows]).items() if k and v > 1)
    if duplicate_ids:
        errors.append(f'duplicate candidate_id values in review queue: {len(duplicate_ids)}')
    verified_false_count = sum(1 for r in queue_rows if str(r.get('verified') or '').strip().lower() == 'false')
    if verified_false_count != len(queue_rows):
        errors.append(f'review queue verified must remain false: {verified_false_count}/{len(queue_rows)}')
    if not errors:
        write_evidence_candidate_review_queue(queue_path, queue_rows)
    after = file_row_count(ROOT/'matrices/evidence_matrix.csv')
    if before != after:
        errors.append('evidence_matrix.csv row count changed during review queue generation')
    result = {
        'paper_id': args.paper_id,
        'citekey': ', '.join(sorted({r.get('citekey') for r in queue_rows if r.get('citekey')})) or '(none)',
        'candidate_count_total': validation['candidate_count'],
        'queue_count': len(queue_rows),
        'queue_csv': path_for_report(queue_path),
        'verified_false_count': verified_false_count,
        'candidate_id_duplicate_count': len(duplicate_ids),
        'evidence_matrix_row_count_before': before,
        'evidence_matrix_row_count_after': after,
        'filters': {
            'paper_id': args.paper_id,
            'min_confidence': args.min_confidence,
            'use_in_section': args.use_in_section,
            'claim_type': args.claim_type,
        },
        'source_artifact_distribution': distribution_for(queue_rows, 'source_artifact'),
        'claim_type_distribution': distribution_for(queue_rows, 'claim_type'),
        'use_in_section_distribution': distribution_for(queue_rows, 'use_in_section'),
        'review_decision_distribution': distribution_for(queue_rows, 'review_decision'),
        'top_candidates': queue_rows[:10],
        'low_confidence_candidates': sorted(queue_rows, key=lambda r: (confidence_value(r), str(r.get('candidate_id') or '')))[:10],
        'warnings': warnings,
        'errors': errors,
    }
    report = write_evidence_candidate_review_report(result)
    print(report)
    print(f"queue_count={len(queue_rows)}")
    print(f"verified_false_count={verified_false_count}")
    print(f"candidate_id_duplicate_count={len(duplicate_ids)}")
    print(f"evidence_matrix_row_count_before={before}")
    print(f"evidence_matrix_row_count_after={after}")
    log(f"review-evidence-candidates 실행: paper_id={args.paper_id}, queue_count={len(queue_rows)}, errors={len(errors)}, report={report.name}")
    if errors:
        raise SystemExit(1)


def write_review_preservation_report(result):
    out = ROOT/f'reports/review/review_preservation_test_{today()}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# Review Preservation Test {today()}\n', '## Summary\n']
    for key in [
        'paper_id', 'selected_candidate_count', 'preserved_candidate_count',
        'evidence_matrix_row_count_before', 'evidence_matrix_row_count_after',
        'evidence_matrix_sha256_before', 'evidence_matrix_sha256_after',
        'evidence_matrix_schema_changed', 'evidence_matrix_identity_set_changed',
        'passed',
    ]:
        lines.append(f'- {key}: {result.get(key)}\n')
    lines.append('\n## Selected Candidates\n')
    lines.extend([f"- `{cid}`\n" for cid in result.get('selected_candidate_ids', [])] or ['- none\n'])
    lines.append('\n## Warnings\n')
    lines.extend([f"- {w}\n" for w in result.get('warnings', [])] or ['- none\n'])
    lines.append('\n## Errors\n')
    lines.extend([f"- {e}\n" for e in result.get('errors', [])] or ['- none\n'])
    out.write_text(''.join(lines), encoding='utf-8')
    return out


def cmd_test_review_preservation(args):
    queue_path = ROOT/'matrices/evidence_candidate_review_queue.csv'
    if not queue_path.exists():
        raise SystemExit(f'ERROR: missing review queue file: {queue_path}')
    original_bytes = queue_path.read_bytes()
    before = evidence_matrix_metrics()
    warnings = []
    errors = []
    selected_ids = []
    preserved_count = 0
    try:
        rows = read_evidence_candidate_review_queue(queue_path)
        target_rows = [r for r in rows if r.get('paper_id') == args.paper_id][:2]
        selected_ids = [r.get('candidate_id') for r in target_rows]
        if len(target_rows) != 2:
            errors.append(f'expected 2 candidates for preservation test, found {len(target_rows)}')
        else:
            reviewed_at = now()
            decisions = ['include', 'needs_pdf_check']
            expected = {}
            for row, decision in zip(target_rows, decisions):
                row['review_decision'] = decision
                row['reviewer'] = 'preservation_test'
                row['review_note'] = 'manual field preservation regression test'
                row['reviewed_at'] = reviewed_at
                expected[row['candidate_id']] = {
                    'review_decision': row['review_decision'],
                    'reviewer': row['reviewer'],
                    'review_note': row['review_note'],
                    'reviewed_at': row['reviewed_at'],
                }
            by_id = {r.get('candidate_id'): r for r in rows}
            for target in target_rows:
                by_id[target.get('candidate_id')] = target
            write_evidence_candidate_review_queue(queue_path, rows)
            cmd_review_evidence_candidates(argparse.Namespace(
                paper_id=args.paper_id,
                min_confidence=None,
                use_in_section=None,
                claim_type=None,
            ))
            regenerated = {r.get('candidate_id'): r for r in read_evidence_candidate_review_queue(queue_path)}
            for candidate_id, expected_values in expected.items():
                row = regenerated.get(candidate_id) or {}
                if all(row.get(k) == v for k, v in expected_values.items()):
                    preserved_count += 1
                else:
                    errors.append(f'review fields were not preserved for candidate_id={candidate_id}')
    finally:
        queue_path.write_bytes(original_bytes)
    after = evidence_matrix_metrics()
    comparison = compare_evidence_matrix_metrics(before, after)
    append_evidence_matrix_protection_errors(errors, comparison)
    passed = len(selected_ids) == 2 and preserved_count == 2 and not comparison['sha256_changed'] and not errors
    result = {
        'paper_id': args.paper_id,
        'selected_candidate_count': len(selected_ids),
        'preserved_candidate_count': preserved_count,
        'selected_candidate_ids': selected_ids,
        'evidence_matrix_row_count_before': before['row_count'],
        'evidence_matrix_row_count_after': after['row_count'],
        'evidence_matrix_sha256_before': before['sha256'],
        'evidence_matrix_sha256_after': after['sha256'],
        'evidence_matrix_schema_changed': comparison['schema_changed'],
        'evidence_matrix_identity_set_changed': comparison['identity_set_changed'],
        'passed': str(passed).lower(),
        'warnings': warnings,
        'errors': errors,
    }
    report = write_review_preservation_report(result)
    print(report)
    print(f"selected_candidate_count={len(selected_ids)}")
    print(f"preserved_candidate_count={preserved_count}")
    print(f"evidence_matrix_sha256_before={before['sha256']}")
    print(f"evidence_matrix_sha256_after={after['sha256']}")
    log(f"test-review-preservation 실행: paper_id={args.paper_id}, preserved={preserved_count}/{len(selected_ids)}, errors={len(errors)}")
    if errors or not passed:
        raise SystemExit(1)


def canonical_bib_citekeys():
    paths = [ROOT/'05_manuscript/references.bib', ROOT/'manuscript/references.bib']
    keys = set()
    for path in paths:
        keys |= parse_bib_citekeys(path)
    return keys


def promotion_blockers_for_row(row, duplicate_candidate_ids, bib_keys, existing_keys):
    blockers = []
    if not str(row.get('reviewer') or '').strip():
        blockers.append('missing_reviewer')
    if not str(row.get('reviewed_at') or '').strip():
        blockers.append('missing_reviewed_at')
    if not str(row.get('quote') or '').strip():
        blockers.append('missing_quote')
    if not str(row.get('claim') or '').strip():
        blockers.append('missing_claim')
    if not str(row.get('citekey') or '').strip():
        blockers.append('missing_citekey')
    if not str(row.get('paper_id') or '').strip():
        blockers.append('missing_paper_id')
    if not row_has_source_location(row):
        blockers.append('missing_source_location')
    if str(row.get('source_artifact') or '').strip() == 'citation_contexts.json':
        blockers.append('citation_context_only')
    citekey_value = str(row.get('citekey') or '').strip()
    if citekey_value and citekey_value not in bib_keys:
        blockers.append('citekey_not_found')
    if confidence_value(row, default=-1.0) < 0.55:
        blockers.append('low_confidence')
    if str(row.get('review_decision') or '').strip() not in EVIDENCE_REVIEW_DECISIONS:
        blockers.append('invalid_review_decision')
    if str(row.get('candidate_id') or '').strip() != candidate_id_for(row):
        blockers.append('candidate_id_mismatch')
    if str(row.get('candidate_id') or '').strip() in duplicate_candidate_ids:
        blockers.append('candidate_id_duplicate')
    existing_key = '|'.join([
        row.get('paper_id', ''),
        row.get('citekey', ''),
        row.get('claim', ''),
        row.get('quote', ''),
    ])
    if existing_key in existing_keys:
        blockers.append('already_in_evidence_matrix')
    return blockers


def source_location_for_patch(row):
    page = str(row.get('page') or '').strip()
    if page:
        return page
    quote_hash = hashlib.sha256(str(row.get('quote') or '').encode('utf-8')).hexdigest()[:12]
    return f"section_id={row.get('section_id') or ''}; section_heading={row.get('section_heading') or ''}; quote_sha256={quote_hash}"


def write_promotion_plan_report(result):
    out = ROOT/f'reports/promotion/promotion_plan_{today()}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# Promotion Plan Dry Run {today()}\n', '## Summary\n']
    for key in [
        'paper_id_filter', 'candidate_id_filter', 'queue_count',
        'include_candidate_count', 'promotion_ready_count', 'blocked_count',
        'evidence_matrix_row_count_before', 'evidence_matrix_row_count_after',
        'evidence_matrix_sha256_before', 'evidence_matrix_sha256_after',
        'evidence_matrix_schema_changed', 'evidence_matrix_identity_set_changed',
    ]:
        lines.append(f'- {key}: {result.get(key)}\n')
    lines.append('\n## Output CSVs\n')
    for path in result.get('output_csvs', []):
        lines.append(f'- {path}\n')
    lines.append('\n## Blocker Distribution\n')
    for value, count in result.get('blocker_distribution', {}).items():
        lines.append(f'- {value}: {count}\n')
    lines.append('\n## Warnings\n')
    lines.extend([f"- {w}\n" for w in result.get('warnings', [])] or ['- none\n'])
    lines.append('\n## Errors\n')
    lines.extend([f"- {e}\n" for e in result.get('errors', [])] or ['- none\n'])
    out.write_text(''.join(lines), encoding='utf-8')
    return out


def cmd_promotion_plan(args):
    if not args.dry_run:
        raise SystemExit('ERROR: promotion-plan requires --dry-run. Apply promotion is not implemented.')
    if args.paper_id and args.candidate_id:
        raise SystemExit('ERROR: --paper-id and --candidate-id cannot be used together')
    before = evidence_matrix_metrics()
    validation = validate_review_queue_data(args.paper_id, write_report=True)
    errors = list(validation['errors'])
    warnings = list(validation['warnings'])
    rows = list(validation['rows'])
    if args.candidate_id:
        rows = [r for r in rows if str(r.get('candidate_id') or '').strip() == args.candidate_id]
        if not rows:
            errors.append(f'no review queue row found for candidate_id={args.candidate_id}')
    queue_count = len(rows)
    include_rows = [r for r in rows if str(r.get('review_decision') or '').strip() == 'include']
    bib_keys = canonical_bib_citekeys()
    existing_keys = evidence_matrix_existing_candidate_keys()
    duplicate_ids = validation.get('duplicate_candidate_ids') or set()
    promotion_rows = []
    patch_rows = []
    blocker_counts = defaultdict(int)
    created_at = now()
    for row in include_rows:
        blockers = promotion_blockers_for_row(row, duplicate_ids, bib_keys, existing_keys)
        for blocker in blockers:
            blocker_counts[blocker] += 1
        promotion_ready = not blockers
        blocker_text = ';'.join(blockers)
        promotion_rows.append({
            'candidate_id': row.get('candidate_id', ''),
            'paper_id': row.get('paper_id', ''),
            'citekey': row.get('citekey', ''),
            'source_artifact': row.get('source_artifact', ''),
            'section_id': row.get('section_id', ''),
            'section_heading': row.get('section_heading', ''),
            'claim_type': row.get('claim_type', ''),
            'claim': row.get('claim', ''),
            'quote': row.get('quote', ''),
            'page': row.get('page', ''),
            'confidence': row.get('confidence', ''),
            'use_in_section': row.get('use_in_section', ''),
            'review_decision': row.get('review_decision', ''),
            'reviewer': row.get('reviewer', ''),
            'reviewed_at': row.get('reviewed_at', ''),
            'promotion_ready': str(promotion_ready).lower(),
            'promotion_blockers': blocker_text,
            'verified': 'false',
        })
        patch_rows.append({
            'candidate_id': row.get('candidate_id', ''),
            'paper_id': row.get('paper_id', ''),
            'citekey': row.get('citekey', ''),
            'claim_type': row.get('claim_type', ''),
            'claim': row.get('claim', ''),
            'quote': row.get('quote', ''),
            'page': row.get('page', ''),
            'section': row.get('section_heading') or row.get('section_id') or '',
            'confidence': row.get('confidence', ''),
            'use_in_section': row.get('use_in_section', ''),
            'my_comment': 'promotion dry-run only; verify original PDF before verified=true',
            'verified': 'false',
            'source_file': row.get('source_artifact', ''),
            'source_location': source_location_for_patch(row),
            'extraction_method': 'candidate_review_dry_run',
            'promotion_ready': str(promotion_ready).lower(),
            'promotion_blockers': blocker_text,
            'review_decision': row.get('review_decision', ''),
            'reviewer': row.get('reviewer', ''),
            'reviewed_at': row.get('reviewed_at', ''),
            'created_at': created_at,
        })
    out_candidates = ROOT/'matrices/evidence_promotion_candidates.csv'
    out_patch = ROOT/'matrices/evidence_matrix_patch_preview.csv'
    out_candidates.parent.mkdir(parents=True, exist_ok=True)
    with out_candidates.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=PROMOTION_CANDIDATE_FIELDS)
        writer.writeheader()
        writer.writerows(promotion_rows)
    with out_patch.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=EVIDENCE_PATCH_PREVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(patch_rows)
    after = evidence_matrix_metrics()
    comparison = compare_evidence_matrix_metrics(before, after)
    append_evidence_matrix_protection_errors(errors, comparison)
    result = {
        'paper_id_filter': args.paper_id or '(none)',
        'candidate_id_filter': args.candidate_id or '(none)',
        'queue_count': queue_count,
        'include_candidate_count': len(include_rows),
        'promotion_ready_count': sum(1 for r in promotion_rows if r.get('promotion_ready') == 'true'),
        'blocked_count': sum(1 for r in promotion_rows if r.get('promotion_ready') != 'true'),
        'blocker_distribution': dict(sorted(blocker_counts.items())),
        'output_csvs': [path_for_report(out_candidates), path_for_report(out_patch)],
        'evidence_matrix_row_count_before': before['row_count'],
        'evidence_matrix_row_count_after': after['row_count'],
        'evidence_matrix_sha256_before': before['sha256'],
        'evidence_matrix_sha256_after': after['sha256'],
        'evidence_matrix_schema_changed': comparison['schema_changed'],
        'evidence_matrix_identity_set_changed': comparison['identity_set_changed'],
        'warnings': warnings,
        'errors': errors,
    }
    report = write_promotion_plan_report(result)
    print(report)
    for key in [
        'queue_count', 'include_candidate_count', 'promotion_ready_count',
        'blocked_count', 'evidence_matrix_row_count_before',
        'evidence_matrix_row_count_after', 'evidence_matrix_sha256_before',
        'evidence_matrix_sha256_after', 'evidence_matrix_schema_changed',
        'evidence_matrix_identity_set_changed',
    ]:
        print(f"{key}={result.get(key)}")
    print(f"output_csv={out_candidates}")
    print(f"output_csv={out_patch}")
    log(f"promotion-plan dry-run 실행: paper_id={args.paper_id or '(none)'}, candidate_id={args.candidate_id or '(none)'}, include={len(include_rows)}, ready={result['promotion_ready_count']}, errors={len(errors)}")
    if errors:
        raise SystemExit(1)


def resolve_root_path(path_text):
    path = Path(path_text)
    if path.is_absolute():
        return path
    return ROOT / path


def read_csv_rows_with_header(path):
    if not path.exists():
        return [], []
    with path.open(encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def read_evidence_matrix_with_header():
    path = ROOT/'matrices/evidence_matrix.csv'
    with path.open(encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def evidence_key_from_row(row):
    return '|'.join([
        row.get('paper_id', ''),
        row.get('citekey', ''),
        row.get('claim', ''),
        row.get('quote') or row.get('exact_quote') or '',
    ])


def preview_row_required_value_errors(row):
    label = row.get('candidate_id') or '(blank candidate_id)'
    errors = []
    for field in PROMOTION_APPLY_PREVIEW_REQUIRED_VALUES:
        if not str(row.get(field) or '').strip():
            errors.append(f'{label}: missing preview value `{field}`')
    try:
        conf = float(str(row.get('confidence') or '').strip())
        if conf < 0.0 or conf > 1.0:
            errors.append(f'{label}: confidence out of range `{row.get("confidence")}`')
    except Exception:
        errors.append(f'{label}: confidence is not numeric `{row.get("confidence")}`')
    return errors


def preview_queue_mismatch_errors(preview, queue_row):
    label = preview.get('candidate_id') or '(blank candidate_id)'
    errors = []
    field_pairs = [
        ('paper_id', 'paper_id'),
        ('citekey', 'citekey'),
        ('claim_type', 'claim_type'),
        ('claim', 'claim'),
        ('quote', 'quote'),
        ('page', 'page'),
        ('confidence', 'confidence'),
        ('use_in_section', 'use_in_section'),
        ('review_decision', 'review_decision'),
        ('reviewer', 'reviewer'),
        ('reviewed_at', 'reviewed_at'),
    ]
    for preview_field, queue_field in field_pairs:
        if str(preview.get(preview_field) or '') != str(queue_row.get(queue_field) or ''):
            errors.append(f'{label}: preview `{preview_field}` does not match review queue `{queue_field}`')
    expected_section = str(queue_row.get('section_heading') or queue_row.get('section_id') or '')
    if str(preview.get('section') or '') != expected_section:
        errors.append(f'{label}: preview `section` does not match review queue source section')
    if str(preview.get('source_file') or '') != str(queue_row.get('source_artifact') or ''):
        errors.append(f'{label}: preview `source_file` does not match review queue `source_artifact`')
    expected_location = source_location_for_patch(queue_row)
    if str(preview.get('source_location') or '') != expected_location:
        errors.append(f'{label}: preview `source_location` does not match regenerated source location')
    expected_id = candidate_id_for(queue_row)
    if str(preview.get('candidate_id') or '').strip() != expected_id:
        errors.append(f'{label}: candidate_id does not match deterministic review queue hash `{expected_id}`')
    return errors


def validate_promotion_preview(preview_path, paper_id=None, candidate_id=None, write_queue_report=True):
    preview_path = resolve_root_path(preview_path)
    before = evidence_matrix_metrics()
    errors = []
    warnings = []
    preview_rows = []
    preview_header = []
    matrix_rows = []
    matrix_header = []
    if not preview_path.exists():
        errors.append(f'missing patch preview file: {path_for_report(preview_path)}')
    else:
        try:
            preview_rows, preview_header = read_csv_rows_with_header(preview_path)
        except Exception as e:
            errors.append(f'failed to read patch preview file: {e}')
    missing_preview_headers = [h for h in EVIDENCE_PATCH_PREVIEW_FIELDS if h not in preview_header]
    if missing_preview_headers:
        errors.append('patch preview missing required headers: ' + ', '.join(missing_preview_headers))
    selected_rows = [r for r in preview_rows if (not paper_id or r.get('paper_id') == paper_id) and (not candidate_id or str(r.get('candidate_id') or '').strip() == candidate_id)]
    if paper_id and preview_path.exists() and not selected_rows:
        errors.append(f'no patch preview rows found for paper_id={paper_id}')
    if candidate_id and preview_path.exists() and not selected_rows:
        errors.append(f'no patch preview row found for candidate_id={candidate_id}')

    ev_path = ROOT/'matrices/evidence_matrix.csv'
    if not ev_path.exists():
        errors.append(f'missing Evidence Matrix: {path_for_report(ev_path)}')
    else:
        try:
            matrix_rows, matrix_header = read_evidence_matrix_with_header()
        except Exception as e:
            errors.append(f'failed to read Evidence Matrix: {e}')
    missing_matrix_columns = [c for c in EVIDENCE_MATRIX_PROMOTION_REQUIRED_COLUMNS if c not in matrix_header]
    if missing_matrix_columns:
        errors.append('Evidence Matrix missing guarded promotion columns; run migrate-evidence first: ' + ', '.join(missing_matrix_columns))

    candidate_ids = [str(r.get('candidate_id') or '').strip() for r in selected_rows]
    duplicate_ids = sorted(k for k, v in count_values(candidate_ids).items() if k and v > 1)
    if duplicate_ids:
        errors.append(f'patch preview duplicate candidate_id values: {len(duplicate_ids)}')
        warnings.extend([f'patch preview duplicate candidate_id: {cid}' for cid in duplicate_ids[:50]])
    duplicate_preview_keys = sorted(k for k, v in count_values([evidence_key_from_row(r) for r in selected_rows]).items() if k.strip('|') and v > 1)
    if duplicate_preview_keys:
        errors.append(f'patch preview duplicate evidence keys: {len(duplicate_preview_keys)}')

    preview_verified_false_count = sum(1 for r in selected_rows if str(r.get('verified') or '').strip().lower() == 'false')
    if selected_rows and preview_verified_false_count != len(selected_rows):
        errors.append(f'patch preview verified must remain false: {preview_verified_false_count}/{len(selected_rows)}')
    for row in selected_rows:
        errors.extend(preview_row_required_value_errors(row))
        label = row.get('candidate_id') or '(blank candidate_id)'
        if str(row.get('promotion_ready') or '').strip().lower() != 'true':
            errors.append(f'{label}: promotion_ready is not true in patch preview')
        if str(row.get('promotion_blockers') or '').strip():
            errors.append(f'{label}: patch preview has promotion_blockers `{row.get("promotion_blockers")}`')
        if str(row.get('review_decision') or '').strip() != 'include':
            errors.append(f'{label}: review_decision is not include')

    queue_validation = validate_review_queue_data(paper_id, write_report=write_queue_report)
    errors.extend(queue_validation.get('errors') or [])
    warnings.extend(queue_validation.get('warnings') or [])
    queue_rows = queue_validation.get('rows') or []
    queue_by_id = {str(r.get('candidate_id') or '').strip(): r for r in queue_rows if str(r.get('candidate_id') or '').strip()}
    duplicate_queue_ids = queue_validation.get('duplicate_candidate_ids') or set()
    bib_keys = canonical_bib_citekeys()
    existing_keys = evidence_matrix_existing_candidate_keys()

    ready_rows = []
    skipped_rows = []
    blocked_rows = []
    blocker_counts = defaultdict(int)
    for preview in selected_rows:
        cid = str(preview.get('candidate_id') or '').strip()
        row_errors = []
        row_blockers = []
        queue_row = queue_by_id.get(cid)
        if not queue_row:
            row_blockers.append('missing_review_queue_row')
        else:
            row_errors.extend(preview_queue_mismatch_errors(preview, queue_row))
            current_blockers = promotion_blockers_for_row(queue_row, duplicate_queue_ids, bib_keys, existing_keys)
            row_blockers.extend(current_blockers)
        if row_errors:
            errors.extend(row_errors)
            row_blockers.append('preview_review_queue_mismatch')
        for blocker in row_blockers:
            blocker_counts[blocker] += 1
        detail = {
            'candidate_id': cid,
            'preview': preview,
            'queue_row': queue_row,
            'blockers': row_blockers,
        }
        if row_blockers == ['already_in_evidence_matrix']:
            skipped_rows.append(detail)
        elif row_blockers:
            blocked_rows.append(detail)
        else:
            ready_rows.append(detail)

    after = evidence_matrix_metrics()
    comparison = compare_evidence_matrix_metrics(before, after)
    append_evidence_matrix_protection_errors(errors, comparison)
    return {
        'preview_path': path_for_report(preview_path),
        'paper_id_filter': paper_id or '(none)',
        'candidate_id_filter': candidate_id or '(none)',
        'preview_total_rows': len(preview_rows),
        'selected_rows': len(selected_rows),
        'preview_verified_false_count': preview_verified_false_count,
        'ready_rows': ready_rows,
        'skipped_rows': skipped_rows,
        'blocked_rows': blocked_rows,
        'ready_count': len(ready_rows),
        'skipped_count': len(skipped_rows),
        'blocked_count': len(blocked_rows),
        'candidate_id_duplicate_count': len(duplicate_ids),
        'duplicate_preview_evidence_key_count': len(duplicate_preview_keys),
        'blocker_distribution': dict(sorted(blocker_counts.items())),
        'matrix_header': matrix_header,
        'matrix_row_count_before_validation': before['row_count'],
        'matrix_row_count_after_validation': after['row_count'],
        'matrix_sha256_before_validation': before['sha256'],
        'matrix_sha256_after_validation': after['sha256'],
        'matrix_schema_changed_during_validation': comparison['schema_changed'],
        'matrix_identity_changed_during_validation': comparison['identity_set_changed'],
        'warnings': warnings,
        'errors': errors,
        'queue_report': queue_validation.get('report') or '',
    }


def evidence_row_from_preview(preview, fieldnames, evidence_id, applied_at, preview_path):
    row = {field: '' for field in fieldnames}
    quote = preview.get('quote') or ''
    candidate_id = str(preview.get('candidate_id') or '').strip()
    reviewer = str(preview.get('reviewer') or '').strip()
    reviewed_at = str(preview.get('reviewed_at') or '').strip()
    source_method = str(preview.get('extraction_method') or '').strip()
    comment = str(preview.get('my_comment') or '').strip()
    if comment:
        comment = comment + '; guarded apply appended with verified=false'
    else:
        comment = 'guarded apply appended with verified=false; verify original PDF before verified=true'
    risk_note = '; '.join([
        f'candidate_id={candidate_id}',
        f'reviewer={reviewer}',
        f'reviewed_at={reviewed_at}',
        f'applied_from={path_for_report(resolve_root_path(preview_path))}',
        f'preview_extraction_method={source_method}',
        'verified=false',
    ])
    values = {
        'paper_id': preview.get('paper_id', ''),
        'citekey': preview.get('citekey', ''),
        'claim_type': preview.get('claim_type', ''),
        'claim': preview.get('claim', ''),
        'quote': quote,
        'page': preview.get('page', ''),
        'section': preview.get('section', ''),
        'confidence': preview.get('confidence', ''),
        'use_in_section': preview.get('use_in_section', ''),
        'my_comment': comment,
        'verified': 'false',
        'source_file': preview.get('source_file', ''),
        'created_at': preview.get('created_at') or applied_at,
        'updated_at': applied_at,
        'evidence_id': evidence_id,
        'exact_quote': quote,
        'source_location': preview.get('source_location', ''),
        'extraction_method': 'candidate_review_guarded_apply',
        'verified_by': '',
        'verified_at': '',
        'use_in_chapter': preview.get('use_in_section', ''),
        'use_in_paragraph': '',
        'risk_note': risk_note,
    }
    for key, value in values.items():
        if key in row:
            row[key] = value
    row['verified'] = 'false'
    return row


def write_promotion_apply_report(result):
    out = ROOT/f'reports/promotion/promotion_apply_{today()}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# Evidence Promotion Guarded Apply {today()}\n', '## Summary\n']
    for key in [
        'mode', 'preview_path', 'paper_id_filter', 'candidate_id_filter',
        'preview_total_rows', 'selected_rows', 'ready_count', 'appended_count',
        'skipped_count', 'blocked_count', 'candidate_id_duplicate_count',
        'duplicate_preview_evidence_key_count', 'all_appended_verified_false',
        'evidence_matrix_row_count_before', 'evidence_matrix_row_count_after',
        'evidence_matrix_sha256_before', 'evidence_matrix_sha256_after',
        'evidence_matrix_schema_changed', 'backup_path', 'applied',
    ]:
        lines.append(f'- {key}: {result.get(key)}\n')
    lines.append('\n## Validation Reports\n')
    if result.get('queue_report'):
        lines.append(f"- review_queue_validation: `{result.get('queue_report')}`\n")
    else:
        lines.append('- review_queue_validation: none\n')
    lines.append('\n## Blocker Distribution\n')
    for value, count in (result.get('blocker_distribution') or {}).items():
        lines.append(f'- {value}: {count}\n')
    if not result.get('blocker_distribution'):
        lines.append('- none\n')
    lines.append('\n## Appended Candidates\n')
    lines.extend([f"- `{cid}`\n" for cid in result.get('appended_candidate_ids', [])] or ['- none\n'])
    lines.append('\n## Skipped Candidates\n')
    lines.extend([f"- `{item.get('candidate_id')}`: {';'.join(item.get('blockers') or []) or 'skipped'}\n" for item in result.get('skipped_rows', [])] or ['- none\n'])
    lines.append('\n## Blocked Candidates\n')
    lines.extend([f"- `{item.get('candidate_id')}`: {';'.join(item.get('blockers') or []) or 'blocked'}\n" for item in result.get('blocked_rows', [])] or ['- none\n'])
    lines.append('\n## Warnings\n')
    lines.extend([f"- {w}\n" for w in result.get('warnings', [])] or ['- none\n'])
    lines.append('\n## Errors\n')
    lines.extend([f"- {e}\n" for e in result.get('errors', [])] or ['- none\n'])
    lines.append('\n## Safety Notes\n')
    lines.append('- Applied rows, if any, are appended only; existing Evidence Matrix rows are preserved.\n')
    lines.append('- Applied rows keep `verified=false`; `verified_by` and `verified_at` remain blank.\n')
    lines.append('- Candidate traceability is preserved in `risk_note` as `candidate_id=...`.\n')
    out.write_text(''.join(lines), encoding='utf-8')
    return out


def cmd_promote_evidence(args):
    if args.apply and args.dry_run:
        raise SystemExit('ERROR: --apply and --dry-run cannot be used together')
    if not args.apply and not args.dry_run:
        raise SystemExit('ERROR: choose --dry-run or --apply')
    before = evidence_matrix_metrics()
    validation = validate_promotion_preview(args.from_preview, paper_id=args.paper_id, candidate_id=args.candidate_id, write_queue_report=True)
    errors = list(validation.get('errors') or [])
    warnings = list(validation.get('warnings') or [])
    appended_candidate_ids = []
    backup_path = ''
    applied = False
    all_appended_verified_false = True

    if args.apply:
        if validation['selected_rows'] == 0:
            errors.append('no patch preview rows selected for apply')
        if validation['blocked_count']:
            errors.append(f'blocked candidates present: {validation["blocked_count"]}')
        if not errors and validation['ready_count']:
            ev = ROOT/'matrices/evidence_matrix.csv'
            matrix_rows, fieldnames = read_evidence_matrix_with_header()
            backup_dir = ROOT/'matrices/backups'
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup = backup_dir/f'evidence_matrix_before_promotion_apply_{stamp}.csv'
            shutil.copy2(ev, backup)
            backup_path = path_for_report(backup)
            seen_ids = {str(r.get('evidence_id') or '').strip() for r in matrix_rows if str(r.get('evidence_id') or '').strip()}
            applied_at = now()
            new_rows = []
            for index, item in enumerate(validation['ready_rows'], 1):
                preview = item['preview']
                temp_row = evidence_row_from_preview(preview, fieldnames, '', applied_at, args.from_preview)
                evidence_id = stable_evidence_id(temp_row, len(matrix_rows) + index, seen_ids)
                temp_row['evidence_id'] = evidence_id
                temp_row['verified'] = 'false'
                new_rows.append(temp_row)
                appended_candidate_ids.append(preview.get('candidate_id', ''))
            with ev.open('w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(matrix_rows)
                writer.writerows(new_rows)
            applied = True
            all_appended_verified_false = all(str(r.get('verified') or '').strip().lower() == 'false' for r in new_rows)
            if not all_appended_verified_false:
                errors.append('one or more appended rows are not verified=false')
        elif not errors and validation['ready_count'] == 0 and validation['skipped_count'] == validation['selected_rows']:
            warnings.append('all selected candidates were already present; no Evidence Matrix write performed')
        elif not errors and validation['ready_count'] == 0:
            errors.append('no promotion-ready rows selected for apply')

    after = evidence_matrix_metrics()
    comparison = compare_evidence_matrix_metrics(before, after)
    if args.dry_run:
        append_evidence_matrix_protection_errors(errors, comparison)
    else:
        expected_rows = before['row_count'] + len(appended_candidate_ids)
        if after['row_count'] != expected_rows:
            errors.append(f'Evidence Matrix row count mismatch after apply: expected {expected_rows}, found {after["row_count"]}')
        if comparison['schema_changed']:
            errors.append('Evidence Matrix schema changed during guarded apply')

    result = {
        'mode': 'apply' if args.apply else 'dry-run',
        'preview_path': validation['preview_path'],
        'paper_id_filter': validation['paper_id_filter'],
        'candidate_id_filter': validation['candidate_id_filter'],
        'preview_total_rows': validation['preview_total_rows'],
        'selected_rows': validation['selected_rows'],
        'ready_count': validation['ready_count'],
        'appended_count': len(appended_candidate_ids),
        'skipped_count': validation['skipped_count'],
        'blocked_count': validation['blocked_count'],
        'candidate_id_duplicate_count': validation['candidate_id_duplicate_count'],
        'duplicate_preview_evidence_key_count': validation['duplicate_preview_evidence_key_count'],
        'all_appended_verified_false': str(all_appended_verified_false).lower(),
        'evidence_matrix_row_count_before': before['row_count'],
        'evidence_matrix_row_count_after': after['row_count'],
        'evidence_matrix_sha256_before': before['sha256'],
        'evidence_matrix_sha256_after': after['sha256'],
        'evidence_matrix_schema_changed': comparison['schema_changed'],
        'backup_path': backup_path or '(none)',
        'applied': str(applied).lower(),
        'queue_report': validation.get('queue_report') or '',
        'blocker_distribution': validation.get('blocker_distribution') or {},
        'appended_candidate_ids': appended_candidate_ids,
        'skipped_rows': [{'candidate_id': r.get('candidate_id'), 'blockers': r.get('blockers')} for r in validation.get('skipped_rows', [])],
        'blocked_rows': [{'candidate_id': r.get('candidate_id'), 'blockers': r.get('blockers')} for r in validation.get('blocked_rows', [])],
        'warnings': warnings,
        'errors': errors,
    }
    report = write_promotion_apply_report(result)
    print(report)
    for key in [
        'mode', 'selected_rows', 'ready_count', 'appended_count', 'skipped_count',
        'blocked_count', 'evidence_matrix_row_count_before', 'evidence_matrix_row_count_after',
        'evidence_matrix_sha256_before', 'evidence_matrix_sha256_after', 'backup_path',
        'applied',
    ]:
        print(f"{key}={result.get(key)}")
    log(f"promote-evidence {result['mode']} 실행: selected={result['selected_rows']}, appended={result['appended_count']}, skipped={result['skipped_count']}, blocked={result['blocked_count']}, errors={len(errors)}")
    if errors:
        raise SystemExit(1)


def candidate_id_from_risk_note(value):
    match = re.search(r'(?:^|;)\s*candidate_id=([^;]+)', str(value or ''))
    return match.group(1).strip() if match else ''


def parse_source_location(value):
    parsed = {}
    for part in str(value or '').split(';'):
        if '=' not in part:
            continue
        key, raw = part.split('=', 1)
        parsed[key.strip()] = raw.strip()
    return parsed


def promoted_evidence_rows(paper_id=None, candidate_id=None):
    rows = read_evidence_for_audit()
    promoted = []
    for row in rows:
        cid = candidate_id_from_risk_note(row.get('risk_note'))
        if not cid and str(row.get('extraction_method') or '').strip() != 'candidate_review_guarded_apply':
            continue
        if paper_id and row.get('paper_id') != paper_id:
            continue
        if candidate_id and cid != candidate_id:
            continue
        copy = dict(row)
        copy['_candidate_id'] = cid
        promoted.append(copy)
    return promoted


def source_artifact_text_for_row(row, errors):
    paper_id = str(row.get('paper_id') or '').strip()
    source_file = str(row.get('source_file') or '').strip()
    if not paper_id:
        return '', 'missing_paper_id'
    artifact_dir = ROOT/'data/parsed/grobid'/slug(paper_id, 80)
    if source_file == 'sections.json':
        path = artifact_dir/'sections.json'
        if not path.exists():
            return '', f'missing_artifact:{path_for_report(path)}'
        data = read_json_file(path, errors)
        loc = parse_source_location(row.get('source_location'))
        section_id = loc.get('section_id') or ''
        section_heading = loc.get('section_heading') or row.get('section') or ''
        sections = data.get('sections') if isinstance(data.get('sections'), list) else []
        candidates = []
        if section_id:
            candidates = [s for s in sections if str(s.get('section_id') or '') == section_id]
        if not candidates and section_heading:
            candidates = [s for s in sections if str(s.get('heading') or '') == section_heading]
        if not candidates:
            return '', f'section_not_found:{section_id or section_heading or "(blank)"}'
        return str(candidates[0].get('text') or ''), ''
    if source_file == 'citation_contexts.json':
        path = artifact_dir/'citation_contexts.json'
        if not path.exists():
            return '', f'missing_artifact:{path_for_report(path)}'
        data = read_json_file(path, errors)
        contexts = data.get('contexts') if isinstance(data.get('contexts'), list) else []
        quote = str(row.get('quote') or row.get('exact_quote') or '').strip()
        matches = [c for c in contexts if quote and quote in str(c.get('sentence') or '')]
        if not matches:
            return '', 'citation_context_not_found'
        return str(matches[0].get('sentence') or ''), ''
    return '', f'unsupported_source_file:{source_file or "(blank)"}'


def promoted_row_qa(row):
    warnings = []
    errors = []
    cid = row.get('_candidate_id') or candidate_id_from_risk_note(row.get('risk_note'))
    quote = str(row.get('quote') or '').strip()
    exact_quote = str(row.get('exact_quote') or '').strip()
    loc = parse_source_location(row.get('source_location'))
    expected_quote_sha = loc.get('quote_sha256') or ''
    actual_quote_sha = hashlib.sha256(quote.encode('utf-8')).hexdigest()[:12] if quote else ''
    if not cid:
        errors.append('missing_candidate_id_trace')
    if str(row.get('verified') or '').strip().lower() != 'false':
        errors.append('verified_not_false')
    if str(row.get('verified_by') or '').strip():
        errors.append('verified_by_must_be_blank')
    if str(row.get('verified_at') or '').strip():
        errors.append('verified_at_must_be_blank')
    if str(row.get('extraction_method') or '').strip() != 'candidate_review_guarded_apply':
        errors.append('unexpected_extraction_method')
    if not quote:
        errors.append('missing_quote')
    if not exact_quote:
        errors.append('missing_exact_quote')
    elif exact_quote != quote:
        errors.append('exact_quote_differs_from_quote')
    if not str(row.get('source_location') or '').strip():
        errors.append('missing_source_location')
    if expected_quote_sha and actual_quote_sha != expected_quote_sha:
        errors.append('quote_sha256_mismatch')
    if not str(row.get('page') or '').strip():
        warnings.append('page_blank_pdf_check_required')
    if any(token in quote for token in ['460,497', '13,431', '92.7%', 'Orphadata', 'biomedical', 'clinical']):
        warnings.append('domain_specific_claim_use_cautiously')
    artifact_text, artifact_error = source_artifact_text_for_row(row, errors)
    quote_in_artifact = bool(quote and artifact_text and quote in artifact_text)
    if artifact_error:
        errors.append(artifact_error)
    elif not quote_in_artifact:
        errors.append('quote_not_found_in_source_artifact')
    return {
        'candidate_id': cid,
        'paper_id': row.get('paper_id') or '',
        'citekey': row.get('citekey') or '',
        'evidence_id': row.get('evidence_id') or '',
        'claim_type': row.get('claim_type') or '',
        'use_in_section': row.get('use_in_section') or '',
        'source_file': row.get('source_file') or '',
        'source_location': row.get('source_location') or '',
        'quote_sha256_expected': expected_quote_sha,
        'quote_sha256_actual': actual_quote_sha,
        'quote_in_source_artifact': str(quote_in_artifact).lower(),
        'verified': row.get('verified') or '',
        'page': row.get('page') or '',
        'quote': quote,
        'warnings': warnings,
        'errors': errors,
    }


def write_promoted_evidence_qa_report(result):
    out = ROOT/f'reports/audit_reports/promoted_evidence_qa_{today()}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# Promoted Evidence QA {today()}\n', '## Summary\n']
    for key in [
        'paper_id_filter', 'candidate_id_filter', 'promoted_count',
        'verified_false_count', 'quote_in_source_artifact_count',
        'page_blank_count', 'domain_specific_warning_count',
        'row_error_count', 'row_warning_count', 'evidence_matrix_row_count',
        'evidence_matrix_sha256',
    ]:
        lines.append(f'- {key}: {result.get(key)}\n')
    lines.append('\n## Candidate QA Table\n')
    lines.append('| candidate_id | evidence_id | claim_type | source | quote_in_artifact | verified | warnings | errors |\n')
    lines.append('|---|---|---|---|---|---|---|---|\n')
    for row in result.get('rows', []):
        warnings = '<br>'.join(row.get('warnings') or []) or 'none'
        errors = '<br>'.join(row.get('errors') or []) or 'none'
        source = f"{row.get('source_file')} / {row.get('source_location')}".replace('|', '\\|')
        lines.append(f"| `{row.get('candidate_id')}` | `{row.get('evidence_id')}` | {row.get('claim_type')} | {source} | {row.get('quote_in_source_artifact')} | {row.get('verified')} | {warnings} | {errors} |\n")
    lines.append('\n## Quote Preview\n')
    for row in result.get('rows', []):
        lines.append(f"### `{row.get('candidate_id')}`\n\n")
        lines.append(f"- evidence_id: `{row.get('evidence_id')}`\n")
        lines.append(f"- use_in_section: {row.get('use_in_section')}\n")
        lines.append(f"- quote_sha256: expected `{row.get('quote_sha256_expected') or '(none)'}`, actual `{row.get('quote_sha256_actual') or '(none)'}`\n\n")
        lines.append('```text\n' + (row.get('quote') or '')[:1200] + '\n```\n\n')
    lines.append('## Warnings\n')
    lines.extend([f"- {w}\n" for w in result.get('warnings', [])] or ['- none\n'])
    lines.append('\n## Errors\n')
    lines.extend([f"- {e}\n" for e in result.get('errors', [])] or ['- none\n'])
    lines.append('\n## PM Note\n')
    lines.append('- This QA does not set `verified=true`. Page/PDF-level verification is still required before final verified evidence status.\n')
    out.write_text(''.join(lines), encoding='utf-8')
    return out


def cmd_audit_promoted_evidence(args):
    matrix = evidence_matrix_metrics()
    rows = promoted_evidence_rows(getattr(args, 'paper_id', None), getattr(args, 'candidate_id', None))
    qa_rows = [promoted_row_qa(row) for row in rows]
    candidate_ids = [row.get('candidate_id') for row in qa_rows]
    duplicate_ids = sorted(k for k, v in count_values(candidate_ids).items() if k and v > 1)
    warnings = []
    errors = []
    if not qa_rows:
        errors.append('no promoted evidence rows found for requested filter')
    if duplicate_ids:
        errors.append(f'duplicate promoted candidate_id values: {len(duplicate_ids)}')
        warnings.extend([f'duplicate promoted candidate_id: {cid}' for cid in duplicate_ids[:50]])
    for row in qa_rows:
        for err in row.get('errors') or []:
            errors.append(f"{row.get('candidate_id')}: {err}")
        for warn in row.get('warnings') or []:
            warnings.append(f"{row.get('candidate_id')}: {warn}")
    result = {
        'paper_id_filter': getattr(args, 'paper_id', None) or '(none)',
        'candidate_id_filter': getattr(args, 'candidate_id', None) or '(none)',
        'promoted_count': len(qa_rows),
        'verified_false_count': sum(1 for r in qa_rows if str(r.get('verified') or '').strip().lower() == 'false'),
        'quote_in_source_artifact_count': sum(1 for r in qa_rows if r.get('quote_in_source_artifact') == 'true'),
        'page_blank_count': sum(1 for r in qa_rows if not str(r.get('page') or '').strip()),
        'domain_specific_warning_count': sum(1 for r in qa_rows if 'domain_specific_claim_use_cautiously' in (r.get('warnings') or [])),
        'row_error_count': sum(len(r.get('errors') or []) for r in qa_rows),
        'row_warning_count': sum(len(r.get('warnings') or []) for r in qa_rows),
        'evidence_matrix_row_count': matrix['row_count'],
        'evidence_matrix_sha256': matrix['sha256'],
        'rows': qa_rows,
        'warnings': warnings,
        'errors': errors,
    }
    report = write_promoted_evidence_qa_report(result)
    print(report)
    for key in [
        'promoted_count', 'verified_false_count', 'quote_in_source_artifact_count',
        'page_blank_count', 'domain_specific_warning_count', 'row_error_count',
        'row_warning_count', 'evidence_matrix_row_count', 'evidence_matrix_sha256',
    ]:
        print(f"{key}={result.get(key)}")
    log(f"audit-promoted-evidence 실행: paper_id={getattr(args, 'paper_id', None) or '(none)'}, rows={len(qa_rows)}, errors={len(errors)}")
    if errors:
        raise SystemExit(1)


def promoted_row_date(row):
    value = str(row.get('updated_at') or row.get('created_at') or '').strip()
    if not value:
        return ''
    return value.split('T', 1)[0].split(' ', 1)[0]


def filter_promoted_rows(rows, since=None):
    if not since:
        return rows
    since_date = str(since).strip().split('T', 1)[0].split(' ', 1)[0]
    return [row for row in rows if promoted_row_date(row) and promoted_row_date(row) >= since_date]


PROMOTED_REVIEW_INPUT_FIELDS = ['candidate_id', 'evidence_id', 'paper_id', 'citekey', 'claim', 'exact_quote', 'claim_type', 'use_in_section', 'section', 'source_artifact', 'source_location', 'page', 'verified', 'risk_note', 'created_at', 'updated_at']


def promoted_review_input_row(row):
    return {
        'candidate_id': row.get('_candidate_id') or candidate_id_from_risk_note(row.get('risk_note')),
        'evidence_id': row.get('evidence_id') or '',
        'paper_id': row.get('paper_id') or '',
        'citekey': row.get('citekey') or '',
        'claim': row.get('claim') or '',
        'exact_quote': row.get('exact_quote') or row.get('quote') or '',
        'claim_type': row.get('claim_type') or '',
        'use_in_section': row.get('use_in_section') or '',
        'section': row.get('section') or '',
        'source_artifact': row.get('source_file') or '',
        'source_location': row.get('source_location') or '',
        'page': row.get('page') or '',
        'verified': row.get('verified') or '',
        'risk_note': row.get('risk_note') or '',
        'created_at': row.get('created_at') or '',
        'updated_at': row.get('updated_at') or '',
    }


def write_promoted_review_input_report(result):
    out = ROOT/f'reports/review/promoted_rows_external_review_input_{today()}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# Promoted Rows External Review Input {today()}\n', '## Summary\n']
    for key in ['paper_id_filter', 'since', 'row_count', 'output_csv', 'evidence_matrix_row_count', 'evidence_matrix_sha256']:
        lines.append(f'- {key}: {result.get(key)}\n')
    lines.append('\n## Rows\n')
    for row in result.get('rows', []):
        lines.append(f"- `{row.get('candidate_id')}` / `{row.get('evidence_id')}` / {row.get('claim_type')} / {row.get('use_in_section')}\n")
    out.write_text(''.join(lines), encoding='utf-8')
    return out


def cmd_extract_promoted_rows(args):
    rows = promoted_evidence_rows(getattr(args, 'paper_id', None), getattr(args, 'candidate_id', None))
    rows = filter_promoted_rows(rows, getattr(args, 'since', None))
    out_path = resolve_root_path(args.output) if getattr(args, 'output', None) else ROOT/f'reports/review/promoted_rows_external_review_input_{today()}.csv'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_rows = [promoted_review_input_row(row) for row in rows]
    with out_path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=PROMOTED_REVIEW_INPUT_FIELDS)
        writer.writeheader()
        writer.writerows(out_rows)
    matrix = evidence_matrix_metrics()
    result = {
        'paper_id_filter': getattr(args, 'paper_id', None) or '(none)',
        'candidate_id_filter': getattr(args, 'candidate_id', None) or '(none)',
        'since': getattr(args, 'since', None) or '(none)',
        'row_count': len(out_rows),
        'output_csv': path_for_report(out_path),
        'evidence_matrix_row_count': matrix['row_count'],
        'evidence_matrix_sha256': matrix['sha256'],
        'rows': out_rows,
    }
    report = write_promoted_review_input_report(result)
    print(out_path)
    print(report)
    print(f"row_count={len(out_rows)}")
    print(f"evidence_matrix_row_count={matrix['row_count']}")
    print(f"evidence_matrix_sha256={matrix['sha256']}")
    log(f"extract-promoted-rows 실행: rows={len(out_rows)}, output={path_for_report(out_path)}")


def write_pdf_page_check_report(result):
    out = ROOT/f'reports/review/pdf_page_check_required_{today()}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# PDF Page Check Required {today()}\n', '## Summary\n']
    for key in ['mode', 'promoted_only', 'paper_id_filter', 'row_count', 'page_blank_count', 'evidence_matrix_row_count', 'evidence_matrix_sha256']:
        lines.append(f'- {key}: {result.get(key)}\n')
    lines.append('\n## Page Blank Rows\n')
    lines.append('| candidate_id | evidence_id | citekey | claim_type | section | quote_preview |\n')
    lines.append('|---|---|---|---|---|---|\n')
    for row in result.get('page_blank_rows', []):
        quote = (row.get('exact_quote') or row.get('quote') or '').replace('|', '\\|')[:160]
        lines.append(f"| `{row.get('_candidate_id')}` | `{row.get('evidence_id')}` | `{row.get('citekey')}` | {row.get('claim_type')} | {str(row.get('section') or '').replace('|', '\\|')} | {quote} |\n")
    if not result.get('page_blank_rows'):
        lines.append('| none |  |  |  |  |  |\n')
    lines.append('\n## Warnings\n')
    lines.extend([f"- {w}\n" for w in result.get('warnings', [])] or ['- none\n'])
    lines.append('\n## Errors\n')
    lines.extend([f"- {e}\n" for e in result.get('errors', [])] or ['- none\n'])
    out.write_text(''.join(lines), encoding='utf-8')
    return out


def cmd_mark_pdf_check_required(args):
    if not getattr(args, 'dry_run', False):
        raise SystemExit('ERROR: mark-pdf-check-required is report-only and requires --dry-run')
    rows = promoted_evidence_rows(getattr(args, 'paper_id', None), getattr(args, 'candidate_id', None)) if getattr(args, 'promoted_only', False) else read_evidence_for_audit()
    page_blank_rows = [row for row in rows if not str(row.get('page') or '').strip()]
    warnings = [f"{row.get('_candidate_id') or candidate_id_from_risk_note(row.get('risk_note')) or row.get('evidence_id')}: page_blank_pdf_check_required" for row in page_blank_rows]
    matrix = evidence_matrix_metrics()
    result = {
        'mode': 'dry-run',
        'promoted_only': str(bool(getattr(args, 'promoted_only', False))).lower(),
        'paper_id_filter': getattr(args, 'paper_id', None) or '(none)',
        'row_count': len(rows),
        'page_blank_count': len(page_blank_rows),
        'page_blank_rows': page_blank_rows,
        'evidence_matrix_row_count': matrix['row_count'],
        'evidence_matrix_sha256': matrix['sha256'],
        'warnings': warnings,
        'errors': [],
    }
    report = write_pdf_page_check_report(result)
    print(report)
    print(f"row_count={len(rows)}")
    print(f"page_blank_count={len(page_blank_rows)}")
    print(f"evidence_matrix_row_count={matrix['row_count']}")
    log(f"mark-pdf-check-required dry-run 실행: promoted_only={result['promoted_only']}, page_blank={len(page_blank_rows)}")


DOMAIN_SPECIFIC_KEYWORDS = ['biomedical', 'clinical', 'clinician', 'patient', 'patients', 'disease', 'diseases', 'medical', 'medicine', 'orphanet', 'orphadata', 'pmid', 'drug', 'phenotyping', 'biology', 'regulatory', 'onset age', 'clinical milestone']


def domain_specific_hits(row):
    text = ' '.join([row.get('claim') or '', row.get('quote') or '', row.get('exact_quote') or '', row.get('section') or '']).lower()
    return sorted({kw for kw in DOMAIN_SPECIFIC_KEYWORDS if kw in text})


def write_domain_specific_claim_audit_report(result):
    out = ROOT/f'reports/review/domain_specific_claim_audit_{today()}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# Domain-specific Claim Audit {today()}\n', '## Summary\n']
    for key in ['promoted_only', 'paper_id_filter', 'row_count', 'domain_specific_count', 'overclaim_warning_count', 'evidence_matrix_row_count', 'evidence_matrix_sha256']:
        lines.append(f'- {key}: {result.get(key)}\n')
    lines.append('\n## Domain-specific Rows\n')
    lines.append('| candidate_id | evidence_id | claim_type | use_in_section | hits | recommendation | quote_preview |\n')
    lines.append('|---|---|---|---|---|---|---|\n')
    for item in result.get('domain_rows', []):
        row = item['row']
        quote = (row.get('exact_quote') or row.get('quote') or '').replace('|', '\\|')[:180]
        lines.append(f"| `{row.get('_candidate_id')}` | `{row.get('evidence_id')}` | {row.get('claim_type')} | {row.get('use_in_section')} | {', '.join(item.get('hits') or [])} | {item.get('recommendation')} | {quote} |\n")
    if not result.get('domain_rows'):
        lines.append('| none |  |  |  |  |  |  |\n')
    lines.append('\n## Global Recommendation\n')
    lines.append('- Treat domain-specific rows as related-work/background examples only. Do not use them as PaperOps performance evidence.\n')
    lines.append('- Prefer wording such as “illustrates”, “provides an example”, or “suggests a design pattern” rather than causal/general performance claims.\n')
    lines.append('- Keep `verified=false` until PDF/page-level verification is complete.\n')
    out.write_text(''.join(lines), encoding='utf-8')
    return out


def cmd_audit_domain_specific_claims(args):
    rows = promoted_evidence_rows(getattr(args, 'paper_id', None), getattr(args, 'candidate_id', None)) if getattr(args, 'promoted_only', False) else read_evidence_for_audit()
    domain_rows = []
    for row in rows:
        hits = domain_specific_hits(row)
        if hits:
            recommendation = 'revise_or_downgrade_to_pdf_check; use as related-work/background example only; no PaperOps performance generalization'
            domain_rows.append({'row': row, 'hits': hits, 'recommendation': recommendation})
    matrix = evidence_matrix_metrics()
    result = {
        'promoted_only': str(bool(getattr(args, 'promoted_only', False))).lower(),
        'paper_id_filter': getattr(args, 'paper_id', None) or '(none)',
        'row_count': len(rows),
        'domain_specific_count': len(domain_rows),
        'overclaim_warning_count': len(domain_rows),
        'domain_rows': domain_rows,
        'evidence_matrix_row_count': matrix['row_count'],
        'evidence_matrix_sha256': matrix['sha256'],
    }
    report = write_domain_specific_claim_audit_report(result)
    print(report)
    print(f"row_count={len(rows)}")
    print(f"domain_specific_count={len(domain_rows)}")
    print(f"overclaim_warning_count={len(domain_rows)}")
    print(f"evidence_matrix_row_count={matrix['row_count']}")
    log(f"audit-domain-specific-claims 실행: promoted_only={result['promoted_only']}, domain_specific={len(domain_rows)}")


def git_status_paths(pathspecs=None):
    cmd = ['git', 'status', '--short']
    if pathspecs:
        cmd += ['--'] + list(pathspecs)
    try:
        result = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return [], f'git status failed: {result.stderr.strip()}'
        return [line.strip() for line in result.stdout.splitlines() if line.strip()], ''
    except Exception as e:
        return [], f'git status unavailable: {e}'


def write_no_auto_verified_guard_report(result):
    out = ROOT/f'reports/audit_reports/no_auto_verified_guard_{today()}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# No-auto-verified Guard {today()}\n', '## Summary\n']
    for key in ['promoted_only', 'paper_id_filter', 'row_count', 'verified_true_count', 'verified_by_filled_count', 'verified_at_filled_count', 'manuscript_changed_count', 'allow_approved_manuscript_changes', 'manuscript_apply_report', 'paperqa_langgraph_warning_count', 'passed']:
        lines.append(f'- {key}: {result.get(key)}\n')
    lines.append('\n## Manuscript Changes\n')
    lines.extend([f"- `{line}`\n" for line in result.get('manuscript_changes', [])] or ['- none\n'])
    lines.append('\n## PaperQA2/LangGraph Warnings\n')
    lines.extend([f"- `{line}`\n" for line in result.get('paperqa_langgraph_warnings', [])] or ['- none\n'])
    lines.append('\n## Errors\n')
    lines.extend([f"- {e}\n" for e in result.get('errors', [])] or ['- none\n'])
    lines.append('\n## Warnings\n')
    lines.extend([f"- {w}\n" for w in result.get('warnings', [])] or ['- none\n'])
    out.write_text(''.join(lines), encoding='utf-8')
    return out


def approved_manuscript_apply_report_errors(report_arg):
    if not report_arg:
        return ['--manuscript-apply-report is required when allowing approved manuscript changes']
    report_path = resolve_root_path(report_arg)
    if not report_path.exists():
        return [f'manuscript apply report not found: {path_for_report(report_path)}']
    text = report_path.read_text(encoding='utf-8')
    checks = [
        ('- mode: apply', 'manuscript apply report must have mode=apply'),
        ('- applied: true', 'manuscript apply report must have applied=true'),
        ('- blocked_rows: 0', 'manuscript apply report must have blocked_rows=0'),
        ('## Errors\n- none', 'manuscript apply report must have no errors'),
    ]
    return [message for needle, message in checks if needle not in text]


def cmd_guard_no_auto_verified(args):
    rows = promoted_evidence_rows(getattr(args, 'paper_id', None), getattr(args, 'candidate_id', None)) if getattr(args, 'promoted_only', False) else read_evidence_for_audit()
    errors = []
    warnings = []
    verified_true = [r for r in rows if str(r.get('verified') or '').strip().lower() == 'true']
    verified_by_filled = [r for r in rows if str(r.get('verified_by') or '').strip()]
    verified_at_filled = [r for r in rows if str(r.get('verified_at') or '').strip()]
    if verified_true:
        errors.append(f'promoted/evidence rows with verified=true: {len(verified_true)}')
    if verified_by_filled:
        errors.append(f'promoted/evidence rows with verified_by filled: {len(verified_by_filled)}')
    if verified_at_filled:
        errors.append(f'promoted/evidence rows with verified_at filled: {len(verified_at_filled)}')
    manuscript_changes, git_warn = git_status_paths(['05_manuscript', 'manuscript'])
    if git_warn:
        warnings.append(git_warn)
    allow_manuscript_changes = bool(getattr(args, 'allow_approved_manuscript_changes', False))
    manuscript_apply_report = getattr(args, 'manuscript_apply_report', None) or ''
    if manuscript_changes:
        if allow_manuscript_changes:
            approval_errors = approved_manuscript_apply_report_errors(manuscript_apply_report)
            if approval_errors:
                errors.extend(approval_errors)
            else:
                warnings.append(f'approved manuscript changes allowed by {manuscript_apply_report}')
        else:
            errors.append(f'manuscript files changed: {len(manuscript_changes)}')
    all_changes, git_warn_all = git_status_paths()
    if git_warn_all:
        warnings.append(git_warn_all)
    paperqa_langgraph_warnings = [line for line in all_changes if re.search(r'paperqa|langgraph', line, re.I)]
    if paperqa_langgraph_warnings:
        warnings.append(f'PaperQA2/LangGraph related file changes detected: {len(paperqa_langgraph_warnings)}')
    result = {
        'promoted_only': str(bool(getattr(args, 'promoted_only', False))).lower(),
        'paper_id_filter': getattr(args, 'paper_id', None) or '(none)',
        'row_count': len(rows),
        'verified_true_count': len(verified_true),
        'verified_by_filled_count': len(verified_by_filled),
        'verified_at_filled_count': len(verified_at_filled),
        'manuscript_changed_count': len(manuscript_changes),
        'allow_approved_manuscript_changes': str(allow_manuscript_changes).lower(),
        'manuscript_apply_report': manuscript_apply_report or '(none)',
        'paperqa_langgraph_warning_count': len(paperqa_langgraph_warnings),
        'manuscript_changes': manuscript_changes,
        'paperqa_langgraph_warnings': paperqa_langgraph_warnings,
        'passed': str(not errors).lower(),
        'errors': errors,
        'warnings': warnings,
    }
    report = write_no_auto_verified_guard_report(result)
    print(report)
    for key in ['row_count', 'verified_true_count', 'verified_by_filled_count', 'verified_at_filled_count', 'manuscript_changed_count', 'paperqa_langgraph_warning_count', 'passed']:
        print(f"{key}={result.get(key)}")
    log(f"guard-no-auto-verified 실행: promoted_only={result['promoted_only']}, passed={result['passed']}, errors={len(errors)}")
    if errors:
        raise SystemExit(1)


PROMOTED_ROW_EXTERNAL_REVIEW_DECISIONS = {
    'fb5137d2707f5e1d': {
        'external_review_decision': 'downgrade_to_pdf_check',
        'suggested_claim_type': 'evaluation_pattern',
        'suggested_use_in_section': 'ch2_related_work;ch3_evaluation_design_motivation',
        'rewrite_if_needed': 'ChronoMedKG 사례는 LLM judge가 아닌 gold-standard comparison을 평가 설계에 활용한 도메인 특화 사례를 보여준다.',
        'pdf_check_priority': 'high',
        'paperops_generalization_allowed': 'false',
        'pdf_page_check_required': 'true',
        'domain_specific_risk': 'high',
    },
    '23fe8ed59649fffa': {
        'external_review_decision': 'revise',
        'suggested_claim_type': 'method_pattern',
        'suggested_use_in_section': 'ch2_related_work;ch3_design_motivation',
        'rewrite_if_needed': '도메인 특화 KG 연구에서 disease-autonomous multi-agent pipeline이 대규모 biomedical triples 생성에 활용된 사례가 있다.',
        'pdf_check_priority': 'high',
        'paperops_generalization_allowed': 'false',
        'pdf_page_check_required': 'true',
        'domain_specific_risk': 'high',
    },
    'd5f10096ff325472': {
        'external_review_decision': 'revise',
        'suggested_claim_type': 'method_pattern',
        'suggested_use_in_section': 'ch2_related_work;ch3_design_motivation',
        'rewrite_if_needed': 'ChronoMedKG는 각 질병 단위를 독립적으로 처리하는 multi-stage pipeline 구조를 채택했다. 이는 도메인 단위 batch/agent pipeline 설계 사례로 참고할 수 있다.',
        'pdf_check_priority': 'medium',
        'paperops_generalization_allowed': 'false',
        'pdf_page_check_required': 'true',
        'domain_specific_risk': 'medium',
    },
    'e340369a59fb2532': {
        'external_review_decision': 'revise',
        'suggested_claim_type': 'agent_workflow_pattern',
        'suggested_use_in_section': 'ch2_related_work;ch3_system_design_motivation',
        'rewrite_if_needed': '특정 biomedical KG 구축 사례에서는 disease identifier를 입력으로 네 개의 협력 agent가 end-to-end pipeline을 수행하도록 설계했다.',
        'pdf_check_priority': 'medium',
        'paperops_generalization_allowed': 'false',
        'pdf_page_check_required': 'true',
        'domain_specific_risk': 'medium',
    },
    '06488bdee4bc0074': {
        'external_review_decision': 'keep',
        'suggested_claim_type': 'governance',
        'suggested_use_in_section': 'ch3_system_design;ch5_evaluation_design',
        'rewrite_if_needed': '검증 harness, judge-panel code, error taxonomy를 공개하는 방식은 연구 자동화 시스템의 auditability와 reproducibility를 높이는 설계 패턴으로 볼 수 있다.',
        'pdf_check_priority': 'medium',
        'paperops_generalization_allowed': 'limited_auditability_design_principle_only',
        'pdf_page_check_required': 'true',
        'domain_specific_risk': 'low',
    },
    '154a4607cb42751c': {
        'external_review_decision': 'revise',
        'suggested_claim_type': 'provenance_pattern',
        'suggested_use_in_section': 'ch2_related_work;ch3_evidence_model_design',
        'rewrite_if_needed': 'ChronoMedKG는 triple 단위에 evidence grading과 PMID provenance를 부여하는 방식으로 출처 추적성을 강화한 사례다.',
        'pdf_check_priority': 'high',
        'paperops_generalization_allowed': 'false',
        'pdf_page_check_required': 'true',
        'domain_specific_risk': 'high',
    },
    '6abed7fd8c1bd310': {
        'external_review_decision': 'revise',
        'suggested_claim_type': 'human_oversight',
        'suggested_use_in_section': 'ch2_related_work;ch3_governance_design;ch6_limitations',
        'rewrite_if_needed': '도메인 특화 자동화 시스템도 원천 데이터 범위와 실제 적용 범위를 구분하며, 고위험 도메인 적용에는 인간 전문가 검토와 별도 평가가 필요하다는 제한을 명시한다.',
        'pdf_check_priority': 'medium',
        'paperops_generalization_allowed': 'false',
        'pdf_page_check_required': 'true',
        'domain_specific_risk': 'high',
    },
    '67558897e8bfbc54': {
        'external_review_decision': 'revise',
        'suggested_claim_type': 'validation_boundary',
        'suggested_use_in_section': 'ch3_evaluation_design;ch5_evaluation_limitations;ch6_limitations',
        'rewrite_if_needed': 'text-grounding 검증은 원문 근거 일치 여부를 확인하는 절차이지, 도메인 사실 자체의 독립적 재검증은 아니라는 한계를 명확히 해야 한다.',
        'pdf_check_priority': 'medium',
        'paperops_generalization_allowed': 'limited_validation_boundary_principle_only',
        'pdf_page_check_required': 'true',
        'domain_specific_risk': 'medium',
    },
}

PROMOTED_ROW_REVIEW_DECISION_FIELDS = PROMOTED_REVIEW_INPUT_FIELDS + ['external_review_decision', 'suggested_claim_type', 'suggested_use_in_section', 'rewrite_if_needed', 'pdf_check_priority', 'paperops_generalization_allowed', 'pdf_page_check_required', 'domain_specific_risk']
EVIDENCE_METADATA_PATCH_PREVIEW_FIELDS = ['candidate_id', 'evidence_id', 'paper_id', 'citekey', 'current_claim_type', 'suggested_claim_type', 'current_use_in_section', 'suggested_use_in_section', 'current_claim', 'rewrite_if_needed', 'external_review_decision', 'pdf_check_priority', 'paperops_generalization_allowed', 'pdf_page_check_required', 'domain_specific_risk', 'verified_should_remain', 'patch_action', 'source_review']


def row_level_decision_for(candidate_id):
    return PROMOTED_ROW_EXTERNAL_REVIEW_DECISIONS.get(candidate_id, {
        'external_review_decision': 'downgrade_to_pdf_check',
        'suggested_claim_type': '',
        'suggested_use_in_section': '',
        'rewrite_if_needed': '',
        'pdf_check_priority': 'high',
        'paperops_generalization_allowed': 'false',
        'pdf_page_check_required': 'true',
        'domain_specific_risk': 'unknown',
    })


def write_row_level_review_pm_report(result):
    out = ROOT/f'reports/review/promoted_rows_row_level_review_decisions_{today()}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# Promoted Row-level Review Decisions {today()}\n', '## Summary\n']
    for key in ['mode', 'input_csv', 'decision_csv', 'metadata_patch_preview', 'row_count', 'keep_count', 'revise_count', 'downgrade_to_pdf_check_count', 'exclude_count', 'evidence_matrix_row_count', 'evidence_matrix_sha256']:
        lines.append(f'- {key}: {result.get(key)}\n')
    lines.append('\n## Decisions\n')
    lines.append('| candidate_id | evidence_id | decision | suggested_claim_type | suggested_use_in_section | pdf_check_priority | generalization |\n')
    lines.append('|---|---|---|---|---|---|---|\n')
    for row in result.get('rows', []):
        lines.append(f"| `{row.get('candidate_id')}` | `{row.get('evidence_id')}` | {row.get('external_review_decision')} | {row.get('suggested_claim_type')} | {row.get('suggested_use_in_section')} | {row.get('pdf_check_priority')} | {row.get('paperops_generalization_allowed')} |\n")
    lines.append('\n## Safety Notes\n')
    lines.append('- This command is dry-run only and does not modify `matrices/evidence_matrix.csv`.\n')
    lines.append('- All reviewed rows must remain `verified=false` until PDF/page verification is complete.\n')
    out.write_text(''.join(lines), encoding='utf-8')
    return out


def cmd_update_promoted_row_review_metadata(args):
    if not getattr(args, 'dry_run', False):
        raise SystemExit('ERROR: update-promoted-row-review-metadata requires --dry-run; direct Evidence Matrix apply is prohibited')
    input_path = resolve_root_path(args.input)
    if not input_path.exists():
        raise SystemExit(f'ERROR: missing input CSV: {input_path}')
    with input_path.open(encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        input_rows = list(reader)
    decision_rows = []
    patch_rows = []
    for row in input_rows:
        cid = str(row.get('candidate_id') or '').strip()
        decision = row_level_decision_for(cid)
        out_row = {field: row.get(field, '') for field in PROMOTED_REVIEW_INPUT_FIELDS}
        out_row.update(decision)
        decision_rows.append(out_row)
        patch_rows.append({
            'candidate_id': cid,
            'evidence_id': row.get('evidence_id', ''),
            'paper_id': row.get('paper_id', ''),
            'citekey': row.get('citekey', ''),
            'current_claim_type': row.get('claim_type', ''),
            'suggested_claim_type': decision.get('suggested_claim_type', ''),
            'current_use_in_section': row.get('use_in_section', ''),
            'suggested_use_in_section': decision.get('suggested_use_in_section', ''),
            'current_claim': row.get('claim', ''),
            'rewrite_if_needed': decision.get('rewrite_if_needed', ''),
            'external_review_decision': decision.get('external_review_decision', ''),
            'pdf_check_priority': decision.get('pdf_check_priority', ''),
            'paperops_generalization_allowed': decision.get('paperops_generalization_allowed', ''),
            'pdf_page_check_required': decision.get('pdf_page_check_required', ''),
            'domain_specific_risk': decision.get('domain_specific_risk', ''),
            'verified_should_remain': 'false',
            'patch_action': 'metadata_preview_only_no_evidence_matrix_write',
            'source_review': 'GPT Pro row-level review captured 2026-06-04',
        })
    decision_csv = resolve_root_path(args.output) if getattr(args, 'output', None) else ROOT/f'reports/review/promoted_rows_row_level_review_decisions_{today()}.csv'
    patch_csv = resolve_root_path(args.patch_preview) if getattr(args, 'patch_preview', None) else ROOT/f'matrices/evidence_matrix_metadata_patch_preview_{today()}.csv'
    decision_csv.parent.mkdir(parents=True, exist_ok=True)
    patch_csv.parent.mkdir(parents=True, exist_ok=True)
    with decision_csv.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=PROMOTED_ROW_REVIEW_DECISION_FIELDS)
        writer.writeheader(); writer.writerows(decision_rows)
    with patch_csv.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=EVIDENCE_METADATA_PATCH_PREVIEW_FIELDS)
        writer.writeheader(); writer.writerows(patch_rows)
    matrix = evidence_matrix_metrics()
    counts = distribution_for(decision_rows, 'external_review_decision')
    result = {
        'mode': 'dry-run',
        'input_csv': path_for_report(input_path),
        'decision_csv': path_for_report(decision_csv),
        'metadata_patch_preview': path_for_report(patch_csv),
        'row_count': len(decision_rows),
        'keep_count': counts.get('keep', 0),
        'revise_count': counts.get('revise', 0),
        'downgrade_to_pdf_check_count': counts.get('downgrade_to_pdf_check', 0),
        'exclude_count': counts.get('exclude', 0),
        'evidence_matrix_row_count': matrix['row_count'],
        'evidence_matrix_sha256': matrix['sha256'],
        'rows': decision_rows,
    }
    report = write_row_level_review_pm_report(result)
    print(decision_csv)
    print(patch_csv)
    print(report)
    for key in ['row_count', 'keep_count', 'revise_count', 'downgrade_to_pdf_check_count', 'exclude_count', 'evidence_matrix_row_count', 'evidence_matrix_sha256']:
        print(f"{key}={result.get(key)}")
    log(f"update-promoted-row-review-metadata dry-run 실행: rows={len(decision_rows)}, patch={path_for_report(patch_csv)}")


OVERCLAIM_KEYWORDS = ['accuracy', 'validated', 'consensus triples', 'disease', 'diseases', 'clinical', 'biology', 'biomedical', 'orphadata', 'pmid']


def paperops_overclaim_flags(row):
    text = ' '.join([row.get('claim') or '', row.get('quote') or '', row.get('exact_quote') or '', row.get('section') or '']).lower()
    hits = sorted({kw for kw in OVERCLAIM_KEYWORDS if kw in text})
    flags = []
    if hits:
        flags.append('domain_or_metric_specific_terms')
    if str(row.get('use_in_section') or '').strip() == 'evaluation' and str(row.get('citekey') or '') != 'paperops':
        flags.append('non_paperops_evaluation_claim')
    if any(kw in hits for kw in ['accuracy', 'validated', 'consensus triples']):
        flags.append('performance_or_scale_claim_not_paperops')
    return hits, flags


def write_paperops_overclaim_guard_report(result):
    out = ROOT/f'reports/review/paperops_overclaim_guard_{today()}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# PaperOps Overclaim Guard {today()}\n', '## Summary\n']
    for key in ['promoted_only', 'paper_id_filter', 'row_count', 'flagged_count', 'performance_evidence_allowed_count', 'evidence_matrix_row_count', 'evidence_matrix_sha256']:
        lines.append(f'- {key}: {result.get(key)}\n')
    lines.append('\n## Flagged Rows\n')
    lines.append('| candidate_id | evidence_id | use_in_section | hits | flags | allowed_as_paperops_performance_evidence | recommendation |\n')
    lines.append('|---|---|---|---|---|---|---|\n')
    for item in result.get('flagged_rows', []):
        row = item['row']
        lines.append(f"| `{row.get('_candidate_id')}` | `{row.get('evidence_id')}` | {row.get('use_in_section')} | {', '.join(item.get('hits') or [])} | {', '.join(item.get('flags') or [])} | false | {item.get('recommendation')} |\n")
    if not result.get('flagged_rows'):
        lines.append('| none |  |  |  |  |  |  |\n')
    lines.append('\n## Guardrail\n')
    lines.append('- Flagged rows may be used only as related-work/design-pattern/limitation examples, not as PaperOps performance evidence.\n')
    out.write_text(''.join(lines), encoding='utf-8')
    return out


def cmd_guard_paperops_overclaim(args):
    rows = promoted_evidence_rows(getattr(args, 'paper_id', None), getattr(args, 'candidate_id', None)) if getattr(args, 'promoted_only', False) else read_evidence_for_audit()
    flagged = []
    for row in rows:
        hits, flags = paperops_overclaim_flags(row)
        if flags:
            flagged.append({
                'row': row,
                'hits': hits,
                'flags': flags,
                'recommendation': 'do_not_use_as_paperops_performance_evidence; use only as related-work/design-pattern/limitation candidate',
            })
    matrix = evidence_matrix_metrics()
    result = {
        'promoted_only': str(bool(getattr(args, 'promoted_only', False))).lower(),
        'paper_id_filter': getattr(args, 'paper_id', None) or '(none)',
        'row_count': len(rows),
        'flagged_count': len(flagged),
        'performance_evidence_allowed_count': 0 if flagged else len(rows),
        'flagged_rows': flagged,
        'evidence_matrix_row_count': matrix['row_count'],
        'evidence_matrix_sha256': matrix['sha256'],
    }
    report = write_paperops_overclaim_guard_report(result)
    print(report)
    print(f"row_count={len(rows)}")
    print(f"flagged_count={len(flagged)}")
    print(f"performance_evidence_allowed_count={result['performance_evidence_allowed_count']}")
    print(f"evidence_matrix_row_count={matrix['row_count']}")
    log(f"guard-paperops-overclaim 실행: promoted_only={result['promoted_only']}, flagged={len(flagged)}")


PDF_PAGE_VERIFICATION_FIELDS = ['candidate_id', 'evidence_id', 'citekey', 'exact_quote', 'current_section', 'source_location', 'page_blank', 'pdf_page_to_fill', 'pdf_verified_by', 'pdf_verified_at', 'pdf_verification_note']


def cmd_pdf_page_verification_sheet(args):
    rows = promoted_evidence_rows(getattr(args, 'paper_id', None), getattr(args, 'candidate_id', None)) if getattr(args, 'promoted_only', False) else read_evidence_for_audit()
    out_path = resolve_root_path(args.output) if getattr(args, 'output', None) else ROOT/f'reports/review/pdf_page_verification_sheet_{today()}.csv'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_rows = []
    for row in rows:
        out_rows.append({
            'candidate_id': row.get('_candidate_id') or candidate_id_from_risk_note(row.get('risk_note')),
            'evidence_id': row.get('evidence_id') or '',
            'citekey': row.get('citekey') or '',
            'exact_quote': row.get('exact_quote') or row.get('quote') or '',
            'current_section': row.get('section') or '',
            'source_location': row.get('source_location') or '',
            'page_blank': str(not bool(str(row.get('page') or '').strip())).lower(),
            'pdf_page_to_fill': '',
            'pdf_verified_by': '',
            'pdf_verified_at': '',
            'pdf_verification_note': '',
        })
    with out_path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=PDF_PAGE_VERIFICATION_FIELDS)
        writer.writeheader(); writer.writerows(out_rows)
    report = ROOT/f'reports/review/pdf_page_verification_sheet_{today()}.md'
    report.parent.mkdir(parents=True, exist_ok=True)
    matrix = evidence_matrix_metrics()
    lines = [f'# PDF Page Verification Sheet {today()}\n', '## Summary\n']
    lines.append(f'- output_csv: {path_for_report(out_path)}\n')
    lines.append(f'- row_count: {len(out_rows)}\n')
    lines.append(f'- page_blank_count: {sum(1 for r in out_rows if r.get("page_blank") == "true")}\n')
    lines.append(f'- evidence_matrix_row_count: {matrix["row_count"]}\n')
    lines.append(f'- evidence_matrix_sha256: {matrix["sha256"]}\n')
    lines.append('\n## Instructions\n')
    lines.append('- Fill `pdf_page_to_fill`, `pdf_verified_by`, `pdf_verified_at`, and `pdf_verification_note` manually after checking the original PDF/TEI.\n')
    lines.append('- Do not set Evidence Matrix `verified=true` from this sheet automatically.\n')
    report.write_text(''.join(lines), encoding='utf-8')
    print(out_path)
    print(report)
    print(f"row_count={len(out_rows)}")
    print(f"page_blank_count={sum(1 for r in out_rows if r.get('page_blank') == 'true')}")
    print(f"evidence_matrix_row_count={matrix['row_count']}")
    log(f"pdf-page-verification-sheet 실행: rows={len(out_rows)}, output={path_for_report(out_path)}")


def pdf_path_for_paper_id(paper_id):
    if not paper_id:
        return None, 'missing_paper_id'
    try:
        c = conn()
        row = c.execute('SELECT local_pdf_path FROM papers WHERE id=?', (paper_id,)).fetchone()
        c.close()
    except Exception as e:
        return None, f'db_lookup_failed:{e}'
    if not row or not row['local_pdf_path']:
        return None, 'missing_local_pdf_path'
    path = ROOT/row['local_pdf_path']
    if not path.exists():
        return path, 'pdf_path_not_found'
    return path, ''


def normalize_pdf_text(value):
    text = str(value or '').replace('\u00ad', '')
    text = re.sub(r'(\w)-\s+(\w)', r'\1\2', text)
    text = re.sub(r'\s+', ' ', text).strip().lower()
    return text


def compact_pdf_text(value):
    return re.sub(r'[^a-z0-9가-힣]+', '', normalize_pdf_text(value))


def locate_quote_pages_in_pdf(pdf_path, quote):
    try:
        import fitz
    except Exception as e:
        return [], 'pymupdf_missing', f'PyMuPDF unavailable: {e}'
    if not pdf_path or not Path(pdf_path).exists():
        return [], 'missing_pdf', 'PDF file is missing'
    norm_quote = normalize_pdf_text(quote)
    compact_quote = compact_pdf_text(quote)
    exact_pages = []
    compact_pages = []
    try:
        doc = fitz.open(str(pdf_path))
        for index, page in enumerate(doc, 1):
            text = page.get_text('text')
            norm_page = normalize_pdf_text(text)
            if norm_quote and norm_quote in norm_page:
                exact_pages.append(index)
                continue
            if compact_quote and compact_quote in compact_pdf_text(text):
                compact_pages.append(index)
    except Exception as e:
        return [], 'pdf_read_error', str(e)
    if exact_pages:
        return exact_pages, 'normalized_exact_substring', ''
    if compact_pages:
        return compact_pages, 'compact_alnum_substring', ''
    return [], 'not_found', ''


PDF_PAGE_LOCATOR_FIELDS = ['candidate_id', 'evidence_id', 'paper_id', 'citekey', 'exact_quote', 'current_page', 'candidate_pages', 'candidate_page_count', 'match_method', 'confidence', 'pdf_path', 'pdf_error', 'verified_should_remain', 'note']


def write_pdf_page_locator_report(result):
    out = ROOT/f'reports/review/pdf_page_locator_candidates_{today()}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# PDF Page Locator Candidates {today()}\n', '## Summary\n']
    for key in ['promoted_only', 'paper_id_filter', 'row_count', 'found_count', 'not_found_count', 'single_page_count', 'multi_page_count', 'output_csv', 'evidence_matrix_row_count', 'evidence_matrix_sha256']:
        lines.append(f'- {key}: {result.get(key)}\n')
    lines.append('\n## Page Candidates\n')
    lines.append('| candidate_id | evidence_id | pages | method | confidence | note |\n')
    lines.append('|---|---|---|---|---|---|\n')
    for row in result.get('rows', []):
        note = str(row.get('note') or '').replace('|', '\\|')
        lines.append(f"| `{row.get('candidate_id')}` | `{row.get('evidence_id')}` | {row.get('candidate_pages') or '(none)'} | {row.get('match_method')} | {row.get('confidence')} | {note} |\n")
    lines.append('\n## Safety Notes\n')
    lines.append('- This command is a locator assistant only. It does not modify Evidence Matrix and does not set `verified=true`.\n')
    lines.append('- PM/user must manually inspect the PDF page before copying any page number into verification sheets or future metadata patches.\n')
    out.write_text(''.join(lines), encoding='utf-8')
    return out


def cmd_locate_pdf_pages(args):
    rows = promoted_evidence_rows(getattr(args, 'paper_id', None), getattr(args, 'candidate_id', None)) if getattr(args, 'promoted_only', False) else read_evidence_for_audit()
    out_path = resolve_root_path(args.output) if getattr(args, 'output', None) else ROOT/f'reports/review/pdf_page_locator_candidates_{today()}.csv'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_rows = []
    for row in rows:
        pdf_path, pdf_error = pdf_path_for_paper_id(row.get('paper_id'))
        pages, method, locate_error = locate_quote_pages_in_pdf(pdf_path, row.get('exact_quote') or row.get('quote') or '') if not pdf_error else ([], 'not_run', pdf_error)
        page_text = ';'.join(str(p) for p in pages)
        if len(pages) == 1 and method == 'normalized_exact_substring':
            confidence = 'high'
        elif len(pages) == 1:
            confidence = 'medium'
        elif len(pages) > 1:
            confidence = 'low_multiple_pages'
        else:
            confidence = 'none'
        note = 'candidate page requires manual PDF confirmation'
        if locate_error:
            note = locate_error
        out_rows.append({
            'candidate_id': row.get('_candidate_id') or candidate_id_from_risk_note(row.get('risk_note')),
            'evidence_id': row.get('evidence_id') or '',
            'paper_id': row.get('paper_id') or '',
            'citekey': row.get('citekey') or '',
            'exact_quote': row.get('exact_quote') or row.get('quote') or '',
            'current_page': row.get('page') or '',
            'candidate_pages': page_text,
            'candidate_page_count': len(pages),
            'match_method': method,
            'confidence': confidence,
            'pdf_path': path_for_report(pdf_path) if pdf_path else '',
            'pdf_error': locate_error or pdf_error,
            'verified_should_remain': 'false',
            'note': note,
        })
    with out_path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=PDF_PAGE_LOCATOR_FIELDS)
        writer.writeheader(); writer.writerows(out_rows)
    matrix = evidence_matrix_metrics()
    found_count = sum(1 for r in out_rows if str(r.get('candidate_pages') or '').strip())
    multi_count = sum(1 for r in out_rows if int(r.get('candidate_page_count') or 0) > 1)
    result = {
        'promoted_only': str(bool(getattr(args, 'promoted_only', False))).lower(),
        'paper_id_filter': getattr(args, 'paper_id', None) or '(none)',
        'row_count': len(out_rows),
        'found_count': found_count,
        'not_found_count': len(out_rows) - found_count,
        'single_page_count': sum(1 for r in out_rows if int(r.get('candidate_page_count') or 0) == 1),
        'multi_page_count': multi_count,
        'output_csv': path_for_report(out_path),
        'evidence_matrix_row_count': matrix['row_count'],
        'evidence_matrix_sha256': matrix['sha256'],
        'rows': out_rows,
    }
    report = write_pdf_page_locator_report(result)
    print(out_path)
    print(report)
    for key in ['row_count', 'found_count', 'not_found_count', 'single_page_count', 'multi_page_count', 'evidence_matrix_row_count', 'evidence_matrix_sha256']:
        print(f"{key}={result.get(key)}")
    log(f"locate-pdf-pages 실행: promoted_only={result['promoted_only']}, found={found_count}/{len(out_rows)}, output={path_for_report(out_path)}")


PAGE_METADATA_PATCH_REQUIRED_FIELDS = ['candidate_id', 'evidence_id', 'proposed_page', 'proposed_source_location', 'verified_should_remain']


def validate_page_metadata_patch(patch_path):
    patch_path = resolve_root_path(patch_path)
    errors = []
    warnings = []
    patch_rows = []
    patch_header = []
    if not patch_path.exists():
        errors.append(f'missing page metadata patch preview: {path_for_report(patch_path)}')
    else:
        try:
            patch_rows, patch_header = read_csv_rows_with_header(patch_path)
        except Exception as e:
            errors.append(f'failed to read patch preview: {e}')
    missing_headers = [h for h in PAGE_METADATA_PATCH_REQUIRED_FIELDS if h not in patch_header]
    if missing_headers:
        errors.append('page metadata patch preview missing required headers: ' + ', '.join(missing_headers))
    ids = [str(r.get('evidence_id') or '').strip() for r in patch_rows]
    duplicate_ids = sorted(k for k, v in count_values(ids).items() if k and v > 1)
    if duplicate_ids:
        errors.append(f'duplicate evidence_id in page metadata patch: {len(duplicate_ids)}')
    for row in patch_rows:
        label = row.get('candidate_id') or row.get('evidence_id') or '(blank row)'
        if not str(row.get('evidence_id') or '').strip():
            errors.append(f'{label}: missing evidence_id')
        if not str(row.get('candidate_id') or '').strip():
            errors.append(f'{label}: missing candidate_id')
        page = str(row.get('proposed_page') or '').strip()
        if not page:
            errors.append(f'{label}: missing proposed_page')
        elif not re.fullmatch(r'\d+(?:[-,;]\d+)*', page):
            warnings.append(f'{label}: proposed_page is non-standard `{page}`')
        source_location = str(row.get('proposed_source_location') or '').strip()
        if not source_location:
            errors.append(f'{label}: missing proposed_source_location')
        elif page and f'pdf_page={page}' not in source_location:
            errors.append(f'{label}: proposed_source_location does not contain pdf_page={page}')
        if str(row.get('verified_should_remain') or '').strip().lower() != 'false':
            errors.append(f'{label}: verified_should_remain must be false')
    return {
        'patch_path': patch_path,
        'rows': patch_rows,
        'header': patch_header,
        'duplicate_evidence_id_count': len(duplicate_ids),
        'warnings': warnings,
        'errors': errors,
    }


def write_page_metadata_apply_report(result):
    out = ROOT/f'reports/review/page_metadata_apply_{today()}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# Page Metadata Guarded Apply {today()}\n', '## Summary\n']
    for key in [
        'mode', 'patch_preview', 'patch_rows', 'matched_rows', 'updated_rows',
        'skipped_rows', 'blocked_rows', 'evidence_matrix_row_count_before',
        'evidence_matrix_row_count_after', 'evidence_matrix_sha256_before',
        'evidence_matrix_sha256_after', 'evidence_matrix_schema_changed',
        'backup_path', 'applied', 'verified_true_count_after',
        'verified_by_filled_count_after', 'verified_at_filled_count_after',
    ]:
        lines.append(f'- {key}: {result.get(key)}\n')
    lines.append('\n## Updated Evidence IDs\n')
    lines.extend([f"- `{item.get('evidence_id')}` candidate_id=`{item.get('candidate_id')}` page={item.get('proposed_page')}\n" for item in result.get('updated_items', [])] or ['- none\n'])
    lines.append('\n## Skipped Rows\n')
    lines.extend([f"- `{item.get('evidence_id')}`: {item.get('reason')}\n" for item in result.get('skipped_items', [])] or ['- none\n'])
    lines.append('\n## Warnings\n')
    lines.extend([f"- {w}\n" for w in result.get('warnings', [])] or ['- none\n'])
    lines.append('\n## Errors\n')
    lines.extend([f"- {e}\n" for e in result.get('errors', [])] or ['- none\n'])
    lines.append('\n## Safety Notes\n')
    lines.append('- This command updates only `page` and `source_location` for matched Evidence Matrix rows.\n')
    lines.append('- It does not set `verified=true`, `verified_by`, or `verified_at`.\n')
    out.write_text(''.join(lines), encoding='utf-8')
    return out


def cmd_apply_page_metadata(args):
    if args.apply and args.dry_run:
        raise SystemExit('ERROR: --apply and --dry-run cannot be used together')
    if not args.apply and not args.dry_run:
        raise SystemExit('ERROR: choose --dry-run or --apply')
    before = evidence_matrix_metrics()
    validation = validate_page_metadata_patch(args.from_preview)
    errors = list(validation.get('errors') or [])
    warnings = list(validation.get('warnings') or [])
    patch_rows = validation.get('rows') or []
    ev = ROOT/'matrices/evidence_matrix.csv'
    matrix_rows = []
    fieldnames = []
    if not ev.exists():
        errors.append(f'missing Evidence Matrix: {path_for_report(ev)}')
    else:
        try:
            matrix_rows, fieldnames = read_evidence_matrix_with_header()
        except Exception as e:
            errors.append(f'failed to read Evidence Matrix: {e}')
    for required in ['evidence_id', 'page', 'source_location', 'verified', 'verified_by', 'verified_at']:
        if fieldnames and required not in fieldnames:
            errors.append(f'Evidence Matrix missing required column: {required}')
    by_evidence_id = {str(row.get('evidence_id') or '').strip(): row for row in matrix_rows if str(row.get('evidence_id') or '').strip()}
    updated_items = []
    skipped_items = []
    blocked_items = []
    for patch in patch_rows:
        evidence_id = str(patch.get('evidence_id') or '').strip()
        candidate_id = str(patch.get('candidate_id') or '').strip()
        target = by_evidence_id.get(evidence_id)
        if not target:
            blocked_items.append({'evidence_id': evidence_id, 'candidate_id': candidate_id, 'reason': 'evidence_id_not_found'})
            continue
        if candidate_id and candidate_id_from_risk_note(target.get('risk_note')) and candidate_id_from_risk_note(target.get('risk_note')) != candidate_id:
            blocked_items.append({'evidence_id': evidence_id, 'candidate_id': candidate_id, 'reason': 'candidate_id_mismatch'})
            continue
        if str(target.get('verified') or '').strip().lower() != 'false':
            blocked_items.append({'evidence_id': evidence_id, 'candidate_id': candidate_id, 'reason': 'target_verified_not_false'})
            continue
        if str(target.get('verified_by') or '').strip() or str(target.get('verified_at') or '').strip():
            blocked_items.append({'evidence_id': evidence_id, 'candidate_id': candidate_id, 'reason': 'target_verified_fields_not_blank'})
            continue
        proposed_page = str(patch.get('proposed_page') or '').strip()
        proposed_source_location = str(patch.get('proposed_source_location') or '').strip()
        if str(target.get('page') or '').strip() == proposed_page and str(target.get('source_location') or '').strip() == proposed_source_location:
            skipped_items.append({'evidence_id': evidence_id, 'candidate_id': candidate_id, 'reason': 'already_applied'})
        else:
            updated_items.append({'evidence_id': evidence_id, 'candidate_id': candidate_id, 'proposed_page': proposed_page, 'proposed_source_location': proposed_source_location})
    if blocked_items:
        errors.append(f'blocked page metadata rows: {len(blocked_items)}')

    backup_path = ''
    applied = False
    if args.apply and not errors and updated_items:
        backup_dir = ROOT/'matrices/backups'
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir/f'evidence_matrix_before_page_metadata_apply_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        shutil.copy2(ev, backup)
        backup_path = path_for_report(backup)
        patches_by_id = {item['evidence_id']: item for item in updated_items}
        for row in matrix_rows:
            evidence_id = str(row.get('evidence_id') or '').strip()
            if evidence_id in patches_by_id:
                patch = patches_by_id[evidence_id]
                row['page'] = patch['proposed_page']
                row['source_location'] = patch['proposed_source_location']
                row['verified'] = 'false'
                if 'verified_by' in row:
                    row['verified_by'] = ''
                if 'verified_at' in row:
                    row['verified_at'] = ''
        with ev.open('w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader(); writer.writerows(matrix_rows)
        applied = True
    elif args.apply and not errors and not updated_items:
        warnings.append('no page metadata rows needed updating')

    after = evidence_matrix_metrics()
    comparison = compare_evidence_matrix_metrics(before, after)
    if args.dry_run:
        append_evidence_matrix_protection_errors(errors, comparison)
    else:
        if after['row_count'] != before['row_count']:
            errors.append(f'Evidence Matrix row count changed during page metadata apply: {before["row_count"]} -> {after["row_count"]}')
        if comparison['schema_changed']:
            errors.append('Evidence Matrix schema changed during page metadata apply')
    final_rows = read_evidence_for_audit()
    promoted_final = [r for r in final_rows if candidate_id_from_risk_note(r.get('risk_note'))]
    verified_true_count = sum(1 for r in promoted_final if str(r.get('verified') or '').strip().lower() == 'true')
    verified_by_filled_count = sum(1 for r in promoted_final if str(r.get('verified_by') or '').strip())
    verified_at_filled_count = sum(1 for r in promoted_final if str(r.get('verified_at') or '').strip())
    if verified_true_count:
        errors.append(f'promoted rows with verified=true after apply: {verified_true_count}')
    if verified_by_filled_count:
        errors.append(f'promoted rows with verified_by filled after apply: {verified_by_filled_count}')
    if verified_at_filled_count:
        errors.append(f'promoted rows with verified_at filled after apply: {verified_at_filled_count}')
    result = {
        'mode': 'apply' if args.apply else 'dry-run',
        'patch_preview': path_for_report(validation.get('patch_path')) if validation.get('patch_path') else args.from_preview,
        'patch_rows': len(patch_rows),
        'matched_rows': len(updated_items) + len(skipped_items),
        'updated_rows': len(updated_items) if args.apply else 0,
        'skipped_rows': len(skipped_items),
        'blocked_rows': len(blocked_items),
        'evidence_matrix_row_count_before': before['row_count'],
        'evidence_matrix_row_count_after': after['row_count'],
        'evidence_matrix_sha256_before': before['sha256'],
        'evidence_matrix_sha256_after': after['sha256'],
        'evidence_matrix_schema_changed': comparison['schema_changed'],
        'backup_path': backup_path or '(none)',
        'applied': str(applied).lower(),
        'verified_true_count_after': verified_true_count,
        'verified_by_filled_count_after': verified_by_filled_count,
        'verified_at_filled_count_after': verified_at_filled_count,
        'updated_items': updated_items,
        'skipped_items': skipped_items,
        'warnings': warnings,
        'errors': errors,
    }
    report = write_page_metadata_apply_report(result)
    print(report)
    for key in ['mode', 'patch_rows', 'matched_rows', 'updated_rows', 'skipped_rows', 'blocked_rows', 'evidence_matrix_row_count_before', 'evidence_matrix_row_count_after', 'backup_path', 'applied', 'verified_true_count_after']:
        print(f"{key}={result.get(key)}")
    log(f"apply-page-metadata {result['mode']} 실행: updated={result['updated_rows']}, skipped={result['skipped_rows']}, blocked={result['blocked_rows']}, errors={len(errors)}")
    if errors:
        raise SystemExit(1)


MANUSCRIPT_PREVIEW_REQUIRED_FIELDS = ['preview_id', 'target_file', 'target_heading', 'paragraph_role', 'evidence_ids', 'pages', 'proposed_paragraph', 'guard', 'manuscript_auto_insert_allowed', 'requires_pm_approval']
MANUSCRIPT_PATCH_PREVIEW_FIELDS = ['preview_id', 'target_file', 'target_heading', 'paragraph_role', 'insertion_status', 'blockers', 'evidence_ids', 'pages', 'guard', 'original_file_sha256', 'patched_file_sha256', 'proposed_paragraph']
MANUSCRIPT_APPLY_FIELDS = ['preview_id', 'target_file', 'target_heading', 'paragraph_role', 'apply_status', 'blockers', 'evidence_ids', 'pages', 'guard', 'file_sha256_before', 'expected_original_file_sha256', 'expected_patched_file_sha256', 'file_sha256_after', 'backup_path', 'proposed_paragraph']


def manuscript_flag(row, field):
    return str(row.get(field) or '').strip().lower() in {'true', '1', 'yes', 'y'}


def validate_manuscript_target(row):
    errors = []
    target_text = str(row.get('target_file') or '').strip()
    if not target_text:
        errors.append('missing target_file')
        return None, errors
    rel = Path(target_text)
    if rel.is_absolute() or '..' in rel.parts:
        errors.append('target_file must be a safe relative path')
        return None, errors
    path = (ROOT/rel).resolve()
    manuscript_root = (ROOT/'05_manuscript').resolve()
    try:
        path.relative_to(manuscript_root)
    except Exception:
        errors.append('target_file must stay under 05_manuscript')
    if path.suffix.lower() not in {'.qmd', '.md'}:
        errors.append('target_file must be .qmd or .md')
    if not path.exists():
        errors.append(f'target_file does not exist: {target_text}')
    return path, errors


def heading_level(heading):
    match = re.match(r'^(#{1,6})\s+', str(heading or '').strip())
    return len(match.group(1)) if match else 0


def section_end_index(lines, heading_index, heading):
    level = heading_level(heading)
    if not level:
        return heading_index + 1
    for idx in range(heading_index + 1, len(lines)):
        match = re.match(r'^(#{1,6})\s+', lines[idx].strip())
        if match and len(match.group(1)) <= level:
            return idx
    return len(lines)


def insert_paragraph_in_section(content, heading, paragraph):
    lines = content.splitlines()
    heading_text = str(heading or '').strip()
    heading_index = None
    for idx, line in enumerate(lines):
        if line.strip() == heading_text:
            heading_index = idx
            break
    if heading_index is None:
        return content, False
    insert_at = section_end_index(lines, heading_index, heading_text)
    block = []
    if insert_at > 0 and lines[insert_at - 1].strip():
        block.append('')
    block.append(str(paragraph or '').strip())
    block.append('')
    patched_lines = lines[:insert_at] + block + lines[insert_at:]
    return '\n'.join(patched_lines).rstrip() + '\n', True


def unified_manuscript_diff(rel_path, original, patched):
    return ''.join(difflib.unified_diff(
        original.splitlines(keepends=True),
        patched.splitlines(keepends=True),
        fromfile='a/' + str(rel_path).replace('\\', '/'),
        tofile='b/' + str(rel_path).replace('\\', '/'),
        lineterm='\n',
    ))


def write_manuscript_patch_preview_report(result):
    md_path = result['md_path']
    lines = [f"# Manuscript Patch Preview {today()}\n", '## Summary\n']
    for key in [
        'input_preview', 'csv_path', 'diff_path', 'patch_rows', 'ready_rows',
        'blocked_rows', 'target_file_count', 'manuscript_files_modified',
        'evidence_matrix_row_count_before', 'evidence_matrix_row_count_after',
        'evidence_matrix_sha256_before', 'evidence_matrix_sha256_after',
    ]:
        lines.append(f"- {key}: {result.get(key)}\n")
    lines.append('\n## Policy\n')
    lines.append('- This command writes patch-preview artifacts only. It does not modify manuscript files.\n')
    lines.append('- `manuscript_auto_insert_allowed` must remain false in the source preview.\n')
    lines.append('- A separate explicit user approval is required before any actual manuscript edit.\n')
    lines.append('- Evidence rows must not be changed to `verified=true` by this preview.\n')
    lines.append('\n## Rows\n')
    for row in result.get('rows', []):
        lines.append(f"### {row.get('preview_id')} — `{row.get('target_file')}` / {row.get('target_heading')}\n")
        lines.append(f"- status: {row.get('insertion_status')}\n")
        lines.append(f"- blockers: {row.get('blockers') or 'none'}\n")
        lines.append(f"- evidence_ids: {row.get('evidence_ids')}\n")
        lines.append(f"- pages: {row.get('pages')}\n")
        lines.append(f"- guard: {row.get('guard')}\n\n")
        lines.append(str(row.get('proposed_paragraph') or '').strip() + '\n\n')
    lines.append('## Warnings\n')
    lines.extend([f"- {w}\n" for w in result.get('warnings', [])] or ['- none\n'])
    lines.append('\n## Errors\n')
    lines.extend([f"- {e}\n" for e in result.get('errors', [])] or ['- none\n'])
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(''.join(lines), encoding='utf-8')
    return md_path


def cmd_manuscript_patch_preview(args):
    before = evidence_matrix_metrics()
    preview_path = resolve_root_path(args.from_preview)
    prefix = resolve_root_path(args.output_prefix) if args.output_prefix else ROOT/f'reports/review/manuscript_patch_preview_{today()}'
    csv_path = prefix.with_suffix('.csv')
    md_path = prefix.with_suffix('.md')
    diff_path = prefix.with_suffix('.diff')
    errors = []
    warnings = []
    rows = []
    header = []
    if not preview_path.exists():
        errors.append(f'missing manuscript insertion preview: {path_for_report(preview_path)}')
    else:
        try:
            rows, header = read_csv_rows_with_header(preview_path)
        except Exception as e:
            errors.append(f'failed to read manuscript insertion preview: {e}')
    missing_headers = [field for field in MANUSCRIPT_PREVIEW_REQUIRED_FIELDS if field not in header]
    if missing_headers:
        errors.append('manuscript insertion preview missing required headers: ' + ', '.join(missing_headers))

    original_by_path = {}
    patched_by_path = {}
    output_rows = []
    for row in rows:
        row_errors = []
        preview_id = str(row.get('preview_id') or '').strip() or '(blank preview_id)'
        for field in MANUSCRIPT_PREVIEW_REQUIRED_FIELDS:
            if not str(row.get(field) or '').strip():
                row_errors.append(f'missing {field}')
        target_path, target_errors = validate_manuscript_target(row)
        row_errors.extend(target_errors)
        if manuscript_flag(row, 'manuscript_auto_insert_allowed'):
            row_errors.append('manuscript_auto_insert_allowed must remain false')
        if not manuscript_flag(row, 'requires_pm_approval'):
            row_errors.append('requires_pm_approval must be true')
        paragraph = str(row.get('proposed_paragraph') or '').strip()
        original_sha = ''
        patched_sha = ''
        inserted = False
        if target_path and target_path.exists() and not target_errors:
            rel = path_for_report(target_path)
            if target_path not in original_by_path:
                original_by_path[target_path] = target_path.read_text(encoding='utf-8')
                patched_by_path[target_path] = original_by_path[target_path]
            original_sha = file_sha256(target_path)
            current = patched_by_path[target_path]
            if paragraph and paragraph in original_by_path[target_path]:
                row_errors.append('proposed_paragraph already exists in manuscript')
            elif paragraph and not row_errors:
                patched, inserted = insert_paragraph_in_section(current, row.get('target_heading'), paragraph)
                if not inserted:
                    row_errors.append('target_heading not found')
                else:
                    patched_by_path[target_path] = patched
                    patched_sha = hashlib.sha256(patched.encode('utf-8')).hexdigest()
            if not patched_sha:
                patched_sha = hashlib.sha256(current.encode('utf-8')).hexdigest()
        status = 'ready' if not row_errors and inserted else 'blocked'
        if row_errors:
            errors.extend([f'{preview_id}: {e}' for e in row_errors])
        output_rows.append({
            'preview_id': row.get('preview_id', ''),
            'target_file': row.get('target_file', ''),
            'target_heading': row.get('target_heading', ''),
            'paragraph_role': row.get('paragraph_role', ''),
            'insertion_status': status,
            'blockers': ';'.join(row_errors),
            'evidence_ids': row.get('evidence_ids', ''),
            'pages': row.get('pages', ''),
            'guard': row.get('guard', ''),
            'original_file_sha256': original_sha,
            'patched_file_sha256': patched_sha,
            'proposed_paragraph': paragraph,
        })

    diff_chunks = []
    final_patched_sha_by_target = {}
    for path in sorted(patched_by_path, key=lambda p: path_for_report(p)):
        original = original_by_path[path]
        patched = patched_by_path[path]
        final_patched_sha_by_target[path_for_report(path).replace('\\', '/')] = hashlib.sha256(patched.encode('utf-8')).hexdigest()
        if original != patched:
            diff_chunks.append(unified_manuscript_diff(path_for_report(path), original, patched))
    for out_row in output_rows:
        target_key = str(out_row.get('target_file') or '').replace('\\', '/')
        if target_key in final_patched_sha_by_target:
            out_row['patched_file_sha256'] = final_patched_sha_by_target[target_key]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=MANUSCRIPT_PATCH_PREVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)
    diff_path.write_text('\n'.join(diff_chunks), encoding='utf-8')

    after = evidence_matrix_metrics()
    comparison = compare_evidence_matrix_metrics(before, after)
    append_evidence_matrix_protection_errors(errors, comparison)
    changed_manuscript_files = []
    for path, original in original_by_path.items():
        if path.exists() and path.read_text(encoding='utf-8') != original:
            changed_manuscript_files.append(path_for_report(path))
    if changed_manuscript_files:
        errors.append('manuscript files modified unexpectedly: ' + ', '.join(changed_manuscript_files))
    result = {
        'input_preview': path_for_report(preview_path),
        'csv_path': path_for_report(csv_path),
        'md_path': md_path,
        'diff_path': path_for_report(diff_path),
        'patch_rows': len(output_rows),
        'ready_rows': sum(1 for r in output_rows if r.get('insertion_status') == 'ready'),
        'blocked_rows': sum(1 for r in output_rows if r.get('insertion_status') != 'ready'),
        'target_file_count': len(original_by_path),
        'manuscript_files_modified': bool(changed_manuscript_files),
        'evidence_matrix_row_count_before': before['row_count'],
        'evidence_matrix_row_count_after': after['row_count'],
        'evidence_matrix_sha256_before': before['sha256'],
        'evidence_matrix_sha256_after': after['sha256'],
        'rows': output_rows,
        'warnings': warnings,
        'errors': errors,
    }
    write_manuscript_patch_preview_report(result)
    print(md_path)
    for key in ['patch_rows', 'ready_rows', 'blocked_rows', 'target_file_count', 'manuscript_files_modified', 'evidence_matrix_sha256_before', 'evidence_matrix_sha256_after']:
        print(f"{key}={result.get(key)}")
    log(f"manuscript-patch-preview 실행: rows={len(output_rows)}, ready={result['ready_rows']}, blocked={result['blocked_rows']}, modified={result['manuscript_files_modified']}")
    if errors:
        raise SystemExit(1)


def write_manuscript_apply_report(result):
    md_path = result['md_path']
    lines = [f"# Manuscript Guarded Apply {today()}\n", '## Summary\n']
    for key in [
        'mode', 'input_preview', 'csv_path', 'row_count', 'ready_rows',
        'applied_rows', 'would_apply_rows', 'blocked_rows', 'target_file_count',
        'backup_count', 'applied', 'evidence_matrix_row_count_before',
        'evidence_matrix_row_count_after', 'evidence_matrix_sha256_before',
        'evidence_matrix_sha256_after',
    ]:
        lines.append(f"- {key}: {result.get(key)}\n")
    lines.append('\n## Policy\n')
    lines.append('- Applies only rows previously marked `ready` by `manuscript-patch-preview`.\n')
    lines.append('- Requires explicit `--apply`; otherwise it only performs dry-run validation.\n')
    lines.append('- Current manuscript file SHA256 values must match the preview original SHA256 values before writing.\n')
    lines.append('- Final manuscript file SHA256 values must match the preview patched SHA256 values after insertion.\n')
    lines.append('- Evidence Matrix rows and SHA256 must remain unchanged.\n')
    lines.append('- Evidence rows must not be changed to `verified=true` by this manuscript apply step.\n')
    lines.append('\n## Target Files\n')
    for target in result.get('targets', []):
        lines.append(f"### `{target.get('target_file')}`\n")
        for key in ['file_sha256_before', 'expected_patched_file_sha256', 'predicted_file_sha256_after', 'file_sha256_after', 'backup_path']:
            lines.append(f"- {key}: {target.get(key)}\n")
        lines.append('\n')
    lines.append('## Rows\n')
    for row in result.get('rows', []):
        lines.append(f"### {row.get('preview_id')} — `{row.get('target_file')}` / {row.get('target_heading')}\n")
        lines.append(f"- status: {row.get('apply_status')}\n")
        lines.append(f"- blockers: {row.get('blockers') or 'none'}\n")
        lines.append(f"- evidence_ids: {row.get('evidence_ids')}\n")
        lines.append(f"- pages: {row.get('pages')}\n")
        lines.append(f"- guard: {row.get('guard')}\n")
        lines.append(f"- backup_path: {row.get('backup_path') or '(none)'}\n\n")
        lines.append(str(row.get('proposed_paragraph') or '').strip() + '\n\n')
    lines.append('## Warnings\n')
    lines.extend([f"- {w}\n" for w in result.get('warnings', [])] or ['- none\n'])
    lines.append('\n## Errors\n')
    lines.extend([f"- {e}\n" for e in result.get('errors', [])] or ['- none\n'])
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(''.join(lines), encoding='utf-8')
    return md_path


def cmd_apply_manuscript_patch(args):
    if args.apply and args.dry_run:
        raise SystemExit('ERROR: --apply and --dry-run cannot be used together')
    if not args.apply and not args.dry_run:
        raise SystemExit('ERROR: choose --dry-run or --apply')
    mode = 'apply' if args.apply else 'dry-run'
    before = evidence_matrix_metrics()
    preview_path = resolve_root_path(args.from_preview)
    prefix = resolve_root_path(args.output_prefix) if args.output_prefix else ROOT/f'reports/review/manuscript_apply_{today()}'
    csv_path = prefix.with_suffix('.csv')
    md_path = prefix.with_suffix('.md')
    errors = []
    warnings = []
    rows = []
    header = []
    if not preview_path.exists():
        errors.append(f'missing manuscript patch preview: {path_for_report(preview_path)}')
    else:
        try:
            rows, header = read_csv_rows_with_header(preview_path)
        except Exception as e:
            errors.append(f'failed to read manuscript patch preview: {e}')
    missing_headers = [field for field in MANUSCRIPT_PATCH_PREVIEW_FIELDS if field not in header]
    if missing_headers:
        errors.append('manuscript patch preview missing required headers: ' + ', '.join(missing_headers))

    file_state = {}
    output_rows = []
    for row in rows:
        row_errors = []
        preview_id = str(row.get('preview_id') or '').strip() or '(blank preview_id)'
        for field in ['preview_id', 'target_file', 'target_heading', 'insertion_status', 'original_file_sha256', 'patched_file_sha256', 'proposed_paragraph']:
            if not str(row.get(field) or '').strip():
                row_errors.append(f'missing {field}')
        if str(row.get('insertion_status') or '').strip().lower() != 'ready':
            row_errors.append('insertion_status must be ready')
        if str(row.get('blockers') or '').strip():
            row_errors.append('patch preview row has blockers')
        target_path, target_errors = validate_manuscript_target(row)
        row_errors.extend(target_errors)
        paragraph = str(row.get('proposed_paragraph') or '').strip()
        expected_original_sha = str(row.get('original_file_sha256') or '').strip()
        expected_patched_sha = str(row.get('patched_file_sha256') or '').strip()
        file_sha_before = ''
        file_sha_after = ''
        path_key = ''
        if target_path and target_path.exists() and not target_errors:
            path_key = path_for_report(target_path).replace('\\', '/')
            if target_path not in file_state:
                original_text = target_path.read_text(encoding='utf-8')
                file_state[target_path] = {
                    'target_file': path_key,
                    'original': original_text,
                    'patched': original_text,
                    'file_sha_before': file_sha256(target_path),
                    'expected_original': set(),
                    'expected_patched': set(),
                    'row_indexes': [],
                    'predicted_sha': '',
                    'file_sha_after': '',
                    'backup_path': '',
                }
            state = file_state[target_path]
            state['expected_original'].add(expected_original_sha)
            state['expected_patched'].add(expected_patched_sha)
            state['row_indexes'].append(len(output_rows))
            file_sha_before = state['file_sha_before']
            if file_sha_before != expected_original_sha:
                row_errors.append(f'current file sha256 mismatch: expected {expected_original_sha}, got {file_sha_before}')
            elif paragraph and paragraph in state['original']:
                row_errors.append('proposed_paragraph already exists in current manuscript')
            elif not row_errors:
                patched, inserted = insert_paragraph_in_section(state['patched'], row.get('target_heading'), paragraph)
                if not inserted:
                    row_errors.append('target_heading not found')
                else:
                    state['patched'] = patched
                    file_sha_after = hashlib.sha256(patched.encode('utf-8')).hexdigest()
        apply_status = 'ready_to_apply' if not row_errors else 'blocked'
        if row_errors:
            errors.extend([f'{preview_id}: {e}' for e in row_errors])
        output_rows.append({
            'preview_id': row.get('preview_id', ''),
            'target_file': row.get('target_file', ''),
            'target_heading': row.get('target_heading', ''),
            'paragraph_role': row.get('paragraph_role', ''),
            'apply_status': apply_status,
            'blockers': ';'.join(row_errors),
            'evidence_ids': row.get('evidence_ids', ''),
            'pages': row.get('pages', ''),
            'guard': row.get('guard', ''),
            'file_sha256_before': file_sha_before,
            'expected_original_file_sha256': expected_original_sha,
            'expected_patched_file_sha256': expected_patched_sha,
            'file_sha256_after': file_sha_after,
            'backup_path': '',
            'proposed_paragraph': paragraph,
        })

    for path, state in file_state.items():
        expected_originals = {v for v in state['expected_original'] if v}
        expected_patcheds = {v for v in state['expected_patched'] if v}
        if len(expected_originals) != 1:
            errors.append(f"{state['target_file']}: inconsistent original_file_sha256 values")
        if len(expected_patcheds) != 1:
            errors.append(f"{state['target_file']}: inconsistent patched_file_sha256 values")
        predicted_sha = hashlib.sha256(state['patched'].encode('utf-8')).hexdigest()
        state['predicted_sha'] = predicted_sha
        if len(expected_patcheds) == 1:
            expected_final = next(iter(expected_patcheds))
            if predicted_sha != expected_final:
                errors.append(f"{state['target_file']}: predicted patched sha256 mismatch: expected {expected_final}, got {predicted_sha}")
        for idx in state['row_indexes']:
            output_rows[idx]['file_sha256_after'] = predicted_sha

    applied = False
    if not errors and args.apply:
        backup_root = ROOT/'05_manuscript/backups'/f'manuscript_before_guarded_apply_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
        for path, state in file_state.items():
            rel = path.relative_to(ROOT/'05_manuscript')
            backup_path = backup_root/rel
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup_path)
            state['backup_path'] = path_for_report(backup_path)
        for path, state in file_state.items():
            with path.open('w', encoding='utf-8', newline='\n') as f:
                f.write(state['patched'])
        applied = True
        for path, state in file_state.items():
            state['file_sha_after'] = file_sha256(path)
            if state['file_sha_after'] != state['predicted_sha']:
                errors.append(f"{state['target_file']}: file sha256 after apply mismatch: expected {state['predicted_sha']}, got {state['file_sha_after']}")
    elif not errors:
        for state in file_state.values():
            state['file_sha_after'] = state['predicted_sha']

    state_by_target = {state['target_file']: state for state in file_state.values()}
    if not errors:
        for out_row in output_rows:
            target_key = str(out_row.get('target_file') or '').replace('\\', '/')
            state = state_by_target.get(target_key)
            if state:
                out_row['file_sha256_after'] = state.get('file_sha_after') or state.get('predicted_sha')
                out_row['backup_path'] = state.get('backup_path') or ''
            if out_row.get('apply_status') == 'ready_to_apply':
                out_row['apply_status'] = 'applied' if args.apply else 'would_apply'

    after = evidence_matrix_metrics()
    comparison = compare_evidence_matrix_metrics(before, after)
    append_evidence_matrix_protection_errors(errors, comparison)

    targets = []
    for path in sorted(file_state, key=lambda p: path_for_report(p)):
        state = file_state[path]
        expected_patched = sorted(v for v in state['expected_patched'] if v)
        targets.append({
            'target_file': state['target_file'],
            'file_sha256_before': state['file_sha_before'],
            'expected_patched_file_sha256': expected_patched[0] if expected_patched else '',
            'predicted_file_sha256_after': state.get('predicted_sha', ''),
            'file_sha256_after': state.get('file_sha_after', ''),
            'backup_path': state.get('backup_path') or '(none)',
        })

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=MANUSCRIPT_APPLY_FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)
    result = {
        'mode': mode,
        'input_preview': path_for_report(preview_path),
        'csv_path': path_for_report(csv_path),
        'md_path': md_path,
        'row_count': len(output_rows),
        'ready_rows': sum(1 for r in output_rows if r.get('apply_status') in {'ready_to_apply', 'would_apply', 'applied'}),
        'applied_rows': sum(1 for r in output_rows if r.get('apply_status') == 'applied'),
        'would_apply_rows': sum(1 for r in output_rows if r.get('apply_status') == 'would_apply'),
        'blocked_rows': sum(1 for r in output_rows if r.get('apply_status') == 'blocked'),
        'target_file_count': len(file_state),
        'backup_count': sum(1 for state in file_state.values() if state.get('backup_path')),
        'applied': str(applied).lower(),
        'evidence_matrix_row_count_before': before['row_count'],
        'evidence_matrix_row_count_after': after['row_count'],
        'evidence_matrix_sha256_before': before['sha256'],
        'evidence_matrix_sha256_after': after['sha256'],
        'targets': targets,
        'rows': output_rows,
        'warnings': warnings,
        'errors': errors,
    }
    report = write_manuscript_apply_report(result)
    print(report)
    for key in ['mode', 'row_count', 'ready_rows', 'applied_rows', 'would_apply_rows', 'blocked_rows', 'target_file_count', 'backup_count', 'applied', 'evidence_matrix_sha256_before', 'evidence_matrix_sha256_after']:
        print(f"{key}={result.get(key)}")
    log(f"apply-manuscript-patch {mode} 실행: rows={len(output_rows)}, applied_rows={result['applied_rows']}, would_apply_rows={result['would_apply_rows']}, blocked={result['blocked_rows']}, errors={len(errors)}")
    if errors:
        raise SystemExit(1)


def duplicate_count(values):
    counts = defaultdict(int)
    for value in values:
        counts[value] += 1
    return sum(1 for value, count in counts.items() if value and count > 1)


def write_evidence_candidate_report(result):
    out = ROOT/f'reports/audit_reports/evidence_candidates_{today()}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# Evidence Candidates {today()}\n', '## Summary\n']
    for key in ['paper_id', 'citekey', 'candidate_count', 'applied', 'output_csv', 'verified_false_count', 'duplicate_candidate_id_count', 'evidence_matrix_row_count_before', 'evidence_matrix_row_count_after']:
        lines.append(f'- {key}: {result.get(key)}\n')
    lines.append('\n## Warnings\n')
    lines.extend([f"- {w}\n" for w in result.get('warnings', [])] or ['- none\n'])
    lines.append('\n## Errors\n')
    lines.extend([f"- {e}\n" for e in result.get('errors', [])] or ['- none\n'])
    out.write_text(''.join(lines), encoding='utf-8')
    return out


def cmd_extract_evidence_candidates(args):
    if args.apply and args.dry_run:
        raise SystemExit('ERROR: --apply and --dry-run cannot be used together')
    if not args.apply and not args.dry_run:
        raise SystemExit('ERROR: choose --dry-run or --apply')
    before = file_row_count(ROOT/'matrices/evidence_matrix.csv')
    out_csv = ROOT/'matrices/evidence_candidates.csv'
    warnings = []
    errors = []
    try:
        candidates = build_evidence_candidates(args.paper_id)
    except Exception as e:
        candidates = []
        errors.append(str(e))
    citekey = candidates[0]['citekey'] if candidates else ''
    existing = read_evidence_candidates(out_csv)
    existing_by_id = {r.get('candidate_id'): r for r in existing if r.get('candidate_id')}
    if args.apply and not errors:
        for row in candidates:
            row['verified'] = 'false'
            existing_by_id[row['candidate_id']] = row
        merged = list(existing_by_id.values())
        write_evidence_candidates(out_csv, merged)
    after = file_row_count(ROOT/'matrices/evidence_matrix.csv')
    result_rows = read_evidence_candidates(out_csv) if out_csv.exists() else []
    rows_for_check = result_rows if args.apply else candidates
    verified_false_count = sum(1 for r in rows_for_check if str(r.get('verified', '')).lower() == 'false')
    dup_count = duplicate_count([r.get('candidate_id') for r in rows_for_check])
    if after != before:
        errors.append('evidence_matrix.csv row count changed; this command must not modify it')
    if verified_false_count != len(rows_for_check):
        errors.append('one or more candidates are not verified=false')
    result = {
        'paper_id': args.paper_id,
        'citekey': citekey,
        'candidate_count': len(candidates),
        'applied': str(bool(args.apply and not errors)).lower(),
        'output_csv': path_for_report(out_csv),
        'verified_false_count': verified_false_count,
        'duplicate_candidate_id_count': dup_count,
        'evidence_matrix_row_count_before': before,
        'evidence_matrix_row_count_after': after,
        'warnings': warnings,
        'errors': errors,
    }
    report = write_evidence_candidate_report(result)
    print(report)
    print(f"candidate_count={len(candidates)}")
    print(f"verified_false_count={verified_false_count}")
    print(f"duplicate_candidate_id_count={dup_count}")
    log(f"extract-evidence-candidates 실행: paper_id={args.paper_id}, apply={args.apply}, candidates={len(candidates)}, report={report.name}")
    if errors:
        raise SystemExit(1)


def should_backup_file(path):
    parts = {p.lower() for p in path.parts}
    if '.venv' in parts:
        return False
    if path.suffix.lower() == '.zip':
        return False
    rel = path.relative_to(ROOT)
    rel_text = str(rel).replace('\\', '/').lower()
    if rel_text.startswith('data/pdfs/') or rel_text.startswith('data/parsed/'):
        return False
    return path.is_file()


def cmd_backup(args):
    include_dirs = ['scripts', 'config', 'docs', 'matrices', 'manuscript', 'research_design', 'reports', 'notes']
    out_dir = ROOT/'backups'
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir/f"paperops_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    count = 0
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for dirname in include_dirs:
            base = ROOT/dirname
            if not base.exists():
                continue
            for path in base.rglob('*'):
                if not should_backup_file(path):
                    continue
                zf.write(path, path.relative_to(ROOT))
                count += 1
    log(f'backup 실행: {out}, files={count}')
    print(out)
    print('files:', count)


def write_if_missing(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return False
    path.write_text(content, encoding='utf-8', newline='\n')
    return True


def cmd_init_thesis_os(args):
    files = {
        '01_literature/README.md': '# 01 Literature\n\nPaper discovery, screening, paper cards, and bibliography assets.\n',
        '01_literature/screening_matrix.csv': 'paper_id,citekey,title,year,venue,score,status,decision,reason\n',
        '01_literature/evidence_matrix.csv': 'paper_id,citekey,claim_type,claim,quote,page,section,confidence,use_in_section,verified\n',
        '01_literature/references.bib': '% Better BibTeX export target or curated thesis bibliography.\n',
        '02_research_design/problem_definition.md': '# Problem Definition\n\n## Problem\n\n## Baseline\n\n## Proposed Artifact\n\n## Failure Criteria\n',
        '02_research_design/research_questions.md': '# Research Questions\n\n- RQ1:\n- RQ2:\n- RQ3:\n',
        '02_research_design/evaluation_plan.md': '# Evaluation Plan\n\n## Metrics\n\n## Baselines\n\n## Ablation\n',
        '03_artifact/README.md': '# 03 Artifact\n\nSystem design, ontology files, schemas, and implementation notes.\n',
        '03_artifact/ontology.ttl': '@prefix ex: <https://example.org/paperops/> .\n\nex:PaperOps a ex:ResearchArtifact .\n',
        '03_artifact/system_design.md': '# System Design\n\n## Components\n\n## Data Flow\n\n## Traceability Model\n',
        '04_experiments/README.md': '# 04 Experiments\n\nExperiment configs, logs, and evaluation outputs.\n',
        '04_experiments/experiment_log.csv': 'date,experiment_id,question,config,metric,result,notes\n',
        '04_experiments/evaluation_results.csv': 'paper_id,metric,value,baseline,notes\n',
        '05_manuscript/thesis.qmd': '---\ntitle: "PaperOps Thesis Draft"\nformat:\n  html: default\nbibliography: references.bib\n---\n\n# Introduction\n\n# Related Work\n\n# Method\n\n# Experiments\n\n# Discussion\n\n# Conclusion\n',
        '05_manuscript/references.bib': '% Thesis bibliography. Keep citekeys stable.\n',
        '05_manuscript/README.md': '# 05 Manuscript\n\nQuarto/Markdown manuscript workspace.\n',
        '06_review/reviewer_checklist.md': '# Reviewer Checklist\n\n- [ ] Every strong claim has evidence.\n- [ ] Every citation appears in references.bib.\n- [ ] Evidence rows include quote/page where possible.\n',
        '06_review/citation_audit.csv': 'citekey,location,status,issue,fix\n',
        '07_automation/README.md': '# 07 Automation\n\nBatch files, schedules, and operational notes. LangGraph is intentionally out of Sprint 1 scope.\n',
        '07_automation/runbook.md': '# Runbook\n\n## Daily\n\n## Weekly\n\n## Before Advisor Meeting\n',
    }
    created = []
    skipped = []
    for rel, content in files.items():
        path = ROOT/rel
        if write_if_missing(path, content):
            created.append(rel)
        else:
            skipped.append(rel)
    log(f'init-thesis-os 실행: created={len(created)}, skipped={len(skipped)}')
    print('created:', len(created))
    for rel in created:
        print('+', rel)
    print('skipped:', len(skipped))
    for rel in skipped:
        print('=', rel)


EVIDENCE_REQUIRED_COLUMNS = [
    'evidence_id',
    'exact_quote',
    'source_location',
    'extraction_method',
    'verified_by',
    'verified_at',
    'use_in_chapter',
    'use_in_paragraph',
    'risk_note',
]


def stable_evidence_id(row, row_index, seen):
    source_quote = row.get('exact_quote') or row.get('quote') or ''
    parts = [
        row.get('paper_id') or '',
        row.get('citekey') or '',
        row.get('claim_type') or '',
        row.get('claim') or '',
        source_quote,
    ]
    base = hashlib.sha1('\n'.join(parts).encode('utf-8')).hexdigest()[:16]
    evidence_id = f'ev_{base}'
    if evidence_id in seen:
        tie = hashlib.sha1(f'{base}\n{row_index}'.encode('utf-8')).hexdigest()[:8]
        evidence_id = f'ev_{base}_{tie}'
    seen.add(evidence_id)
    return evidence_id


def normalized_verified(value):
    text = str(value or '').strip().lower()
    if text in ('true', 'false'):
        return text
    if text in ('1', 'yes', 'y'):
        return 'true'
    return 'false'


def cmd_migrate_evidence(args):
    ev = ROOT/'matrices/evidence_matrix.csv'
    if not ev.exists():
        raise SystemExit(f'Missing Evidence Matrix: {ev}')
    backup_dir = ROOT/'matrices/backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = backup_dir/f'evidence_matrix_{stamp}.csv'
    shutil.copy2(ev, backup)

    with ev.open(encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        original_columns = list(reader.fieldnames or [])
        rows = list(reader)

    fieldnames = list(original_columns)
    added_columns = [c for c in EVIDENCE_REQUIRED_COLUMNS if c not in fieldnames]
    fieldnames.extend(added_columns)
    if 'verified' not in fieldnames:
        fieldnames.append('verified')
        added_columns.append('verified')

    seen_ids = set()
    for index, row in enumerate(rows, 1):
        for col in fieldnames:
            row.setdefault(col, '')
        if not row.get('evidence_id'):
            row['evidence_id'] = stable_evidence_id(row, index, seen_ids)
        else:
            if row['evidence_id'] in seen_ids:
                row['evidence_id'] = stable_evidence_id(row, index, seen_ids)
            else:
                seen_ids.add(row['evidence_id'])
        if not row.get('exact_quote') and row.get('quote'):
            row['exact_quote'] = row.get('quote') or ''
        if not row.get('source_location'):
            location_parts = []
            if row.get('page'):
                location_parts.append(f"page={row.get('page')}")
            if row.get('section'):
                location_parts.append(f"section={row.get('section')}")
            row['source_location'] = '; '.join(location_parts)
        if not row.get('extraction_method'):
            row['extraction_method'] = 'legacy_migration'
        row['verified'] = normalized_verified(row.get('verified'))

    with ev.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    ids = [r.get('evidence_id') for r in rows if r.get('evidence_id')]
    duplicate_ids = sorted(k for k, v in count_values(ids).items() if v > 1)
    report = ROOT/f'reports/audit_reports/evidence_migration_{today()}.md'
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f'# Evidence Migration {today()}\n',
        '## Summary\n',
        f'- source: `{ev}`\n',
        f'- backup: `{backup}`\n',
        f'- rows_before: {len(rows)}\n',
        f'- rows_after: {len(rows)}\n',
        f'- original_columns: {len(original_columns)}\n',
        f'- final_columns: {len(fieldnames)}\n',
        f"- newly_added_columns: {', '.join(added_columns) if added_columns else 'none'}\n",
        f"- required_governance_columns: {', '.join(EVIDENCE_REQUIRED_COLUMNS)}\n",
        f'- evidence_id_duplicates: {len(duplicate_ids)}\n',
        '\n## Newly Added Columns\n',
    ]
    for col in added_columns:
        lines.append(f'- `{col}`\n')
    if not added_columns:
        lines.append('- none; schema already contained required governance columns\n')
    lines.append('\n## Required Governance Columns\n')
    for col in EVIDENCE_REQUIRED_COLUMNS:
        lines.append(f'- `{col}`\n')
    lines.append('\n## Rules Applied\n')
    lines.append('- Existing rows and columns were preserved.\n')
    lines.append('- `verified` values were preserved when already true/false; missing or ambiguous values defaulted to false.\n')
    lines.append('- `exact_quote` was filled from legacy `quote` when available.\n')
    lines.append('- `evidence_id` was generated from paper_id, citekey, claim_type, claim, and quote/exact_quote.\n')
    if duplicate_ids:
        lines.append('\n## Duplicate Evidence IDs\n')
        for evidence_id in duplicate_ids[:100]:
            lines.append(f'- `{evidence_id}`\n')
    report.write_text(''.join(lines), encoding='utf-8')
    log(f'migrate-evidence 실행: rows={len(rows)}, backup={backup.name}, report={report.name}')
    print(report)


def count_values(values):
    counts = defaultdict(int)
    for value in values:
        counts[value] += 1
    return counts


def db_citekeys():
    c = conn()
    rows = c.execute("SELECT id, citekey, title FROM papers WHERE citekey IS NOT NULL AND citekey != ''").fetchall()
    c.close()
    return rows


def read_evidence_for_audit():
    ev = ROOT/'matrices/evidence_matrix.csv'
    if not ev.exists():
        return []
    with ev.open(encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))


def parse_bib_citekeys(path):
    if not path.exists():
        return set()
    text = path.read_text(encoding='utf-8', errors='ignore')
    return set(re.findall(r'(?m)^@\w+\s*\{\s*([^,\s]+)', text))


def manuscript_citekeys():
    roots = [ROOT/'manuscript', ROOT/'05_manuscript']
    found = defaultdict(set)
    pattern = re.compile(r'(?<![\w.-])@([A-Za-z0-9_:-]+)')
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob('*'):
            if not path.is_file() or path.suffix.lower() not in ('.md', '.qmd', '.tex'):
                continue
            text = path.read_text(encoding='utf-8', errors='ignore')
            for citekey in pattern.findall(text):
                found[citekey].add(str(path.relative_to(ROOT)))
    return found


def format_items(items, limit=100):
    if not items:
        return ['- none\n']
    lines = []
    for item in list(items)[:limit]:
        lines.append(f'- `{item}`\n')
    extra = len(items) - limit
    if extra > 0:
        lines.append(f'- ... {extra} more\n')
    return lines


def cmd_check_citekeys(args):
    init_db()
    db_rows = db_citekeys()
    db_by_key = defaultdict(list)
    for row in db_rows:
        db_by_key[row['citekey']].append(row)
    db_keys = set(db_by_key)
    db_duplicates = {k: v for k, v in db_by_key.items() if len(v) > 1}

    evidence_rows = read_evidence_for_audit()
    evidence_keys = {r.get('citekey') for r in evidence_rows if r.get('citekey')}
    evidence_paper_ids = {r.get('paper_id') for r in evidence_rows if r.get('paper_id')}
    c = conn()
    db_paper_ids = {r[0] for r in c.execute('SELECT id FROM papers').fetchall()}
    c.close()

    canonical_bib = ROOT/'05_manuscript/references.bib'
    legacy_bib = ROOT/'manuscript/references.bib'
    bib_paths = [canonical_bib, legacy_bib]
    bib_by_path = {str(p.relative_to(ROOT)): parse_bib_citekeys(p) for p in bib_paths if p.exists()}
    canonical_bib_keys = parse_bib_citekeys(canonical_bib)
    legacy_bib_keys = parse_bib_citekeys(legacy_bib)
    bib_keys = set().union(*bib_by_path.values()) if bib_by_path else set()
    manuscript_refs = manuscript_citekeys()
    manuscript_keys = set(manuscript_refs)

    evidence_not_in_db = sorted(evidence_keys - db_keys)
    bib_not_in_db = sorted(bib_keys - db_keys)
    db_not_in_bib = sorted(db_keys - bib_keys)
    canonical_bib_not_in_db = sorted(canonical_bib_keys - db_keys)
    db_not_in_canonical_bib = sorted(db_keys - canonical_bib_keys)
    manuscript_not_in_evidence = sorted(manuscript_keys - evidence_keys)
    orphan_evidence_paper_ids = sorted(evidence_paper_ids - db_paper_ids)

    report = ROOT/f'reports/audit_reports/citekey_audit_{today()}.md'
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f'# Citekey Audit {today()}\n',
        '## Summary\n',
        f'- db_citekeys: {len(db_keys)}\n',
        f'- evidence_citekeys: {len(evidence_keys)}\n',
        f'- bib_citekeys_combined: {len(bib_keys)}\n',
        f'- canonical_bib_citekeys: {len(canonical_bib_keys)}\n',
        f'- legacy_bib_citekeys: {len(legacy_bib_keys)}\n',
        f'- manuscript_citekeys: {len(manuscript_keys)}\n',
        f'- db_duplicate_citekeys: {len(db_duplicates)}\n',
        f'- evidence_citekeys_not_in_db: {len(evidence_not_in_db)}\n',
        f'- bib_citekeys_not_in_db: {len(bib_not_in_db)}\n',
        f'- db_citekeys_not_in_bib: {len(db_not_in_bib)}\n',
        f'- canonical_bib_citekeys_not_in_db: {len(canonical_bib_not_in_db)}\n',
        f'- db_citekeys_not_in_canonical_bib: {len(db_not_in_canonical_bib)}\n',
        f'- manuscript_citekeys_not_in_evidence: {len(manuscript_not_in_evidence)}\n',
        f'- orphan_evidence_paper_ids: {len(orphan_evidence_paper_ids)}\n',
        '\n## Bibliography Files\n',
    ]
    for path, keys in bib_by_path.items():
        lines.append(f'- `{path}`: {len(keys)} citekeys\n')

    lines.append('\n## 1. DB Citekey Duplicates\n')
    if not db_duplicates:
        lines.append('- none\n')
    else:
        for citekey, rows in list(db_duplicates.items())[:100]:
            ids = ', '.join(r['id'] for r in rows)
            lines.append(f'- `{citekey}`: {ids}\n')

    lines.append('\n## 2. Evidence Citekeys Not In DB\n')
    lines.extend(format_items(evidence_not_in_db))

    lines.append('\n## 3. references.bib Citekeys Not In DB\n')
    lines.extend(format_items(bib_not_in_db))

    lines.append('\n## 4. DB Citekeys Not In references.bib\n')
    lines.extend(format_items(db_not_in_bib))

    lines.append('\n## 4a. Canonical 05_manuscript/references.bib Gaps\n')
    lines.append(f'- canonical file: `{canonical_bib.relative_to(ROOT)}`\n')
    lines.append(f'- DB citekeys not in canonical bib: {len(db_not_in_canonical_bib)}\n')
    lines.append(f'- canonical bib citekeys not in DB: {len(canonical_bib_not_in_db)}\n')
    lines.append('\n### DB Citekeys Not In Canonical Bib\n')
    lines.extend(format_items(db_not_in_canonical_bib))
    lines.append('\n### Canonical Bib Citekeys Not In DB\n')
    lines.extend(format_items(canonical_bib_not_in_db))

    lines.append('\n## 5. Manuscript Citekeys Not In Evidence Matrix\n')
    if not manuscript_not_in_evidence:
        lines.append('- none\n')
    else:
        for citekey in manuscript_not_in_evidence[:100]:
            files = ', '.join(sorted(manuscript_refs[citekey]))
            lines.append(f'- `{citekey}` in {files}\n')

    lines.append('\n## 6. Orphan Evidence Paper IDs\n')
    lines.extend(format_items(orphan_evidence_paper_ids))

    lines.append('\n## Recommended Fix\n')
    if db_duplicates:
        lines.append('- Resolve duplicate DB citekeys before exporting or drafting citations.\n')
    if evidence_not_in_db or orphan_evidence_paper_ids:
        lines.append('- Reconcile orphan Evidence Matrix rows against `papers.sqlite`; fix paper_id/citekey or archive rows explicitly.\n')
    if bib_not_in_db:
        lines.append('- Import missing bibliography entries into DB or remove unused BibTeX entries from the thesis bibliography.\n')
    if db_not_in_bib:
        lines.append('- Export top/active DB papers into `05_manuscript/references.bib` or legacy `manuscript/references.bib`.\n')
    if db_not_in_canonical_bib:
        lines.append('- Populate canonical `05_manuscript/references.bib`; legacy `manuscript/references.bib` currently contains entries but is not the long-term canonical target.\n')
    if manuscript_not_in_evidence:
        lines.append('- Add Evidence Matrix rows for manuscript citations before treating the manuscript claim as supported.\n')
    if not any([db_duplicates, evidence_not_in_db, orphan_evidence_paper_ids, bib_not_in_db, db_not_in_bib, manuscript_not_in_evidence]):
        lines.append('- No citekey governance issues found.\n')

    report.write_text(''.join(lines), encoding='utf-8')
    log(f'check-citekeys 실행: report={report.name}')
    print(report)


def clean_bib_value(value):
    value = (value or '').strip()
    if len(value) >= 2 and ((value[0] == '{' and value[-1] == '}') or (value[0] == '"' and value[-1] == '"')):
        value = value[1:-1]
    return ' '.join(value.replace('\n', ' ').split())


def parse_bib_entries(path):
    if not path.exists():
        return []
    text = path.read_text(encoding='utf-8', errors='ignore')
    entries = []
    for match in re.finditer(r'(?m)^@(\w+)\s*\{\s*([^,\s]+)\s*,', text):
        entry_type = match.group(1)
        citekey = match.group(2).strip()
        start = match.end()
        depth = 1
        pos = start
        while pos < len(text) and depth > 0:
            ch = text[pos]
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
            pos += 1
        body = text[start:pos-1]
        fields = {}
        for field_match in re.finditer(r'(\w+)\s*=\s*({(?:[^{}]|\{[^{}]*\})*}|"[^"]*"|[^,\n]+)', body, re.S):
            fields[field_match.group(1).lower()] = clean_bib_value(field_match.group(2).rstrip(','))
        entries.append({
            'entry_type': entry_type,
            'citekey': citekey,
            'title': fields.get('title', ''),
            'doi': normalize_doi(fields.get('doi', '')),
            'arxiv_id': normalize_arxiv(fields.get('eprint') or fields.get('arxiv') or fields.get('arxivid') or fields.get('archiveprefix', '') if fields.get('archiveprefix', '').lower() == 'arxiv' else fields.get('eprint', '')),
            'title_norm': norm_title(fields.get('title', '')),
            'fields': fields,
        })
    return entries


def normalize_doi(value):
    return (value or '').strip().lower().replace('https://doi.org/', '').replace('http://doi.org/', '')


def normalize_arxiv(value):
    value = (value or '').strip().lower()
    value = value.replace('arxiv:', '')
    return value.split('v')[0] if re.search(r'v\d+$', value) else value


def paper_match_indexes(rows):
    doi_index = {}
    arxiv_index = {}
    title_index = {}
    for row in rows:
        if row['doi']:
            doi_index.setdefault(normalize_doi(row['doi']), []).append(row)
        if row['arxiv_id']:
            arxiv_index.setdefault(normalize_arxiv(row['arxiv_id']), []).append(row)
        if row['title_norm']:
            title_index.setdefault(row['title_norm'], []).append(row)
    return doi_index, arxiv_index, title_index


def match_bib_entry(entry, indexes):
    doi_index, arxiv_index, title_index = indexes
    if entry['doi'] and len(doi_index.get(entry['doi'], [])) == 1:
        return doi_index[entry['doi']][0], 'doi'
    if entry['arxiv_id'] and len(arxiv_index.get(entry['arxiv_id'], [])) == 1:
        return arxiv_index[entry['arxiv_id']][0], 'arxiv_id'
    if entry['title_norm'] and len(title_index.get(entry['title_norm'], [])) == 1:
        return title_index[entry['title_norm']][0], 'title_norm'
    return None, ''


def db_backup_path():
    backup_dir = DB.parent/'backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir/f"papers_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sqlite"


def cmd_sync_zotero(args):
    init_db()
    bib = ROOT/args.bib
    legacy_bib = ROOT/'manuscript/references.bib'
    canonical_entries = parse_bib_entries(bib)
    legacy_entries = parse_bib_entries(legacy_bib)
    c = conn()
    rows = c.execute("SELECT id,title,doi,arxiv_id,citekey,title_norm FROM papers").fetchall()
    indexes = paper_match_indexes(rows)
    matched = []
    unmatched_bib = []
    matched_db_ids = set()
    for entry in canonical_entries:
        row, method = match_bib_entry(entry, indexes)
        if row:
            matched.append((entry, row, method))
            matched_db_ids.add(row['id'])
        else:
            unmatched_bib.append(entry)

    unmatched_db = [r for r in rows if r['id'] not in matched_db_ids]
    candidates = [(entry, row, method) for entry, row, method in matched if entry['citekey'] and entry['citekey'] != row['citekey']]
    target_counts = count_values([entry['citekey'] for entry, _, _ in candidates])
    duplicate_targets = sorted(k for k, v in target_counts.items() if v > 1)
    canonical_empty = len(canonical_entries) == 0
    applied = 0
    backup = ''
    apply_requested = bool(args.apply)
    if apply_requested and canonical_empty:
        apply_requested = False
    if apply_requested and duplicate_targets:
        apply_requested = False
    if apply_requested and candidates:
        backup_path = db_backup_path()
        shutil.copy2(DB, backup_path)
        backup = str(backup_path)
        for entry, row, method in candidates:
            c.execute('UPDATE papers SET citekey=?, updated_at=? WHERE id=?', (entry['citekey'], now(), row['id']))
            applied += 1
        c.commit()
    c.close()

    report = ROOT/f'reports/audit_reports/zotero_sync_{today()}.md'
    report.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f'# Zotero Sync Report {today()}\n',
        '## Summary\n',
        f'- bib: `{args.bib}`\n',
        f'- mode: {"apply" if args.apply else "dry-run"}\n',
        f'- canonical_bib_entries: {len(canonical_entries)}\n',
        f'- legacy_bib_entries: {len(legacy_entries)}\n',
        f'- db_papers: {len(rows)}\n',
        f'- matched_entries: {len(matched)}\n',
        f'- citekey_change_candidates: {len(candidates)}\n',
        f'- unmatched_bib_entries: {len(unmatched_bib)}\n',
        f'- unmatched_db_papers: {len(unmatched_db)}\n',
        f'- duplicate_target_citekeys: {len(duplicate_targets)}\n',
        f'- applied_changes: {applied}\n',
    ]
    if backup:
        lines.append(f'- db_backup: `{backup}`\n')
    if canonical_empty:
        lines += [
            '\n## WARN: Canonical Bib Is Empty\n',
            f'- canonical bibliography `{args.bib}` has no BibTeX entries.\n',
            f'- legacy `manuscript/references.bib` has {len(legacy_entries)} entries.\n',
            '- recommended action: configure Zotero Better BibTeX export to `05_manuscript/references.bib`.\n',
            '- no legacy bibliography was copied automatically.\n',
        ]
    if args.apply and canonical_empty:
        lines.append('- apply was skipped because canonical bib is empty.\n')
    if args.apply and duplicate_targets:
        lines.append('- apply was skipped because duplicate target citekeys were detected.\n')

    lines.append('\n## Matching Rules\n')
    lines.append('1. DOI\n2. arXiv ID\n3. title_norm\n')

    lines.append('\n## Citekey Change Candidates\n')
    if not candidates:
        lines.append('- none\n')
    else:
        for entry, row, method in candidates[:100]:
            lines.append(f"- `{row['id']}` {method}: `{row['citekey']}` -> `{entry['citekey']}` / {row['title']}\n")
        if len(candidates) > 100:
            lines.append(f'- ... {len(candidates)-100} more\n')

    lines.append('\n## Duplicate Target Citekeys\n')
    lines.extend(format_items(duplicate_targets))

    lines.append('\n## Unmatched BibTeX Entries\n')
    if not unmatched_bib:
        lines.append('- none\n')
    else:
        for entry in unmatched_bib[:100]:
            lines.append(f"- `{entry['citekey']}` {entry['title']}\n")
        if len(unmatched_bib) > 100:
            lines.append(f'- ... {len(unmatched_bib)-100} more\n')

    lines.append('\n## Unmatched DB Papers\n')
    if not unmatched_db:
        lines.append('- none\n')
    else:
        for row in unmatched_db[:100]:
            lines.append(f"- `{row['citekey']}` {row['title']}\n")
        if len(unmatched_db) > 100:
            lines.append(f'- ... {len(unmatched_db)-100} more\n')

    lines.append('\n## Recommended Action\n')
    if canonical_empty:
        lines.append('- Configure Better BibTeX auto-export to `05_manuscript/references.bib`, then rerun dry-run.\n')
    elif candidates and not args.apply:
        lines.append('- Review citekey candidates, then rerun with `--apply` if acceptable.\n')
    elif applied:
        lines.append('- Run `check-citekeys` and inspect manuscript references after applied changes.\n')
    else:
        lines.append('- No Zotero citekey changes are needed from the current canonical bibliography.\n')

    report.write_text(''.join(lines), encoding='utf-8')
    log(f'sync-zotero 실행: bib={args.bib}, entries={len(canonical_entries)}, candidates={len(candidates)}, applied={applied}')
    print(report)


def cmd_init_quarto(args):
    root = ROOT/'05_manuscript'
    chapters = root/'chapters'
    root.mkdir(parents=True, exist_ok=True)
    chapters.mkdir(parents=True, exist_ok=True)
    files = {
        '_quarto.yml': """project:
  type: book

book:
  title: "PaperOps Thesis"
  chapters:
    - thesis.qmd
    - chapters/ch1_intro.qmd
    - chapters/ch2_literature.qmd
    - chapters/ch3_method.qmd
    - chapters/ch4_system.qmd
    - chapters/ch5_evaluation.qmd
    - chapters/ch6_conclusion.qmd

bibliography: references.bib

format:
  html:
    toc: true
  docx: default
""",
        'thesis.qmd': """# PaperOps Thesis

This file is the Quarto entrypoint. Chapter files live under `chapters/`.
""",
        'chapters/ch1_intro.qmd': "# Chapter 1. Introduction\n\n## Problem\n\n## Research Questions\n\n## Contributions\n",
        'chapters/ch2_literature.qmd': "# Chapter 2. Literature Review\n\n## Ontology and Knowledge Graphs\n\n## Research Automation\n\n## Evidence Governance\n",
        'chapters/ch3_method.qmd': "# Chapter 3. Method\n\n## PaperOps Architecture\n\n## Evidence-first Workflow\n\n## Human Verification Policy\n",
        'chapters/ch4_system.qmd': "# Chapter 4. System Implementation\n\n## Data Model\n\n## CLI Commands\n\n## Automation Flow\n",
        'chapters/ch5_evaluation.qmd': "# Chapter 5. Evaluation\n\n## Metrics\n\n## Baselines\n\n## Results\n",
        'chapters/ch6_conclusion.qmd': "# Chapter 6. Conclusion\n\n## Summary\n\n## Limitations\n\n## Future Work\n",
    }
    created = []
    preserved = []
    for rel, content in files.items():
        path = root/rel
        if write_if_missing(path, content):
            created.append(str(path.relative_to(ROOT)))
        else:
            preserved.append(str(path.relative_to(ROOT)))
    render_status = 'not requested'
    if args.render:
        quarto_ok, detail = command_version('quarto')
        if not quarto_ok:
            render_status = f'WARN: Quarto {detail}'
            print(render_status)
        else:
            result = subprocess.run(['quarto', 'render', 'thesis.qmd'], cwd=str(root), capture_output=True, text=True, timeout=120)
            render_status = f'exit={result.returncode}'
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(result.stderr)
    log(f'init-quarto 실행: created={len(created)}, preserved={len(preserved)}, render={render_status}')
    print('created:', len(created))
    for item in created:
        print('+', item)
    print('preserved:', len(preserved))
    for item in preserved:
        print('=', item)


def smoke_run(args):
    result = subprocess.run([sys.executable, 'scripts/paperops.py'] + args, cwd=str(ROOT), capture_output=True, text=True, timeout=180)
    return {
        'command': 'python scripts/paperops.py ' + ' '.join(args),
        'returncode': result.returncode,
        'stdout': (result.stdout or '').strip(),
        'stderr': (result.stderr or '').strip(),
    }


def cmd_smoke_test(args):
    report = ROOT/f'reports/audit_reports/smoke_test_{today()}.md'
    report.parent.mkdir(parents=True, exist_ok=True)
    checks = []
    checks.append({'name': 'DB exists', 'ok': DB.exists(), 'detail': str(DB)})
    ev = ROOT/'matrices/evidence_matrix.csv'
    checks.append({'name': 'Evidence Matrix readable', 'ok': ev.exists() and file_row_count(ev) >= 0, 'detail': f'{ev} / rows={file_row_count(ev)}'})
    commands = [
        ['check-citekeys'],
        ['sync-zotero', '--bib', '05_manuscript/references.bib', '--dry-run'],
        ['cards', '--limit', '3', '--format', 'yaml'],
        ['doctor'],
    ]
    command_results = [smoke_run(cmd) for cmd in commands]
    lines = [f'# Smoke Test {today()}\n', '## File Checks\n']
    for check in checks:
        lines.append(f"- [{'OK' if check['ok'] else 'FAIL'}] {check['name']}: {check['detail']}\n")
    lines.append('\n## Command Checks\n')
    for result in command_results:
        ok = result['returncode'] == 0
        lines.append(f"### {'OK' if ok else 'FAIL'} `{result['command']}`\n")
        lines.append(f"- returncode: {result['returncode']}\n")
        if result['stdout']:
            lines.append('\n```text\n' + result['stdout'][:3000] + '\n```\n')
        if result['stderr']:
            lines.append('\n```text\n' + result['stderr'][:3000] + '\n```\n')
    all_ok = all(c['ok'] for c in checks) and all(r['returncode'] == 0 for r in command_results)
    lines.append('\n## Summary\n')
    lines.append(f'- smoke_test_passed: {str(all_ok).lower()}\n')
    report.write_text(''.join(lines), encoding='utf-8')
    log(f'smoke-test 실행: passed={all_ok}, report={report.name}')
    print(report)
    if not all_ok:
        raise SystemExit(1)


def main():
    ap=argparse.ArgumentParser(description='PaperOps MVP')
    sub=ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('init').set_defaults(func=lambda a:init_db())
    sub.add_parser('status').set_defaults(func=cmd_status)
    p=sub.add_parser('doctor'); p.add_argument('--strict', action='store_true'); p.set_defaults(func=cmd_doctor)
    sub.add_parser('grobid-status').set_defaults(func=cmd_grobid_status)
    p=sub.add_parser('parse-grobid'); p.add_argument('--limit', type=int, default=5); p.add_argument('--paper-id'); p.add_argument('--pdf'); p.add_argument('--apply', action='store_true'); p.add_argument('--dry-run', action='store_true'); p.set_defaults(func=cmd_parse_grobid)
    p=sub.add_parser('validate-grobid-artifacts'); p.add_argument('--paper-id'); p.add_argument('--path'); p.set_defaults(func=cmd_validate_grobid_artifacts)
    p=sub.add_parser('extract-evidence-candidates'); p.add_argument('--paper-id', required=True); p.add_argument('--apply', action='store_true'); p.add_argument('--dry-run', action='store_true'); p.set_defaults(func=cmd_extract_evidence_candidates)
    p=sub.add_parser('validate-evidence-candidates'); p.add_argument('--paper-id'); p.set_defaults(func=cmd_validate_evidence_candidates)
    p=sub.add_parser('review-evidence-candidates'); p.add_argument('--paper-id', required=True); p.add_argument('--min-confidence', type=float); p.add_argument('--use-in-section', choices=sorted(EVIDENCE_CANDIDATE_USE_SECTIONS)); p.add_argument('--claim-type', choices=sorted(EVIDENCE_CANDIDATE_CLAIM_TYPES)); p.set_defaults(func=cmd_review_evidence_candidates)
    p=sub.add_parser('validate-review-queue'); p.add_argument('--paper-id'); p.set_defaults(func=cmd_validate_review_queue)
    p=sub.add_parser('test-review-preservation'); p.add_argument('--paper-id', required=True); p.set_defaults(func=cmd_test_review_preservation)
    p=sub.add_parser('promotion-plan'); p.add_argument('--paper-id'); p.add_argument('--candidate-id'); p.add_argument('--dry-run', action='store_true'); p.set_defaults(func=cmd_promotion_plan)
    p=sub.add_parser('promote-evidence'); p.add_argument('--from-preview', default='matrices/evidence_matrix_patch_preview.csv'); p.add_argument('--paper-id'); p.add_argument('--candidate-id'); p.add_argument('--dry-run', action='store_true'); p.add_argument('--apply', action='store_true'); p.set_defaults(func=cmd_promote_evidence)
    p=sub.add_parser('audit-promoted-evidence'); p.add_argument('--paper-id'); p.add_argument('--candidate-id'); p.set_defaults(func=cmd_audit_promoted_evidence)
    p=sub.add_parser('extract-promoted-rows'); p.add_argument('--since'); p.add_argument('--output', default='reports/review/promoted_rows_external_review_input_2026-06-04.csv'); p.add_argument('--paper-id'); p.add_argument('--candidate-id'); p.set_defaults(func=cmd_extract_promoted_rows)
    p=sub.add_parser('mark-pdf-check-required'); p.add_argument('--promoted-only', action='store_true'); p.add_argument('--paper-id'); p.add_argument('--candidate-id'); p.add_argument('--dry-run', action='store_true'); p.set_defaults(func=cmd_mark_pdf_check_required)
    p=sub.add_parser('audit-domain-specific-claims'); p.add_argument('--promoted-only', action='store_true'); p.add_argument('--paper-id'); p.add_argument('--candidate-id'); p.set_defaults(func=cmd_audit_domain_specific_claims)
    p=sub.add_parser('guard-no-auto-verified'); p.add_argument('--promoted-only', action='store_true'); p.add_argument('--paper-id'); p.add_argument('--candidate-id'); p.add_argument('--allow-approved-manuscript-changes', action='store_true'); p.add_argument('--manuscript-apply-report'); p.set_defaults(func=cmd_guard_no_auto_verified)
    p=sub.add_parser('update-promoted-row-review-metadata'); p.add_argument('--input', default='reports/review/promoted_rows_external_review_input_2026-06-04.csv'); p.add_argument('--output', default='reports/review/promoted_rows_row_level_review_decisions_2026-06-04.csv'); p.add_argument('--patch-preview', default='matrices/evidence_matrix_metadata_patch_preview_2026-06-04.csv'); p.add_argument('--dry-run', action='store_true'); p.set_defaults(func=cmd_update_promoted_row_review_metadata)
    p=sub.add_parser('guard-paperops-overclaim'); p.add_argument('--promoted-only', action='store_true'); p.add_argument('--paper-id'); p.add_argument('--candidate-id'); p.set_defaults(func=cmd_guard_paperops_overclaim)
    p=sub.add_parser('pdf-page-verification-sheet'); p.add_argument('--promoted-only', action='store_true'); p.add_argument('--paper-id'); p.add_argument('--candidate-id'); p.add_argument('--output', default='reports/review/pdf_page_verification_sheet_2026-06-04.csv'); p.set_defaults(func=cmd_pdf_page_verification_sheet)
    p=sub.add_parser('locate-pdf-pages'); p.add_argument('--promoted-only', action='store_true'); p.add_argument('--paper-id'); p.add_argument('--candidate-id'); p.add_argument('--output', default='reports/review/pdf_page_locator_candidates_2026-06-04.csv'); p.set_defaults(func=cmd_locate_pdf_pages)
    p=sub.add_parser('apply-page-metadata'); p.add_argument('--from-preview', default='matrices/evidence_matrix_page_metadata_patch_preview_2026-06-04.csv'); p.add_argument('--dry-run', action='store_true'); p.add_argument('--apply', action='store_true'); p.set_defaults(func=cmd_apply_page_metadata)
    p=sub.add_parser('manuscript-patch-preview'); p.add_argument('--from-preview', default='reports/review/manuscript_outline_insertion_preview_2026-06-04.csv'); p.add_argument('--output-prefix'); p.set_defaults(func=cmd_manuscript_patch_preview)
    p=sub.add_parser('apply-manuscript-patch'); p.add_argument('--from-preview', default='reports/review/manuscript_patch_preview_2026-06-09.csv'); p.add_argument('--output-prefix'); p.add_argument('--dry-run', action='store_true'); p.add_argument('--apply', action='store_true'); p.set_defaults(func=cmd_apply_manuscript_patch)
    sub.add_parser('backup').set_defaults(func=cmd_backup)
    sub.add_parser('init-thesis-os').set_defaults(func=cmd_init_thesis_os)
    sub.add_parser('migrate-evidence').set_defaults(func=cmd_migrate_evidence)
    sub.add_parser('check-citekeys').set_defaults(func=cmd_check_citekeys)
    p=sub.add_parser('sync-zotero'); p.add_argument('--bib', default='05_manuscript/references.bib'); p.add_argument('--dry-run', action='store_true'); p.add_argument('--apply', action='store_true'); p.set_defaults(func=cmd_sync_zotero)
    p=sub.add_parser('init-quarto'); p.add_argument('--render', action='store_true'); p.set_defaults(func=cmd_init_quarto)
    sub.add_parser('smoke-test').set_defaults(func=cmd_smoke_test)
    p=sub.add_parser('collect'); p.add_argument('--limit',type=int,default=20); p.set_defaults(func=cmd_collect)
    sub.add_parser('score').set_defaults(func=cmd_score)
    p=sub.add_parser('digest'); p.add_argument('--top',type=int,default=20); p.set_defaults(func=cmd_digest)
    p=sub.add_parser('download-pdfs'); p.add_argument('--limit',type=int,default=10); p.add_argument('--min-score',type=float,default=0.35); p.add_argument('--overwrite',action='store_true'); p.set_defaults(func=cmd_download)
    p=sub.add_parser('parse'); p.add_argument('--max-pages',type=int,default=80); p.set_defaults(func=cmd_parse)
    p=sub.add_parser('cards'); p.add_argument('--limit',type=int,default=20); p.add_argument('--format', choices=['markdown','yaml'], default='markdown'); p.set_defaults(func=cmd_cards)
    p=sub.add_parser('extract-evidence'); p.add_argument('--limit',type=int,default=20); p.set_defaults(func=cmd_extract)
    sub.add_parser('outline').set_defaults(func=cmd_outline)
    p=sub.add_parser('screen'); p.add_argument('--limit',type=int,default=80); p.set_defaults(func=cmd_screen)
    p=sub.add_parser('gap'); p.add_argument('--limit',type=int,default=80); p.set_defaults(func=cmd_gap)
    p=sub.add_parser('research-design'); p.add_argument('--overwrite',action='store_true'); p.set_defaults(func=cmd_research_design)
    p=sub.add_parser('brief'); p.add_argument('--top',type=int,default=12); p.set_defaults(func=cmd_brief)
    sub.add_parser('audit').set_defaults(func=cmd_audit)
    try:
        import paperops_figures
        paperops_figures.register_subcommands(sub)
    except Exception as e:
        print(f'[warn] figure commands unavailable: {e}', file=sys.stderr)
    try:
        import paperops_draft_audit
        paperops_draft_audit.register_subcommands(sub)
    except Exception as e:
        print(f'[warn] draft audit command unavailable: {e}', file=sys.stderr)
    args=ap.parse_args(); args.func(args)

if __name__ == '__main__': main()
