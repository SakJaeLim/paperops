"""Offline portfolio walkthrough using synthetic text; no research claims verified."""
from __future__ import annotations
import argparse
import contextlib
import hashlib
import io
import json
import tempfile
from pathlib import Path
import paperops as po
import paperops_draft_audit as audit


def run_demo(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='paperops-public-') as temporary:
        root = Path(temporary)
        old = (po.ROOT, po.DB, po.LOG, audit.LOG)
        po.ROOT, po.DB, po.LOG = root, root / 'data/metadata/papers.sqlite', root / 'logs/activity.md'
        audit.LOG = po.LOG
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                po.init_db()
                draft = root / 'synthetic-draft.md'
                draft.write_text('# Synthetic demonstration\n\n정확도는 95.5%로 나타났다.\n\n정확도는 99.9%로 나타났다.\n\n이 방법은 항상 우수한 결과를 낸다.\n', encoding='utf-8')
                experiment = root / 'experiment'
                experiment.mkdir()
                (experiment / 'metrics.csv').write_text('metric,value\naccuracy,95.5\n', encoding='utf-8')
                before = hashlib.sha256(draft.read_bytes()).hexdigest()
                result = audit.audit_draft(draft, output_prefix='synthetic', experiment_data=str(experiment), report_dir=str(root / 'reports'), config_path=str(root / 'no-config.yaml'))
                after = hashlib.sha256(draft.read_bytes()).hexdigest()
                matrix = root / 'matrices/evidence_matrix.csv'
                matrix_before = matrix.read_bytes()
                blocked = False
                try:
                    po.cmd_verify_evidence(argparse.Namespace(evidence_id='synthetic', by='demo', method='demo', note='', attest=False, revoke=False))
                except SystemExit:
                    blocked = True
            types = sorted({finding[1] for finding in result['findings']})
            checks = {
                'draft_unchanged': before == after,
                'matching_number_found': result['numeric_summary']['matched_exact'] >= 1,
                'unsupported_number_flagged': 'numeric_not_found_in_data' in types,
                'unsourced_strong_claim_flagged': 'strong_claim_no_source' in types,
                'verification_without_attestation_blocked': blocked,
                'evidence_matrix_unchanged': matrix_before == matrix.read_bytes(),
            }
            if not all(checks.values()):
                raise RuntimeError(f'Demo checks failed: {checks}')
            report = {'scope': 'Synthetic offline draft audit and attestation gate; not literature fact-checking or LLM evaluation.', 'passed': True, 'checks': checks, 'numeric_summary': result['numeric_summary'], 'finding_types': types, 'draft_sha256': before}
            (output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            # Copy only synthetic inputs and portable report tables, never a user's workspace.
            for name, source in [('synthetic-draft.md', draft), ('synthetic-metrics.csv', experiment / 'metrics.csv'), ('findings.csv', result['csv']), ('numeric-checks.csv', result['numeric_csv'])]:
                (output / name).write_bytes(source.read_bytes())
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return report
        finally:
            po.ROOT, po.DB, po.LOG, audit.LOG = old


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('reports/public-demo'))
    run_demo(parser.parse_args().output)
