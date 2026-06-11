# PaperOps — Evidence-first Thesis Writing OS

PaperOps is a local-first CLI that manages the full lifecycle of evidence-based
academic writing: literature collection, PDF parsing, evidence extraction,
human-gated verification, and **guarded** manuscript editing.

It is not an auto-paper-writer. It is a governance layer that makes every
claim in your manuscript traceable to a source quote, page, and review
decision — and makes every manuscript change pass through a
preview → diff → human approval → apply → audit loop.

![PaperOps end-to-end pipeline](assets/figures/fig_pipeline.svg)

## Why another research agent?

Most LLM research tools optimize for *generation speed*. PaperOps optimizes
for *evidence accountability*:

| Concern | Typical LLM chat / agent | PaperOps |
|---|---|---|
| Where did this sentence come from? | unknown | `paper_id` + `citekey` + quote + page in Evidence Matrix |
| Citation correctness | best-effort | `check-citekeys` against canonical BibTeX (Zotero / Better BibTeX) |
| Manuscript edits | direct overwrite | guarded preview → diff → approval → SHA-checked apply → backup |
| "Verified" status | implied | `verified=true` can **only** be set by a human; guards enforce it |
| Overclaiming | common | `guard-paperops-overclaim`, domain-specific claim audits |
| Reproducibility | session-bound | SQLite + CSV matrices + audit reports + activity log |

Design patterns were synthesized from a survey of 40+ open-source research
tools (PaperQA2, STORM, GPT Researcher, AI-Scientist, ASReview, gpt_academic,
Zotero ecosystems, arXiv/Semantic Scholar MCP servers, and others — see
`docs/03_TOOL_SYNTHESIS.md`). PaperOps re-assembles their best ideas around a
single principle: **no claim enters the manuscript without traceable,
human-reviewed evidence.**

## Core governance rules

1. The Evidence Matrix is never modified casually.
2. `verified=true` is never set automatically — there is no automated
   transition into the verified state.
3. Quote/page matching is *source alignment*, not truth validation.
4. Manuscript edits happen only through the guarded preview/apply flow,
   with backups and post-apply guard + smoke-test checks.
5. Findings from related work are framed as design patterns, never as
   performance evidence for PaperOps itself.

![Evidence verification state transitions](assets/figures/fig_verification_states.svg)

## Architecture

![PaperOps system architecture](assets/figures/fig_architecture.svg)

- **CLI** — `scripts/paperops.py` (single-file orchestrator, stdlib-first)
- **Paper DB** — SQLite (`papers.sqlite`)
- **Parsing** — GROBID (Docker) for PDF → TEI sections/references
- **Bibliography** — Zotero + Better BibTeX canonical `references.bib`
- **Evidence Matrix** — CSV with claim / quote / page / source location /
  review status / verification fields
- **Manuscript** — Quarto chapters, edited only via guarded apply
- **Figures** — spec-driven Graphviz/Mermaid generation
  (`scripts/paperops_figures.py`); sources always saved for reproducibility

## Quickstart

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Unix: source .venv/bin/activate
pip install -r requirements.txt
python scripts/paperops.py init
python scripts/paperops.py status
```

Optional external services:

```bash
# GROBID for PDF parsing
docker run -d -p 8070:8070 lfoppiano/grobid:0.8.0
# Graphviz for figure rendering (https://graphviz.org/download/)
```

## Typical workflow

```bash
# 1. Collect and triage literature
python scripts/paperops.py collect --limit 20
python scripts/paperops.py score
python scripts/paperops.py screen --limit 80
python scripts/paperops.py download-pdfs --limit 10

# 2. Parse and extract evidence candidates
python scripts/paperops.py parse-grobid --paper-id <id> --apply
python scripts/paperops.py extract-evidence-candidates --paper-id <id> --apply

# 3. Human review and promotion (guarded)
python scripts/paperops.py review-evidence-candidates --paper-id <id>
python scripts/paperops.py promotion-plan --paper-id <id> --dry-run
python scripts/paperops.py promote-evidence --paper-id <id> --apply

# 4. Guarded manuscript writing
python scripts/paperops.py manuscript-patch-preview
python scripts/paperops.py apply-manuscript-patch --from-preview <preview.csv> --dry-run
python scripts/paperops.py apply-manuscript-patch --from-preview <preview.csv> --apply

# 5. Figures (spec-driven, reproducible)
python scripts/paperops.py propose-figures
python scripts/paperops.py render-figures
python scripts/paperops.py figure-placeholder-preview
python scripts/paperops.py apply-figure-placeholder --from-preview <preview.csv> --apply

# 6. Always re-verify
python scripts/paperops.py guard-no-auto-verified --promoted-only
python scripts/paperops.py smoke-test
```

![Guarded manuscript apply workflow](assets/figures/fig_guarded_apply.svg)

## What this repo does NOT include

This public release contains code, configuration, design documents, and
generated figure sources only. It deliberately excludes collected paper PDFs,
parsed full texts, evidence matrices with quotes, and personal manuscript
chapters — both for copyright reasons and because your evidence base should
be built from your own literature.

## Honest limitations

- Evidence extraction is keyword/heuristic-based; an LLM-assisted extractor
  is a planned, separately-guarded step.
- Quote/page alignment does not validate the truth of a claim — by design.
- PaperQA2/LangGraph-style deep automation is intentionally deferred until
  the governance layer is stable.
- Quantitative result figures are never generated without an actual data
  file; the figure module refuses to fabricate results.

## License

MIT — see [LICENSE](LICENSE).
