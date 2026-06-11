# PaperOps — 근거 우선(Evidence-first) 연구·논문 작성 OS

[English](README.md) | **한국어** | [中文](README.zh.md) | [日本語](README.ja.md) | [Français](README.fr.md) | [العربية](README.ar.md)

![PaperOps 전체 파이프라인](assets/figures/fig_pipeline.svg)

PaperOps는 **연구-집필 전 과정** — 문헌 수집, 스크리닝, PDF 파싱, 근거 추출,
서지 동기화, 가드된 원고 수정, 재현 가능한 figure 생성, 초안 감사 — 를
**45개 이상의 명령**을 가진 로컬 우선 CLI 하나로 자동화합니다.

논문을 *대신 써주는* 도구가 아닙니다. 파이프라인은 자동이지만, 판단이 필요한
세 지점 — 근거 채택, 원고 수정 승인, `verified=true` 판정 — 은 의도적으로
사람에게 남겨져 있으며, 가드(guard)가 자동화의 위조를 차단합니다.

## 전체 라이프사이클 (단계별)

| 단계 | 내용 | 주요 명령 | 자동화 |
|---|---|---|---|
| 1. 수집 | arXiv / Semantic Scholar / OpenAlex에서 토픽 프로필 기반 수집 | `collect`, `digest` | 자동 |
| 2. 선별 | 관련도 점수, 연구축별 스크리닝, 연구공백 탐지 | `score`, `screen`, `gap`, `brief` | 자동 |
| 3. 확보 | PDF 다운로드, 논문 카드·아웃라인 생성 | `download-pdfs`, `cards`, `outline` | 자동 |
| 4. 파싱 | GROBID로 PDF → 구조화된 섹션/참고문헌 | `parse-grobid`, `validate-grobid-artifacts` | 자동 |
| 5. 추출 | 파싱 텍스트에서 claim/quote/page 근거 후보 추출 | `extract-evidence-candidates`, `validate-evidence-candidates` | 자동 |
| 6. 검토 | 후보별 채택/수정/기각 결정 | `review-evidence-candidates`, `promotion-plan` | **사람 관문** |
| 7. 승격 | 승인된 근거를 Evidence Matrix로 이동 (`verified=false`) | `promote-evidence`, `audit-promoted-evidence` | 가드됨 |
| 8. 페이지 확인 | 각 인용문의 정확한 PDF 페이지 탐지·기록 | `locate-pdf-pages`, `apply-page-metadata` | 가드됨 |
| 9. 서지 | Zotero / Better BibTeX 정본 BibTeX와 citekey 동기화 | `sync-zotero`, `check-citekeys` | 자동 |
| 10. 집필 | 원고 패치를 preview + diff로 생성 | `manuscript-patch-preview` | 자동 |
| 11. 반영 | 승인된 패치를 백업 + SHA 검증 + LF 저장으로 반영 | `apply-manuscript-patch` | **사람 관문** |
| 12. Figure | 스펙 기반 Graphviz/Mermaid 도해, 데이터 조작 금지 | `propose-figures`, `render-figures`, `apply-figure-placeholder` | 가드됨 |
| 13. 초안 감사 | 모든 초안(docx/md/qmd) 검사: 구조, 근거 없는 주장, 과장, 수치를 실제 실험 산출물과 대조 | `audit-manuscript-draft` | 자동 |
| 14. 검증 | 어떤 자동화도 `verified=true`를 설정하지 못했음을 강제 | `guard-no-auto-verified`, `guard-paperops-overclaim`, `smoke-test` | 자동 가드 / **사람 판정** |

## 일반 LLM 채팅 대신 이걸 쓰는 이유

| 관심사 | 일반 LLM 채팅/에이전트 | PaperOps |
|---|---|---|
| 이 문장의 출처는? | 불명 | Evidence Matrix의 `paper_id` + `citekey` + 인용문 + 페이지 |
| 인용 정확성 | 최선의 노력 | 정본 BibTeX와 `check-citekeys` 대조 |
| 원고 수정 | 바로 덮어쓰기 | preview → diff → 승인 → SHA 검증 apply → 백업 → 사후 감사 |
| "검증됨" 상태 | 암묵적 | 사람만 설정 가능, 가드가 강제 |
| 초안 속 수치 | 미확인 | 실제 실험 산출 파일과 자동 대조 |
| 재현성 | 세션에 묶임 | SQLite + CSV 매트릭스 + 감사 보고서 + 활동 로그 + figure 소스 |

40개 이상의 오픈소스 연구 도구(PaperQA2, STORM, GPT Researcher, AI-Scientist,
ASReview, gpt_academic, Zotero 생태계, MCP 서버 — `docs/03_TOOL_SYNTHESIS.md`
참조)의 설계 패턴을 분석해, **"추적 가능하고 사람이 검토한 근거 없이는 어떤
주장도 원고에 들어가지 않는다"**는 한 가지 원칙으로 재조립했습니다.

## 아키텍처

![PaperOps 시스템 아키텍처](assets/figures/fig_architecture.svg)

| 구성요소 | 역할 | 기술 |
|---|---|---|
| `scripts/paperops.py` | 전 단계와 가드를 관장하는 오케스트레이터 CLI | Python, 표준 라이브러리 우선 |
| `scripts/paperops_figures.py` | 스펙 기반 figure 생성 (소스 항상 보존) | Graphviz + Mermaid |
| `scripts/paperops_draft_audit.py` | 초안 감사: 구조, 주장, 수치 대조 | Python |
| `scripts/build_public_release.py` | 화이트리스트 기반 공개 export + 비밀정보 스캔 | Python |
| 논문 DB | 수집 논문 메타데이터, 점수, 읽기 상태 | SQLite |
| Evidence Matrix | claim / quote / page / 출처 위치 / 검토 상태 / 검증 필드 | CSV (diff 친화적) |
| 원고 | 학위논문 챕터, 가드된 apply로만 수정 | Quarto (.qmd) |
| 외부 서비스 | PDF 파싱, 정본 서지 | GROBID (Docker), Zotero + Better BibTeX |

데이터는 한 방향으로 흐르고 모든 가드 단계에서 감사 보고서가 생성됩니다:
**API → 논문 DB → PDF → 파싱 텍스트 → 근거 후보 → (사람) → Evidence Matrix →
패치 preview → (사람) → 원고**, 그리고 모든 변경 후 가드 재실행.

![근거 검증 상태 전이](assets/figures/fig_verification_states.svg)

## 빠른 시작

```bash
git clone https://github.com/SakJaeLim/paperops.git && cd paperops
python -m venv .venv
# Windows: .venv\Scripts\activate | Unix: source .venv/bin/activate
pip install -r requirements.txt
python scripts/paperops.py init
python scripts/paperops.py status
```

외부 서비스 없이 바로 동작: 수집, 점수, 스크리닝, 초안 감사, 가드, figure 소스
생성. 선택적 추가 설치:

| 의존성 | 가능해지는 것 | 설치 |
|---|---|---|
| GROBID | PDF → 구조화 텍스트 파싱 | `docker run -d -p 8070:8070 lfoppiano/grobid:0.8.0` |
| Zotero + Better BibTeX | 정본 서지 동기화 | zotero.org + Better BibTeX 플러그인 |
| Graphviz | SVG/PNG figure 렌더링 | graphviz.org/download |

## 전형적인 세션

```bash
# 수집과 선별
python scripts/paperops.py collect --limit 20
python scripts/paperops.py score && python scripts/paperops.py screen --limit 80

# 파싱과 근거 추출
python scripts/paperops.py download-pdfs --limit 10
python scripts/paperops.py parse-grobid --paper-id <id> --apply
python scripts/paperops.py extract-evidence-candidates --paper-id <id> --apply

# 사람 검토 후 가드된 승격
python scripts/paperops.py review-evidence-candidates --paper-id <id>
python scripts/paperops.py promote-evidence --paper-id <id> --apply

# 가드된 원고 작성
python scripts/paperops.py manuscript-patch-preview
python scripts/paperops.py apply-manuscript-patch --from-preview <preview.csv> --dry-run
python scripts/paperops.py apply-manuscript-patch --from-preview <preview.csv> --apply

# 내 초안 감사 (docx/md/qmd) — 구조, 근거 없는 주장, 과장
python scripts/paperops.py audit-manuscript-draft --input my_thesis_draft.docx

# Figure와 최종 점검
python scripts/paperops.py render-figures
python scripts/paperops.py guard-no-auto-verified --promoted-only
python scripts/paperops.py smoke-test
```

![가드된 원고 apply 워크플로우](assets/figures/fig_guarded_apply.svg)

## 초안 감사 실전 사례

`audit-manuscript-draft`를 실제 KCI 투고 원고(413개 문단)에 적용한 결과:
378개 문장 스캔, 장 구조 점검, 근거 없는 강한 주장과 과장 표현 플래깅,
그리고 초안의 **수치 178건 전부**를 실제 실험 산출 파일과 대조 — 불일치 0건,
반올림 차이 2건 해명, 기저율 1건은 원시 예측 로그에서 재계산해 확인.
감사는 초안을 절대 수정하지 않으며 verified 상태도 만들지 않습니다.
저자를 위한 발견 보고서(MD + CSV)만 생성합니다.

## 거버넌스 규칙

1. Evidence Matrix는 함부로 수정하지 않는다.
2. `verified=true`는 절대 자동으로 설정되지 않는다 — verified 상태로 가는
   자동 전이는 존재하지 않는다.
3. 인용문/페이지 매칭은 *출처 정렬*이지 진실 검증이 아니다.
4. 원고 수정은 백업과 사후 가드 + smoke-test가 따르는 가드된 preview/apply로만.
5. 관련연구의 발견은 설계 패턴으로만 인용하며, PaperOps 자체의 성능 증거로
   포장하지 않는다.

## 이 저장소에 포함되지 않은 것

코드, 설정, 설계 문서, 생성된 figure 소스만 포함합니다. 수집한 논문 PDF,
파싱 전문, 인용문이 담긴 Evidence Matrix, 개인 원고 챕터는 저작권 문제와
"근거 기반은 각자의 문헌으로 구축해야 한다"는 원칙에 따라 의도적으로
제외했습니다. export는 화이트리스트 기반이며 릴리스 전 비밀정보/개인정보
스캔을 거칩니다 (`scripts/build_public_release.py`).

## 정직한 한계

- 근거 추출은 키워드/휴리스틱 기반이며, LLM 보조 추출은 별도 가드 단계로 계획.
- 초안 감사는 사람 검토를 위한 휴리스틱 플래깅이지 진실 검증이 아님.
- 인용문/페이지 정렬은 주장의 진실성을 검증하지 않음 — 설계상 의도.
- 정량 결과 figure는 실제 데이터 파일 없이는 절대 생성하지 않음.

## 라이선스

MIT — [LICENSE](LICENSE) 참조.
