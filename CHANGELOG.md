# Changelog

## v0.2.0 — 2026-07-27

Full-stack overhaul driven by an independent live audit (fresh-clone testing on
Python 3.10–3.13 + line-by-line review). Every finding below was reproduced
before fixing and covered by a regression test where practical. 179+ tests and
a 4-version CI matrix are new in this release.

### Fixed — critical

- **The entire CLI was a `SyntaxError` on Python 3.10/3.11** (a backslash
  inside an f-string expression, legal only from Python 3.12/PEP 701).
  `pyproject.toml` declared `>=3.10` while no 3.10/3.11 user could run any
  command. CI now compiles every script on 3.10–3.13.
- **`review-evidence-candidates` destroyed other papers' human review
  decisions**: it overwrote the shared review queue with only the current
  paper's rows. The queue is now merged — regenerating paper B's queue
  preserves paper A's pending decisions (regression-tested, including an
  Excel-style resave in between).
- **Excel round-trips broke every CSV validator**: BOM from "CSV UTF-8" made
  header validation fail; cp949 re-saves crashed with `UnicodeDecodeError`;
  a stray empty column crashed guarded rewrites mid-write. All CSV reads are
  now BOM-tolerant (`utf-8-sig`) with stray-column capture, all CSV writes
  include a BOM (Korean text renders correctly in Excel), and all guarded
  matrix rewrites are atomic (temp file + `os.replace`) with row projection.
- **`collect` gathered off-topic papers**: the arXiv query was sent as a bare
  keyword soup (matching loosely, sorted by newest — returning unrelated
  brand-new papers), OpenAlex was called without `mailto` (constant HTTP 429),
  and Crossref returned "Decision letter …" peer-review artifacts. Queries are
  now explicit boolean expressions (auto-converted when needed,
  `sortBy=relevance`), `mailto`/API keys/User-Agent are actually read from
  `config/sources.yaml`, all HTTP calls retry with backoff honoring
  `Retry-After`, Crossref filters non-paper types, and a per-source circuit
  breaker stops a failing source from burning minutes of backoff per run.
- **`migrate-evidence` could set `verified=true` from `1`/`yes`/`y`** (e.g.
  Excel booleans) — the one automation path that violated the core invariant.
  Only the exact literal `true` is preserved now; truthy variants are demoted
  to `false` and listed in the migration report.

### Fixed — high

- `sync-zotero --dry-run --apply` silently applied; now rejected like every
  other command. Applied citekey renames now propagate to the Evidence Matrix
  (with backup) instead of stranding old citekeys everywhere.
- Legacy `extract-evidence` appended schema-drifted 14-column rows into the
  migrated 19+-column guarded matrix; it is now blocked with instructions for
  the guarded pipeline.
- Legacy `audit` counted `user@gmail.com` as citekey `gmail` and matched
  citekeys by substring against raw CSV text; it now uses the email-safe
  pattern over both manuscript roots against the real citekey column.
- Cross-source duplicates: the same paper arriving from arXiv (arXiv id only)
  and OpenAlex/Crossref (DOI only) produced two rows. Upsert now matches by
  normalized DOI → arXiv id → normalized title, and merges missing
  identifiers/abstract into the existing row.
- BibTeX arXiv extraction bug that produced the literal id `arxiv` and
  treated any eprint as an arXiv id.
- Manuscript backups were written inside the guarded `05_manuscript/` tree,
  tripping `guard-no-auto-verified` after every legitimate apply and
  double-counting citekeys from backups. Backups now live in
  `backups/manuscript/`, and citekey scans skip any `backups` directory.
- The manuscript-change approval gate accepted any file containing four magic
  substrings. It now also cross-checks the report's recorded post-apply
  SHA-256 values against the manuscript files on disk, rejecting stale or
  fabricated reports.
- PDF downloads saved HTML paywall pages as `.pdf` (later poisoning
  GROBID/PyMuPDF); content-type/magic-byte checks added.
- `backup` never included `papers.sqlite`; `data/metadata` (and
  `05_manuscript`, `tests`) are now in the archive.
- `run_pipeline.py` emoji output crashed with `UnicodeEncodeError` whenever
  stdout was redirected on cp949 Windows, and the whole "one-click" run
  failed if Graphviz was missing. Output is now ASCII, figure rendering
  degrades gracefully (`--strict` restores hard failure), and `--skip-figures`
  was added.
- Silent-zero traps: `extract-evidence-candidates` now errors when the GROBID
  artifact directory is missing instead of "successfully" writing nothing;
  `collect` warns loudly when 0 papers are collected.
- `doctor` mislabeled the mailto check, missed `quarto.cmd` on Windows
  (`shutil.which` now used), and subprocess output is decoded as UTF-8
  everywhere instead of the console codepage.
- GROBID TEI responses are decoded as UTF-8 explicitly (no more charset
  guessing); `screen` thresholds come from `config/scoring.yaml` instead of a
  hardcoded 0.35; section headings containing `;` no longer corrupt
  `source_location`; `scoring.yaml` no longer pins `current_year` (recency
  scoring stays correct after 2026).

### Added

- **`verify-evidence` + verification ledger** — the missing half of the
  governance model. A human sets `verified=true` with `--by NAME --attest`
  (or reverts with `--revoke`); every action is recorded in
  `matrices/verification_ledger.csv`. `guard-no-auto-verified` and the
  promoted-row QA now PASS attested rows and FAIL any verified row without a
  matching ledger attestation — previously the guard failed forever the
  moment a human legitimately verified anything.
- **`extract-evidence-llm`** — guarded LLM-assisted evidence extraction
  (OpenAI-compatible endpoints). The model only proposes candidates; every
  `exact_quote` is verbatim-verified against the GROBID-parsed section text
  (paraphrases and inventions are dropped and reported), and survivors enter
  the same human review → promotion → guarded apply pipeline with
  `verified=false`. This makes `config/prompts/evidence_extractor.md` live
  configuration instead of dead weight.
- **Automatic numeric cross-check in `audit-manuscript-draft`**
  (`--experiment-data DIR`): every number in the draft is checked against
  experiment output files with exact/rounded/not-found statuses — the feature
  the README previously described but the code did not contain.
- **Draft-audit upgrades**: docx footnotes/endnotes parsed (footnote-cited
  sentences count as sourced), tab/break handling, cp949/euc-kr fallback with
  loud warnings instead of silent `errors='ignore'` destruction, Korean
  citation patterns (`(김철수, 2020)`, `[1]`), English strong-claim/overclaim
  detectors, heading-anchored chapter checks, and
  `config/draft_audit.yaml` for topic-specific lists.
- **Figure safety**: heading targeting is line-anchored and code-fence-aware
  (no more inserting under `### X Details` when `## X` was approved),
  ambiguous/missing headings block the row, applies are idempotent, apply
  reports carry real counts + the preview's SHA-256, and figure specs moved
  to `config/figures.yaml`.
- **`make-page-metadata-preview` / `make-outline-insertion-template`** —
  producers for the two guarded-apply input files that previously had to be
  hand-authored with no documentation.
- **`promote-evidence --ready-only`** — apply ready rows while blocked rows
  are reported, instead of one blocked candidate freezing the whole paper.
- **Release hardening**: the secret scan grew from 4 patterns to a named
  ruleset (any email with allowlist, GitHub fine-grained/classic tokens,
  private-key blocks, AWS/Google/Slack/JWT shapes, config-assignment
  secrets); exported configs are sanitized (`*api_key`/`*mailto` values
  blanked and reported); scan failures quarantine the dist instead of leaving
  it publishable; `tests/`, `.github/`, `CHANGELOG.md`, and the AX runbook
  joined the whitelist.
- **Tests + CI**: 179+ pytest cases across core, draft audit, figures, and
  release scanning; GitHub Actions matrix on Python 3.10/3.11/3.12/3.13 with
  compile, test, and offline-smoke stages.

### Changed

- Personal research data moved out of code into config:
  per-candidate review decisions → `config/review_overrides.yaml`,
  corpus/domain keyword lists → `config/qa_profile.yaml`,
  figure specs → `config/figures.yaml`,
  draft-audit source lists → `config/draft_audit.yaml`.
  Shipped values preserve the original author's behavior; other users edit
  YAML instead of Python.
- `config/sources.yaml` is now real configuration: per-source enable flags,
  limits, sleep intervals, mailto, and API keys are read by `collect`.
- Semantic Scholar collection moved into the main `collect` command (with
  API-key header support); the unregistered, crash-prone `paperops_extra.py`
  was removed.
- `requirements.txt`/`pyproject.toml` dropped `pandas`, `rapidfuzz`,
  `bibtexparser`, `pypdf` — none were imported anywhere, and bibtexparser's
  sdist-only build broke installs on some systems. Version bumped to 0.2.0.
- `topic_profile.yaml` queries rewritten as explicit boolean expressions;
  README (en/ko) updated to match actual behavior — including correcting the
  claim that numeric cross-checking was automatic before it was.
