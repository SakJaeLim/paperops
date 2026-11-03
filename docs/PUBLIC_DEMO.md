# Offline portfolio walkthrough

```sh
python scripts/demo_public.py
```

Python 3.10+ is required. This walkthrough uses only the standard library and the
project's own modules. It does not call an LLM or download papers. Outputs are
written to `reports/public-demo`; use `--output PATH` for a different directory.
The synthetic inputs, findings CSV, number-check CSV and `summary.json` can be
reviewed together. A failed expectation makes the command fail.

The script initializes a disposable research workspace and audits a short fictional
draft. One numerical claim (95.5%) appears in the synthetic experiment file; another
(99.9%) does not. An unsourced strong claim is also included deliberately. The demo
checks that PaperOps distinguishes these cases, preserves the draft, rejects an
attempt to verify evidence without attestation, and leaves the evidence matrix
unchanged. It does not submit any human attestation.

A matching number is alignment with an input file, not proof that a result is true.
This demo does not evaluate literature retrieval, PDF parsing, LLM extraction,
research validity, or the complete promotion workflow.

## 한국어 소개

PaperOps는 논문과 연구자료의 근거를 정리하고 원고에서 확인이 필요한 내용을
찾는 도구입니다. 공개 데모는 가상 원고와 실험표를 비교해 일치하는 수치와
표에 없는 수치를 구분하고, 출처 없는 단정적인 문장을 표시합니다. 원고를
직접 고치거나 근거를 자동으로 검증 완료 처리하지 않는지도 함께 확인합니다.

## Reproduction checks

```sh
python -m pip install -r requirements.txt pytest
python -m pytest tests/ -q
python scripts/demo_public.py
```

The public release builder includes this demo and its documentation. Release
redaction paths use forward slashes on Windows and Linux for consistent reports.
