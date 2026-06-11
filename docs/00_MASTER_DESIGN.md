# PaperOps 마스터 설계서

## 1. 문제의식

논문 AI 도구는 많지만, 각각을 모두 설치하고 따로 쓰면 데이터가 분산되고 인용 근거가 약해집니다. 이 프로젝트는 여러 도구의 기능 패턴을 흡수하여 하나의 개인 논문작성 파이프라인으로 통합합니다.

## 2. 최종 구조

```text
Research Scout
  → Librarian
  → Screening Agent
  → Paper Reader
  → Evidence Extractor
  → Synthesis Agent
  → Outline Agent
  → Section Writer
  → Citation Auditor
  → Reviewer Agent
```

## 3. 참고 도구에서 흡수하는 기능

| 도구 유형 | 흡수 기능 | PaperOps 구현 |
|---|---|---|
| daily paper scanner | 새 논문 자동 발견, daily digest | collect, score, digest |
| literature review automation | query expansion, 후보 랭킹 | topic_profile, scoring |
| Zotero/라이브러리 도구 | 개인 서지정보 관리 | BibTeX/Zotero 연동 예정 |
| PDF QA 도구 | PDF 파싱, 근거 문장 추적 | parse, cards, evidence |
| academic writing workspace | Markdown/LaTeX 원고 관리 | manuscript/ |
| reviewer/debugger 도구 | 인용 검증, 리뷰어식 비판 | audit, reviewer prompt |

## 4. Evidence-first 원칙

원고 작성은 요약이 아니라 근거 행렬을 중심으로 진행합니다.

- Evidence Matrix에 없는 논문은 확정적으로 인용하지 않습니다.
- 근거 없는 문장은 `[NEEDS_SOURCE]`로 남깁니다.
- citekey, quote, page, section을 최대한 보존합니다.
- 최종 판단은 사람이 승인합니다.

## 5. 데이터 계층

| 계층 | 역할 |
|---|---|
| PDF | 원문 |
| DOI/arXiv/BibTeX | 서지 원본 |
| SQLite | 처리 상태와 메타데이터 |
| Markdown | 사람이 읽는 지식 기록 |
| Evidence Matrix | 주장-근거 연결 |
| Manuscript | 최종 논문 원고 |
| Vector DB | 선택적 검색 캐시 |
