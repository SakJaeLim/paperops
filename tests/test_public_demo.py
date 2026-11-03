from pathlib import Path
import importlib.util
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
SPEC = importlib.util.spec_from_file_location('paperops_public_demo', REPO / 'scripts/demo_public.py')
demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo)


def test_public_demo_isolated_and_reproducible(tmp_path):
    original = (demo.po.ROOT, demo.po.DB, demo.po.LOG, demo.audit.LOG)
    result = demo.run_demo(tmp_path / 'output')
    assert result['passed']
    assert all(result['checks'].values())
    assert (demo.po.ROOT, demo.po.DB, demo.po.LOG, demo.audit.LOG) == original
    assert (tmp_path / 'output/summary.json').exists()
    assert '99.9' in (tmp_path / 'output/numeric-checks.csv').read_text(encoding='utf-8-sig')
