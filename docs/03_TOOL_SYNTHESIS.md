# GitHub 논문 에이전트 기능 통합 분석

## 통합 방향

기존 도구를 모두 설치하는 대신 기능 패턴을 흡수합니다.

```text
gpt_paper_assistant류 daily scanner
+ AutoSurveyGPT류 survey automation
+ Zotero 연동류 개인 라이브러리
+ PDF QA류 근거 추적
+ scientific writing workspace
+ PaperDebugger류 reviewer/auditor
= Evidence-first PaperOps
```

## 우선 구현 기능

1. 논문 자동 수집
2. 중복 제거
3. 관련도 점수화
4. PDF 파싱
5. Paper Card
6. Evidence Matrix
7. Outline 생성
8. Citation Audit

## 나중에 붙일 기능

- Zotero BetterBibTeX 자동 동기화
- Vector DB 기반 Hybrid Search
- LLM API 기반 deep reading
- Streamlit/FastAPI 대시보드
- LaTeX 자동 빌드
