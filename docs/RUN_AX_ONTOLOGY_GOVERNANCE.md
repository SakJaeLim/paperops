# PaperOps Runbook: AX Ontology Governance Topic

## Research topic

**온톨로지 기반 AX 운영체계와 생성형 AI 거버넌스/보안/의사결정 자동화 연구**

Working English topic:

**Ontology-based AX operating model and generative AI governance, security, and decision automation**

## Goal

Use PaperOps to collect, triage, and evidence-govern papers for a master's-level AI/Big Data engineering thesis. The output should support a thesis that treats ontology and knowledge graphs not as general philosophy, but as an engineering mechanism for enterprise AX operations, LLM governance, security control, traceability, and automated decision workflows.

## Success criteria

1. Collect papers across ontology/KG, GraphRAG, LLM governance, AI security, policy-as-code, decision automation, MLOps/LLMOps, and enterprise AI operations.
2. Screen papers into `important`, `to_read`, `candidate`, and `screened` using the topic profile.
3. Produce digest, brief, gap report, paper cards, and evidence candidate outputs.
4. Keep all evidence in human-review mode. Do not mark `verified=true` automatically.
5. Produce a defensible related-work base for an AI/Big Data engineering thesis.

## Recommended first run

```bash
git clone https://github.com/SakJaeLim/paperops.git
cd paperops
git checkout topic/ax-ontology-governance

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
# source .venv/bin/activate

pip install -r requirements.txt
python scripts/paperops.py init
python scripts/paperops.py collect --limit 30
python scripts/paperops.py score
python scripts/paperops.py screen --limit 120
python scripts/paperops.py digest --top 30
python scripts/paperops.py gap
python scripts/paperops.py brief
python scripts/paperops.py status
```

## If PDFs are needed

```bash
python scripts/paperops.py download-pdfs --limit 15
```

Optional GROBID parsing:

```bash
docker run -d -p 8070:8070 lfoppiano/grobid:0.8.0
python scripts/paperops.py parse-grobid --paper-id <paper_id> --apply
python scripts/paperops.py extract-evidence-candidates --paper-id <paper_id> --apply
python scripts/paperops.py review-evidence-candidates --paper-id <paper_id>
python scripts/paperops.py promote-evidence --paper-id <paper_id> --apply
python scripts/paperops.py guard-no-auto-verified --promoted-only
python scripts/paperops.py smoke-test
```

## Thesis framing to use while reviewing papers

### Core research question

How can an ontology-based operating model improve traceability, governance, security control, and decision automation in enterprise AX systems using generative AI?

### Sub-questions

1. What structural gap exists between conventional RAG/LLM applications and ontology/KG-based operational systems?
2. Which ontology components are required for AX operations: object, relation, policy, state, action, evidence, user role, risk, control, decision, and audit log?
3. How can SHACL/rule constraints and access-control metadata reduce unsafe or non-compliant LLM actions?
4. How should GraphRAG and agentic workflows be evaluated beyond answer accuracy, using traceability, reproducibility, governance compliance, and decision quality?
5. What reference architecture can integrate ontology/KG, RAG, LLM agents, governance controls, security guardrails, and human approval gates?

### Candidate contribution

A reference architecture and evaluation framework for ontology-based AX operating systems that combines:

- enterprise ontology / knowledge graph
- GraphRAG context construction
- policy and security constraints
- decision automation workflow
- human-in-the-loop approval
- audit and lineage tracking
- engineering evaluation metrics

## Review lens

For each paper, extract only the following:

1. Problem definition
2. Limitation of existing methods
3. Engineering contribution
4. Dataset/system setting
5. Evaluation metrics
6. Relevance to AX ontology governance
7. Whether it supports architecture, governance, security, or decision automation

## Expected thesis chapter skeleton

1. Introduction
   - AX systems need controlled, traceable, and governable generative AI.
2. Related Work
   - Ontology/KG, GraphRAG, AI governance, LLM security, decision automation, LLMOps.
3. Problem Definition
   - Conventional LLM/RAG systems lack operational semantics, policy binding, and auditability.
4. Proposed Architecture
   - Ontology-based AX operating model with KG, GraphRAG, agent workflow, policy/rule layer, security guardrails, and approval gates.
5. Implementation / Prototype
   - Domain ontology, graph schema, retrieval pipeline, decision workflow, logging, and validation rules.
6. Evaluation
   - Accuracy, groundedness, traceability, rule violation rate, decision reproducibility, latency, and human-review efficiency.
7. Discussion
   - Enterprise applicability, limitations, governance risks, and future work.
8. Conclusion

## Immediate manual gates

After the first run, inspect:

- `reports/daily_digest/`
- `reports/survey_reports/`
- `data/metadata/papers.sqlite`
- `matrices/evidence_matrix.csv`
- `logs/ACTIVITY_LOG.md`

Do not promote evidence until the paper has been manually checked.
