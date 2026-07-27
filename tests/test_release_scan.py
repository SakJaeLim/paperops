# -*- coding: utf-8 -*-
"""Tests for scripts/build_public_release.py.

Covers the secret scan rules, the email allowlist, the config
sanitizer, the quarantine-on-failure behavior, and the whitelist build,
all against miniature trees under tmp_path (never the real repo dist/).

Every secret-shaped fixture is assembled at runtime by string
concatenation so this file never contains a contiguous secret-looking
string: the test suite itself is exported into the public release and
scanned by the very rules it tests.
"""
import importlib.util
import re
from pathlib import Path

import pytest

SCRIPT_PATH = (Path(__file__).resolve().parents[1]
               / 'scripts' / 'build_public_release.py')


def _load_module():
    spec = importlib.util.spec_from_file_location(
        'build_public_release_under_test', str(SCRIPT_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bpr = _load_module()

SP = chr(32)  # single space, used to keep key-block fixtures non-contiguous

# rule name -> fake fixture that must trigger exactly that rule
FAKE = {
    'email-address': 'kevin' + '@' + 'gm' + 'ail.com',
    'github-fine-grained-pat': 'github' + '_pat_' + 'x' * 22,
    'github-token': 'gh' + 'p_' + 'A' * 36,
    'private-key-block': ('-----BEGIN' + SP + 'RSA' + SP + 'PRIVATE'
                          + SP + 'KEY-----'),
    'aws-access-key': 'AK' + 'IA' + 'ABCDEFGHIJKLMNOP',
    'sk-secret-key': 'sk' + '-' + 'a' * 24,
    'slack-token': 'xo' + 'xb' + '-' + '1234567890' + 'abcd',
    'google-api-key': 'AI' + 'za' + 'B' * 35,
    'jwt': 'ey' + 'J' + 'a' * 24 + '.' + 'ey' + 'J' + 'b' * 8,
    'config-assignment-secret': 'api' + '_key: "' + 'A' * 20 + '"',
}


def _fake_email():
    return 'someone' + '@' + 'gm' + 'ail.com'


def scan_text(tmp_path, text):
    d = tmp_path / 'scan_dir'
    d.mkdir(exist_ok=True)
    (d / 'sample.md').write_text(text, encoding='utf-8')
    return bpr.sanitize_scan(d)


# ---------------------------------------------------------------- patterns


def test_patterns_are_named_compiled_pairs():
    assert bpr.FORBIDDEN_PATTERNS
    for name, pat in bpr.FORBIDDEN_PATTERNS:
        assert isinstance(name, str) and name
        assert hasattr(pat, 'finditer')


def test_fixtures_cover_every_rule():
    assert {n for n, _ in bpr.FORBIDDEN_PATTERNS} == set(FAKE)


@pytest.mark.parametrize('rule', sorted(FAKE))
def test_rule_fires_and_report_names_it(rule, tmp_path):
    issues = scan_text(tmp_path, 'before ' + FAKE[rule] + ' after\n')
    assert issues, rule
    assert any(': ' + rule + ': ' in i for i in issues), issues


@pytest.mark.parametrize('prefix_char', list('pousr'))
def test_github_token_prefix_variants(prefix_char, tmp_path):
    token = 'gh' + prefix_char + '_' + 'z' * 31
    issues = scan_text(tmp_path, token + '\n')
    assert any(': github-token: ' in i for i in issues)


@pytest.mark.parametrize('kind', ['', 'RSA', 'EC', 'OPENSSH', 'DSA', 'PGP'])
def test_private_key_variants(kind, tmp_path):
    middle = (SP + kind) if kind else ''
    block = '-----BEGIN' + middle + SP + 'PRIVATE' + SP + 'KEY-----'
    issues = scan_text(tmp_path, block + '\n')
    assert any(': private-key-block: ' in i for i in issues)


def test_sk_ant_variant(tmp_path):
    token = 'sk' + '-ant-' + 'q' * 22
    issues = scan_text(tmp_path, token + '\n')
    assert any(': sk-secret-key: ' in i for i in issues)


@pytest.mark.parametrize('chan', list('baprs'))
def test_slack_token_variants(chan, tmp_path):
    token = 'xo' + 'x' + chan + '-' + '0' * 12
    issues = scan_text(tmp_path, token + '\n')
    assert any(': slack-token: ' in i for i in issues)


def test_short_or_broken_tokens_do_not_fire(tmp_path):
    text = '\n'.join([
        'ey' + 'J' + 'abc' + '.ey' + 'J' + 'x',   # far below jwt threshold
        'sk' + '-' + 'a' * 10,                    # below length threshold
        'task-based sk' + '-learn workflow',      # ordinary prose
        'gh' + 'p_' + 'A' * 10,                   # below length threshold
    ]) + '\n'
    assert scan_text(tmp_path, text) == []


# ------------------------------------------------------- email allowlist


def test_allowlisted_emails_pass(tmp_path):
    text = '\n'.join([
        'contact: someone@example.com',
        'or: another.person@example.org',
        'bot: noreply@anthropic.com',
        'git: 12345+octo-cat@users.noreply.github.com',
    ]) + '\n'
    assert scan_text(tmp_path, text) == []


def test_non_allowlisted_email_fires(tmp_path):
    addr = 'research.lead' + '@' + 'univ' + '.ac.kr'
    issues = scan_text(tmp_path, 'contact ' + addr + '\n')
    assert any(': email-address: ' in i for i in issues)


# ------------------------------------- config-assignment rule discipline


def test_empty_config_values_do_not_fire(tmp_path):
    # includes the cross-line trap: an empty key directly followed by a
    # long identifier on the next line must not match
    text = '\n'.join([
        'semantic_scholar_api_key: ""',
        "download_token: ''",
        'password:',
        'semantic_scholar_backup_api_key: ""',
        'api_key =',
        'secret: ""  # fill in locally',
    ]) + '\n'
    assert scan_text(tmp_path, text) == []


def test_config_assignment_fires_on_yaml_and_python_forms(tmp_path):
    yaml_form = 'semantic_scholar_' + 'api_key: ' + 'k' * 20
    py_form = 'tok' + 'en = "' + 'h' * 18 + '"'
    upper_form = 'SEC' + 'RET: ' + 'Z' * 16
    for sample in (yaml_form, py_form, upper_form):
        issues = scan_text(tmp_path, sample + '\n')
        assert any(': config-assignment-secret: ' in i for i in issues), sample


# ------------------------------------------------------ config sanitizer


def test_sanitize_configs_blanks_values_and_preserves_lines(tmp_path):
    dist = tmp_path / 'public_release'
    cfg = dist / 'config'
    cfg.mkdir(parents=True)
    email = _fake_email()
    lines = [
        '# collection endpoints',
        'user_agent: "PaperOps/0.1 (mailto:' + email + ')"',
        'openalex_mailto: "' + email + '"',
        'semantic_scholar_api_key: ""',
        'my_api_key: "' + 'S' * 20 + '"',
        '  admin_token: ' + 'b' * 18,
        'plain_key: value',
        'sources:',
        '  arxiv:',
        '    enabled: true',
    ]
    (cfg / 'sources.yaml').write_text('\n'.join(lines) + '\n',
                                      encoding='utf-8')
    red = bpr.sanitize_configs(dist)
    out = (cfg / 'sources.yaml').read_text(encoding='utf-8').splitlines()
    assert out[0] == '# collection endpoints'
    assert out[1] == 'user_agent: ""'
    assert out[2] == 'openalex_mailto: ""'
    assert out[3] == 'semantic_scholar_api_key: ""'   # already blank: kept
    assert out[4] == 'my_api_key: ""'
    assert out[5] == '  admin_token: ""'              # indentation preserved
    assert out[6] == 'plain_key: value'
    assert out[7:] == ['sources:', '  arxiv:', '    enabled: true']
    assert red == [
        ('config/sources.yaml', 'user_agent'),
        ('config/sources.yaml', 'openalex_mailto'),
        ('config/sources.yaml', 'my_api_key'),
        ('config/sources.yaml', 'admin_token'),
    ]
    # redaction records never carry the removed values
    assert all(email not in rel + key for rel, key in red)
    # second pass is a no-op: already-blank values are not re-reported
    assert bpr.sanitize_configs(dist) == []


def test_user_agent_without_email_is_kept(tmp_path):
    dist = tmp_path / 'public_release'
    cfg = dist / 'config'
    cfg.mkdir(parents=True)
    body = 'user_agent: "PaperOps/0.1 (research bot)"\nrate: 1.0\n'
    (cfg / 'other.yaml').write_text(body, encoding='utf-8')
    assert bpr.sanitize_configs(dist) == []
    assert (cfg / 'other.yaml').read_text(encoding='utf-8') == body


# ----------------------------------------------------------- quarantine


def test_quarantine_moves_dist_aside(tmp_path):
    dist = tmp_path / 'public_release'
    dist.mkdir()
    (dist / 'README.md').write_text('hello\n', encoding='utf-8')
    q = bpr.quarantine(dist, stamp='20260101_000000')
    assert q == tmp_path / 'public_release_QUARANTINE_20260101_000000'
    assert not dist.exists()
    assert (q / 'README.md').read_text(encoding='utf-8') == 'hello\n'


def test_quarantine_replaces_prior_same_name(tmp_path):
    dist = tmp_path / 'public_release'
    dist.mkdir()
    (dist / 'new.txt').write_text('new\n', encoding='utf-8')
    prior = tmp_path / 'public_release_QUARANTINE_20260101_000000'
    prior.mkdir()
    (prior / 'old.txt').write_text('old\n', encoding='utf-8')
    q = bpr.quarantine(dist, stamp='20260101_000000')
    assert q == prior
    assert (q / 'new.txt').exists()
    assert not (q / 'old.txt').exists()
    assert not dist.exists()


# ------------------------------------------------- build + run end to end


def _make_mini_tree(root):
    (root / 'scripts').mkdir(parents=True)
    (root / 'scripts' / 'paperops.py').write_text('print("ok")\n',
                                                 encoding='utf-8')
    (root / 'README.md').write_text('# mini\n', encoding='utf-8')
    (root / 'CHANGELOG.md').write_text('# changelog\n', encoding='utf-8')
    docs = root / 'docs'
    docs.mkdir()
    (docs / 'RUN_AX_ONTOLOGY_GOVERNANCE.md').write_text('# runbook\n',
                                                        encoding='utf-8')
    cfg = root / 'config'
    cfg.mkdir()
    (cfg / 'sources.yaml').write_text(
        'user_agent: "Mini/0.1 (mailto:' + _fake_email() + ')"\n'
        'openalex_mailto: "' + _fake_email() + '"\n'
        'semantic_scholar_api_key: ""\n',
        encoding='utf-8')
    t = root / 'tests'
    t.mkdir()
    (t / 'test_dummy.py').write_text('def test_ok():\n    assert True\n',
                                     encoding='utf-8')
    gh = root / '.github' / 'workflows'
    gh.mkdir(parents=True)
    (gh / 'ci.yml').write_text('name: ci\n', encoding='utf-8')
    figs = root / 'reports' / 'figures' / 'src'
    figs.mkdir(parents=True)
    (figs / 'fig.mmd').write_text('graph TD;\n', encoding='utf-8')
    (root / 'reports' / 'figures' / 'pic.svg').write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"/>\n', encoding='utf-8')


def _expected_missing(root):
    miss = [s for s, _ in bpr.WHITELIST_FILES if not (root / s).exists()]
    miss += [s for s, _ in bpr.WHITELIST_DIRS if not (root / s).exists()]
    miss += [s for s, _, _ in bpr.WHITELIST_GLOBS if not (root / s).exists()]
    return miss


def test_whitelist_covers_new_public_paths():
    file_srcs = {s for s, _ in bpr.WHITELIST_FILES}
    dir_srcs = {s for s, _ in bpr.WHITELIST_DIRS}
    assert 'docs/RUN_AX_ONTOLOGY_GOVERNANCE.md' in file_srcs
    assert 'CHANGELOG.md' in file_srcs
    assert 'tests' in dir_srcs
    assert '.github' in dir_srcs


def test_run_builds_sanitizes_and_reports(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    _make_mini_tree(root)
    dist = root / 'dist' / 'public_release'
    assert bpr.run(check=False, root=root, dist=dist) == 0
    for rel in ('README.md', 'CHANGELOG.md',
                'docs/RUN_AX_ONTOLOGY_GOVERNANCE.md',
                'tests/test_dummy.py', '.github/workflows/ci.yml',
                'assets/figures/pic.svg', 'assets/figures/src/fig.mmd',
                '.gitignore'):
        assert (dist / rel).exists(), rel
    # exported config copy is sanitized; the source tree stays untouched
    exported = (dist / 'config' / 'sources.yaml').read_text(encoding='utf-8')
    assert 'user_agent: ""' in exported
    assert 'openalex_mailto: ""' in exported
    assert 'semantic_scholar_api_key: ""' in exported
    assert '@' not in exported
    assert '@' in (root / 'config' / 'sources.yaml').read_text(
        encoding='utf-8')
    info = (dist / 'RELEASE_INFO.txt').read_text(encoding='utf-8')
    n_missing = len(_expected_missing(root))
    assert n_missing > 0  # the mini tree lacks most whitelisted sources
    assert 'missing_whitelist_warnings=' + str(n_missing) in info
    assert 'config_redactions=2' in info
    assert 'redacted: config/sources.yaml: user_agent' in info
    assert 'redacted: config/sources.yaml: openalex_mailto' in info
    assert 'sanitize_issues=0' in info
    # file count is taken after all writes and excludes RELEASE_INFO.txt
    reported = int(re.search(r'files=(\d+)', info).group(1))
    on_disk = [p for p in dist.rglob('*')
               if p.is_file() and p.name != 'RELEASE_INFO.txt']
    assert reported == len(on_disk)
    assert reported == bpr.release_file_count(dist)


def test_run_quarantines_on_scan_failure(tmp_path, capsys):
    root = tmp_path / 'repo'
    root.mkdir()
    _make_mini_tree(root)
    leaked = 'AK' + 'IA' + 'ZZZZZZZZZZZZZZZZ'
    (root / 'README.md').write_text('# mini\nkey ' + leaked + '\n',
                                    encoding='utf-8')
    dist = root / 'dist' / 'public_release'
    assert bpr.run(check=False, root=root, dist=dist) == 1
    assert not dist.exists()
    quarantined = list((root / 'dist').glob('public_release_QUARANTINE_*'))
    assert len(quarantined) == 1
    assert (quarantined[0] / 'README.md').exists()
    out = capsys.readouterr().out
    assert 'Nothing publishable remains' in out
    assert ': aws-access-key: ' in out


def test_check_mode_scans_without_rewriting_report(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    _make_mini_tree(root)
    dist = root / 'dist' / 'public_release'
    assert bpr.run(check=False, root=root, dist=dist) == 0
    info_before = (dist / 'RELEASE_INFO.txt').read_text(encoding='utf-8')
    assert bpr.run(check=True, root=root, dist=dist) == 0
    assert dist.exists()
    assert (dist / 'RELEASE_INFO.txt').read_text(
        encoding='utf-8') == info_before


def test_check_mode_missing_dist_errors(tmp_path):
    assert bpr.run(check=True, root=tmp_path, dist=tmp_path / 'nope') == 1


def test_check_mode_quarantines_dirty_dist(tmp_path):
    dist = tmp_path / 'public_release'
    dist.mkdir()
    (dist / 'note.md').write_text('token' + ': "' + 'v' * 18 + '"\n',
                                  encoding='utf-8')
    assert bpr.run(check=True, root=tmp_path, dist=dist) == 1
    assert not dist.exists()
    assert list(tmp_path.glob('public_release_QUARANTINE_*'))
