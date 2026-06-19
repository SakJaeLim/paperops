# PaperOps — エビデンス・ファーストの研究・論文執筆OS

<details align="right">
  <summary>🌐 Language Translation / 言語選択</summary>
  <br />
  <p>
    <a href="README.md">🇬🇧 English</a> | 
    <a href="README.ko.md">🇰🇷 한국어</a> | 
    <a href="README.de.md">🇩🇪 Deutsch</a> | 
    <a href="README.es.md">🇪🇸 Español</a> | 
    <a href="README.zh.md">🇨🇳 中文</a> | 
    **🇯🇵 日本語** | 
    <a href="README.fr.md">🇫🇷 Français</a> | 
    <a href="README.ar.md">🇸🇦 العربية</a>
  </p>
</details>

![PaperOps エンドツーエンド・パイプライン](assets/figures/fig_pipeline.svg)

PaperOps は**研究執筆のライフサイクル全体** — 文献収集、スクリーニング、
PDF 解析、エビデンス抽出、文献管理同期、ガード付き原稿編集、再現可能な
図表生成、ドラフト監査 — を、**45 以上のコマンド**を持つローカルファースト
CLI ひとつで自動化します。

論文を*代筆する*ツールではありません。パイプラインは自動ですが、判断を要する
3 つのポイント — エビデンスの採用、原稿修正の承認、`verified=true` の判定 —
は意図的に人間に残されており、ガード機構が自動化による偽装を防ぎます。

## ライフサイクル全体(ステージ別)

| ステージ | 内容 | 主要コマンド | 自動化 |
|---|---|---|---|
| 1. 収集 | トピックプロファイルに基づき arXiv / Semantic Scholar / OpenAlex から取得 | `collect`, `digest` | 自動 |
| 2. 選別 | 関連度スコアリング、研究軸別スクリーニング、研究ギャップ発見 | `score`, `screen`, `gap`, `brief` | 自動 |
| 3. 取得 | PDF ダウンロード、論文カード・アウトライン生成 | `download-pdfs`, `cards`, `outline` | 自動 |
| 4. 解析 | GROBID で PDF → 構造化セクション/参考文献 | `parse-grobid`, `validate-grobid-artifacts` | 自動 |
| 5. 抽出 | 解析テキストから主張/引用/ページのエビデンス候補を抽出 | `extract-evidence-candidates`, `validate-evidence-candidates` | 自動 |
| 6. レビュー | 候補ごとに採用/修正/却下を決定 | `review-evidence-candidates`, `promotion-plan` | **人間のゲート** |
| 7. 昇格 | 承認済みエビデンスを Evidence Matrix へ移動(`verified=false`) | `promote-evidence`, `audit-promoted-evidence` | ガード付き |
| 8. ページ特定 | 各引用の正確な PDF ページを特定・記録 | `locate-pdf-pages`, `apply-page-metadata` | ガード付き |
| 9. 文献管理 | Zotero / Better BibTeX の正準 BibTeX と citekey を同期 | `sync-zotero`, `check-citekeys` | 自動 |
| 10. 執筆 | 原稿パッチを preview + diff として生成 | `manuscript-patch-preview` | 自動 |
| 11. 適用 | 承認済みパッチをバックアップ + SHA 検証 + LF 書込で適用 | `apply-manuscript-patch` | **人間のゲート** |
| 12. 図表 | 仕様駆動の Graphviz/Mermaid 図、データ捏造禁止 | `propose-figures`, `render-figures`, `apply-figure-placeholder` | ガード付き |
| 13. ドラフト監査 | 任意の草稿(docx/md/qmd)を検査:構造、出典なし主張、誇張、数値と実験出力の照合 | `audit-manuscript-draft` | 自動 |
| 14. 検証 | いかなる自動化も `verified=true` を設定していないことを強制 | `guard-no-auto-verified`, `guard-paperops-overclaim`, `smoke-test` | 自動ガード / **人間の判定** |

## チャット LLM ではなくこれを使う理由

| 関心事 | 一般的な LLM チャット/エージェント | PaperOps |
|---|---|---|
| この文の出典は? | 不明 | Evidence Matrix の `paper_id` + `citekey` + 引用 + ページ |
| 引用の正確性 | ベストエフォート | 正準 BibTeX と `check-citekeys` で照合 |
| 原稿編集 | 直接上書き | preview → diff → 承認 → SHA 検証適用 → バックアップ → 事後監査 |
| 「検証済み」状態 | 暗黙的 | 人間のみが設定可能、ガードが強制 |
| 草稿内の数値 | 未確認 | 実際の実験出力ファイルと自動照合 |
| 再現性 | セッション限り | SQLite + CSV マトリクス + 監査レポート + 活動ログ + 図表ソース |

40 以上のオープンソース研究ツール(PaperQA2、STORM、GPT Researcher、
AI-Scientist、ASReview、gpt_academic、Zotero エコシステム、MCP サーバー —
`docs/03_TOOL_SYNTHESIS.md` 参照)の設計パターンを調査し、**「追跡可能で
人間がレビューしたエビデンスなしに、いかなる主張も原稿に入れない」**という
一原則のもとに再構成しました。

## アーキテクチャ

![PaperOps システムアーキテクチャ](assets/figures/fig_architecture.svg)

| コンポーネント | 役割 | 技術 |
|---|---|---|
| `scripts/paperops.py` | 全ステージとガードを統括するオーケストレーター CLI | Python、標準ライブラリ優先 |
| `scripts/paperops_figures.py` | 仕様駆動の図表生成(ソースを常に保存) | Graphviz + Mermaid |
| `scripts/paperops_draft_audit.py` | ドラフト監査:構造、主張、数値照合 | Python |
| `scripts/build_public_release.py` | ホワイトリスト式公開エクスポート + 機密スキャン | Python |
| 論文 DB | 論文メタデータ、スコア、読書状態 | SQLite |
| Evidence Matrix | 主張/引用/ページ/出典位置/レビュー状態/検証フィールド | CSV(diff フレンドリー) |
| 原稿 | 論文チャプター、ガード付き apply のみで修正 | Quarto (.qmd) |
| 外部サービス | PDF 解析、正準文献管理 | GROBID (Docker), Zotero + Better BibTeX |

データは一方向に流れ、ガード付き各ステップで監査レポートが生成されます:
**API → 論文 DB → PDF → 解析テキスト → エビデンス候補 → (人間) →
Evidence Matrix → パッチ preview → (人間) → 原稿**、変更のたびにガードを再実行。

![エビデンス検証状態遷移](assets/figures/fig_verification_states.svg)

## クイックスタート

```bash
git clone https://github.com/SakJaeLim/paperops.git && cd paperops
python -m venv .venv
# Windows: .venv\Scripts\activate | Unix: source .venv/bin/activate
pip install -r requirements.txt
python scripts/paperops.py init
python scripts/paperops.py status
```

外部サービスなしで即動作:収集、スコアリング、スクリーニング、ドラフト監査、
ガード、図表ソース生成。オプションの追加コンポーネント:

| 依存 | 有効になる機能 | インストール |
|---|---|---|
| GROBID | PDF → 構造化テキスト解析 | `docker run -d -p 8070:8070 lfoppiano/grobid:0.8.0` |
| Zotero + Better BibTeX | 正準文献管理の同期 | zotero.org + Better BibTeX プラグイン |
| Graphviz | SVG/PNG 図表レンダリング | graphviz.org/download |

## 典型的なセッション

```bash
# 収集と選別
python scripts/paperops.py collect --limit 20
python scripts/paperops.py score && python scripts/paperops.py screen --limit 80

# 解析とエビデンス抽出
python scripts/paperops.py download-pdfs --limit 10
python scripts/paperops.py parse-grobid --paper-id <id> --apply
python scripts/paperops.py extract-evidence-candidates --paper-id <id> --apply

# 人間のレビュー後、ガード付き昇格
python scripts/paperops.py review-evidence-candidates --paper-id <id>
python scripts/paperops.py promote-evidence --paper-id <id> --apply

# ガード付き原稿執筆
python scripts/paperops.py manuscript-patch-preview
python scripts/paperops.py apply-manuscript-patch --from-preview <preview.csv> --dry-run
python scripts/paperops.py apply-manuscript-patch --from-preview <preview.csv> --apply

# 自分のドラフトを監査(docx/md/qmd)
python scripts/paperops.py audit-manuscript-draft --input my_thesis_draft.docx

# 図表と最終チェック
python scripts/paperops.py render-figures
python scripts/paperops.py guard-no-auto-verified --promoted-only
python scripts/paperops.py smoke-test
```

![ガード付き原稿適用ワークフロー](assets/figures/fig_guarded_apply.svg)

## ドラフト監査の実例

`audit-manuscript-draft` を実際の KCI 投稿原稿(413 段落)に適用:
378 文をスキャンし、章構造を点検し、出典のない強い主張と誇張表現をフラグし、
草稿内の**全 178 個の数値**を実際の実験出力ファイルと照合 — 不一致 0 件、
丸め誤差 2 件は説明済み、ベースレート 1 件は生の予測ログから再計算して確認。
監査はドラフトを決して修正せず、verified 状態も作りません。著者のための
発見レポート(MD + CSV)のみを生成します。

## ガバナンスルール

1. Evidence Matrix をみだりに変更しない。
2. `verified=true` は決して自動設定されない — verified 状態への自動遷移は
   存在しない。
3. 引用/ページ照合は*出典アライメント*であり、真実性の検証ではない。
4. 原稿編集はバックアップと事後ガード + スモークテストを伴うガード付き
   preview/apply のみ。
5. 関連研究の知見は設計パターンとしてのみ引用し、PaperOps 自体の性能根拠と
   して誇張しない。

## このリポジトリに含まれないもの

コード、設定、設計ドキュメント、生成された図表ソースのみ。著作権上の理由と
「エビデンス基盤は各自の文献で構築すべき」という原則から、収集論文の PDF、
解析全文、引用入り Evidence Matrix、個人の原稿チャプターは意図的に除外。
エクスポートはホワイトリスト式で、リリース前に機密/個人情報スキャンを実施
(`scripts/build_public_release.py`)。

## 正直な限界

- エビデンス抽出はキーワード/ヒューリスティックベース。LLM 支援抽出は別途
  ガード付きステップとして計画中。
- ドラフト監査は人間のレビューのためのヒューリスティックなフラグ付けであり、
  真実性の検証ではない。
- 引用/ページのアライメントは主張の真実性を検証しない — 設計上の意図。
- 実データファイルなしに定量結果の図表は決して生成しない。

## ライセンス

MIT — [LICENSE](LICENSE) を参照。
