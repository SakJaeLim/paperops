# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse, csv, hashlib, json, math, re, sqlite3, urllib.parse, urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'data/metadata/papers.sqlite'
LOG = ROOT / 'logs/ACTIVITY_LOG.md'

def now(): return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
def today(): return datetime.now().strftime('%Y-%m-%d')
def log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    if not LOG.exists(): LOG.write_text('# 논문AGENT 활동 로그\n\n', encoding='utf-8')
    with LOG.open('a', encoding='utf-8') as f: f.write(f'- [{now()}] {msg}\n')
def conn():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c
def slug(s,n=70): return re.sub(r'[^A-Za-z0-9가-힣]+','_',s or '').strip('_').lower()[:n] or 'untitled'
def norm_title(s): return re.sub(r'\W+','',(s or '').lower())
def stable_id(p):
    key = 'doi:'+(p.get('doi') or '').lower() if p.get('doi') else 'title:'+norm_title(p.get('title',''))
    return hashlib.sha1(key.encode('utf-8')).hexdigest()[:16]
def citekey(p):
    y=str(p.get('year') or 'nd'); first='paper'
    if p.get('authors'): first=re.split(r'\s+',p['authors'][0].replace(',',' '))[0].lower()
    word=re.findall(r'[A-Za-z]{4,}',p.get('title','')); tail=(word[0].lower() if word else 'study')
    return slug(f'{first}{y}{tail}',40)
def fetch_json(url, headers=None):
    req=urllib.request.Request(url,headers=headers or {'User-Agent':'PaperOps/0.1'})
    with urllib.request.urlopen(req,timeout=40) as r: return json.loads(r.read().decode('utf-8','ignore'))
def load_queries():
    # YAML 없이도 동작하도록 단순 파싱
    text=(ROOT/'config/topic_profile.yaml').read_text(encoding='utf-8',errors='ignore')
    qs=re.findall(r'query:\s*"([^"]+)"', text)
    return qs or ['ontology knowledge graph semantic web','automated literature review research assistant']
def upsert(p):
    p['id']=p.get('id') or stable_id(p); p['citekey']=p.get('citekey') or citekey(p)
    c=conn()
    c.execute('''INSERT OR IGNORE INTO papers(id,title,authors_json,year,venue,doi,arxiv_id,abstract,url,pdf_url,source,collection_date,status,score,topic_relevance,citation_count,open_access,local_pdf_path,parsed_text_path,citekey,title_norm,raw_json,updated_at)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
    (p['id'],p.get('title'),json.dumps(p.get('authors') or [],ensure_ascii=False),p.get('year'),p.get('venue'),p.get('doi'),p.get('arxiv_id'),p.get('abstract'),p.get('url'),p.get('pdf_url'),p.get('source'),now(),'new',0,0,p.get('citation_count') or 0,1 if p.get('open_access') else 0,None,None,p['citekey'],norm_title(p.get('title','')),json.dumps(p,ensure_ascii=False),now()))
    c.commit(); c.close()

def cmd_collect_s2(args):
    total=0; raw=ROOT/f'data/incoming/semantic_scholar_{datetime.now().strftime("%Y%m%d_%H%M%S")}.jsonl'
    raw.parent.mkdir(parents=True,exist_ok=True)
    with raw.open('w',encoding='utf-8') as f:
        for q in load_queries():
            url='https://api.semanticscholar.org/graph/v1/paper/search?query='+urllib.parse.quote(q)+'&limit='+str(args.limit)+'&fields=title,abstract,year,venue,authors,url,openAccessPdf,citationCount,externalIds'
            try: js=fetch_json(url)
            except Exception as e:
                print('WARN semantic_scholar', e); continue
            for item in js.get('data',[]):
                ext=item.get('externalIds') or {}; pdf=item.get('openAccessPdf') or {}
                p=dict(title=item.get('title'),authors=[a.get('name','') for a in item.get('authors',[])],year=item.get('year'),venue=item.get('venue'),doi=ext.get('DOI'),arxiv_id=ext.get('ArXiv'),abstract=item.get('abstract') or '',url=item.get('url'),pdf_url=pdf.get('url'),source='semantic_scholar',citation_count=item.get('citationCount') or 0,open_access=bool(pdf.get('url')))
                upsert(p); f.write(json.dumps(p,ensure_ascii=False)+'\n'); total+=1
    log(f'Semantic Scholar 수집 완료: {total}건, raw={raw.name}')
    print(raw, total)

def cmd_dedupe(args):
    c=conn(); rows=c.execute('SELECT * FROM papers').fetchall(); c.close()
    groups=defaultdict(list)
    for r in rows:
        key=(r['doi'] or '').lower().strip() or r['title_norm']
        if key: groups[key].append(r)
    dup=[v for v in groups.values() if len(v)>1]
    out=ROOT/f'reports/audit_reports/duplicate_report_{today()}.md'
    lines=[f'# Duplicate Report {today()}\n',f'- duplicate groups: {len(dup)}\n']
    for g in dup[:200]:
        lines.append('\n## Group\n')
        for r in g: lines.append(f"- `{r['id']}` {r['title']} / {r['source']} / {r['doi']}\n")
    out.write_text('\n'.join(lines),encoding='utf-8'); log(f'중복 리포트 생성: {out}')
    print(out)

def top(limit):
    c=conn(); rows=c.execute('SELECT * FROM papers ORDER BY score DESC, year DESC LIMIT ?',(limit,)).fetchall(); c.close(); return rows

def cmd_export_bib(args):
    out=ROOT/'manuscript/references.bib'; rows=top(args.limit); lines=[]
    for r in rows:
        typ='article'; authors=' and '.join(json.loads(r['authors_json'] or '[]'))
        lines.append(f"@{typ}{{{r['citekey']},")
        lines.append(f"  title = {{{r['title'] or ''}}},")
        if authors: lines.append(f"  author = {{{authors}}},")
        if r['year']: lines.append(f"  year = {{{r['year']}}},")
        if r['venue']: lines.append(f"  journal = {{{r['venue']}}},")
        if r['doi']: lines.append(f"  doi = {{{r['doi']}}},")
        if r['url']: lines.append(f"  url = {{{r['url']}}},")
        lines.append('}\n')
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text('\n'.join(lines),encoding='utf-8'); log(f'BibTeX export 완료: {out}, {len(rows)}건')
    print(out)

def cmd_weekly(args):
    rows=top(args.top); out=ROOT/f'reports/weekly_review/weekly_review_{today()}.md'; out.parent.mkdir(parents=True,exist_ok=True)
    lines=[f'# Weekly Research Review {today()}\n','## 이번 주 핵심 후보\n']
    for r in rows:
        lines.append(f"- **{r['title']}** ({r['year']}) `{r['citekey']}` score={r['score']:.3f} status={r['status']}\n")
    lines += ['\n## 읽기 우선순위\n1. important/to_read 논문 Paper Card 검토\n2. Evidence Matrix의 low confidence 행 검증\n3. Related Work에 쓸 claim 선별\n','\n## 다음 액션\n- PDF 다운로드/파싱\n- 검증된 quote/page 추가\n- 중복 제거\n']
    out.write_text('\n'.join(lines),encoding='utf-8'); log(f'Weekly Review 생성: {out}')
    print(out)

def read_evidence():
    ev=ROOT/'matrices/evidence_matrix.csv'
    if not ev.exists(): return []
    with ev.open(encoding='utf-8',newline='') as f: return list(csv.DictReader(f))

def cmd_draft_related(args):
    ev=read_evidence(); out=ROOT/'manuscript/sections/02_related_work.md'
    rows=top(30)
    lines=['# 2. Related Work\n','> 초벌입니다. `[NEEDS_VERIFICATION]` 표시가 있는 문장은 원문 확인 필요.\n','## 2.1 Ontology and Knowledge Graph Foundations\n']
    for e in ev[:15]:
        if e.get('claim'):
            lines.append(f"- {e['claim']} @{e.get('citekey','')} [confidence: {e.get('confidence','low')}]\n")
    lines.append('\n## 2.2 AI-assisted Literature Review and Research Agents\n')
    for r in rows[:15]:
        title=(r['title'] or '').lower()
        if any(k in title for k in ['review','research','writing','agent','citation','literature']):
            lines.append(f"- {r['title']} contributes to this area @{r['citekey']} [NEEDS_VERIFICATION].\n")
    lines.append('\n## 2.3 Gap Summary\n- Existing work is fragmented across paper discovery, reading, evidence extraction, and writing support. [NEEDS_SOURCE]\n- The proposed PaperOps pipeline focuses on evidence-first integration rather than isolated automation. [NEEDS_SOURCE]\n')
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text('\n'.join(lines),encoding='utf-8'); log(f'Related Work 초안 생성: {out}')
    print(out)

def cmd_reviewer(args):
    out=ROOT/f'reports/audit_reports/reviewer_report_{today()}.md'
    text=(ROOT/'manuscript/main.md').read_text(encoding='utf-8',errors='ignore') if (ROOT/'manuscript/main.md').exists() else ''
    ev=read_evidence(); low=sum(1 for e in ev if e.get('confidence')!='high')
    report=f'''# Reviewer-style Report {today()}

## Summary
현재 원고는 작업 템플릿 단계이며, Evidence Matrix 기반으로 보강 중입니다.

## Strengths
- 수집, 점수화, Paper Card, Evidence Matrix, Audit 흐름이 연결되어 있습니다.
- citekey와 근거 행렬을 중심으로 작성하도록 설계되어 있습니다.

## Weaknesses / Risks
- Evidence Matrix의 low/medium confidence 행: {low}개
- 원문 quote/page가 부족한 행은 최종 인용 근거로 쓰기 어렵습니다.
- Related Work 초안의 `[NEEDS_VERIFICATION]`, `[NEEDS_SOURCE]`를 제거해야 합니다.

## Required Revisions
1. important 논문 PDF 원문 확인
2. quote/page/section 보강
3. manuscript/main.md에 섹션별 초안 통합
4. citation audit 재실행
5. 최신 논문 누락 여부 점검
'''
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text(report,encoding='utf-8'); log(f'Reviewer Report 생성: {out}')
    print(out)

def cmd_index(args):
    out=ROOT/'INDEX.md'
    files=['README.md','docs/00_MASTER_DESIGN.md','docs/01_MVP_ROADMAP.md','docs/02_PIPELINE_SPEC.md','docs/08_ENHANCEMENT_REPORT_'+today()+'.md','reports/daily_digest/digest_'+today()+'.md','reports/weekly_review/weekly_review_'+today()+'.md','reports/survey_reports/outline_'+today()+'.md','reports/survey_reports/gap_map_'+today()+'.md','reports/survey_reports/thesis_brief_'+today()+'.md','reports/audit_reports/reviewer_report_'+today()+'.md','matrices/evidence_matrix.csv','matrices/screening_matrix.csv','matrices/gap_matrix.csv','research_design/problem_definition.md','research_design/research_questions.md','research_design/artifact_definition.md','research_design/evaluation_plan.md','manuscript/main.md','manuscript/sections/02_related_work.md']
    lines=['# 논문AGENT 산출물 인덱스\n']+[f'- [{f}]({f})\n' for f in files if (ROOT/f).exists()]
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text('\n'.join(lines),encoding='utf-8'); log(f'INDEX 생성: {out}')
    print(out)

def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('collect-s2'); p.add_argument('--limit',type=int,default=20); p.set_defaults(func=cmd_collect_s2)
    sub.add_parser('dedupe').set_defaults(func=cmd_dedupe)
    p=sub.add_parser('export-bib'); p.add_argument('--limit',type=int,default=80); p.set_defaults(func=cmd_export_bib)
    p=sub.add_parser('weekly'); p.add_argument('--top',type=int,default=30); p.set_defaults(func=cmd_weekly)
    sub.add_parser('draft-related').set_defaults(func=cmd_draft_related)
    sub.add_parser('reviewer').set_defaults(func=cmd_reviewer)
    sub.add_parser('index').set_defaults(func=cmd_index)
    args=ap.parse_args(); args.func(args)
if __name__=='__main__': main()
