# PaperOps — OS de recherche et de rédaction de thèse « evidence-first »

[English](README.md) | [한국어](README.ko.md) | [中文](README.zh.md) | [日本語](README.ja.md) | **Français** | [العربية](README.ar.md)

![Pipeline PaperOps de bout en bout](assets/figures/fig_pipeline.svg)

PaperOps automatise **l'ensemble du cycle de vie de la rédaction scientifique**
— collecte de littérature, tri, analyse de PDF, extraction de preuves,
synchronisation bibliographique, édition de manuscrit sous garde-fous,
génération de figures reproductibles et audit de brouillons — via une seule
CLI locale comptant **plus de 45 commandes**.

Ce n'est *pas* un rédacteur automatique d'articles. Le pipeline est
automatisé, mais trois points de jugement sont délibérément réservés à
l'humain : l'adoption des preuves, l'approbation des modifications du
manuscrit et la décision `verified=true`. Des garde-fous empêchent toute
étape automatisée de les falsifier.

## Le cycle complet, étape par étape

| Étape | Contenu | Commandes clés | Automatisation |
|---|---|---|---|
| 1. Collecte | Récupération depuis arXiv / Semantic Scholar / OpenAlex selon un profil thématique | `collect`, `digest` | Automatique |
| 2. Tri | Score de pertinence, criblage par axes de recherche, détection des lacunes | `score`, `screen`, `gap`, `brief` | Automatique |
| 3. Acquisition | Téléchargement des PDF, fiches de lecture et plans | `download-pdfs`, `cards`, `outline` | Automatique |
| 4. Analyse | PDF → sections/références structurées via GROBID | `parse-grobid`, `validate-grobid-artifacts` | Automatique |
| 5. Extraction | Candidats de preuve (affirmation/citation/page) depuis le texte analysé | `extract-evidence-candidates`, `validate-evidence-candidates` | Automatique |
| 6. Revue | Décision garder / réviser / rejeter pour chaque candidat | `review-evidence-candidates`, `promotion-plan` | **Porte humaine** |
| 7. Promotion | Transfert des preuves approuvées vers la matrice de preuves (`verified=false`) | `promote-evidence`, `audit-promoted-evidence` | Sous garde-fous |
| 8. Localisation | Recherche et enregistrement de la page PDF exacte de chaque citation | `locate-pdf-pages`, `apply-page-metadata` | Sous garde-fous |
| 9. Bibliographie | Synchronisation des citekeys avec le BibTeX canonique Zotero / Better BibTeX | `sync-zotero`, `check-citekeys` | Automatique |
| 10. Rédaction | Génération de correctifs de manuscrit en preview + diff | `manuscript-patch-preview` | Automatique |
| 11. Application | Application des correctifs approuvés avec sauvegarde + contrôle SHA + écriture LF | `apply-manuscript-patch` | **Porte humaine** |
| 12. Figures | Figures Graphviz/Mermaid pilotées par spécification, aucune donnée fabriquée | `propose-figures`, `render-figures`, `apply-figure-placeholder` | Sous garde-fous |
| 13. Audit de brouillon | Vérification de tout brouillon (docx/md/qmd) : structure, affirmations sans source, exagérations, chiffres confrontés aux sorties d'expériences réelles | `audit-manuscript-draft` | Automatique |
| 14. Vérification | Garantie qu'aucune automatisation n'a jamais posé `verified=true` | `guard-no-auto-verified`, `guard-paperops-overclaim`, `smoke-test` | Garde automatique / **verdict humain** |

## Pourquoi cela plutôt qu'un chat LLM ?

| Préoccupation | Chat/agent LLM classique | PaperOps |
|---|---|---|
| D'où vient cette phrase ? | inconnu | `paper_id` + `citekey` + citation + page dans la matrice de preuves |
| Exactitude des citations | au mieux | `check-citekeys` contre le BibTeX canonique |
| Édition du manuscrit | écrasement direct | preview → diff → approbation → application contrôlée par SHA → sauvegarde → audit |
| Statut « vérifié » | implicite | seul un humain peut le poser ; les garde-fous l'imposent |
| Chiffres du brouillon | non vérifiés | confrontés aux fichiers de sortie d'expériences réelles |
| Reproductibilité | liée à la session | SQLite + matrices CSV + rapports d'audit + journal + sources des figures |

Les patrons de conception proviennent d'une étude de plus de 40 outils de
recherche open source (PaperQA2, STORM, GPT Researcher, AI-Scientist,
ASReview, gpt_academic, écosystème Zotero, serveurs MCP — voir
`docs/03_TOOL_SYNTHESIS.md`), réassemblés autour d'un principe unique :
**aucune affirmation n'entre dans le manuscrit sans preuve traçable et
revue par un humain.**

## Architecture

![Architecture du système PaperOps](assets/figures/fig_architecture.svg)

| Composant | Rôle | Technologie |
|---|---|---|
| `scripts/paperops.py` | CLI orchestrateur : toutes les étapes et garde-fous | Python, bibliothèque standard d'abord |
| `scripts/paperops_figures.py` | Génération de figures pilotée par spécification (sources toujours conservées) | Graphviz + Mermaid |
| `scripts/paperops_draft_audit.py` | Audit de brouillon : structure, affirmations, recoupement des chiffres | Python |
| `scripts/build_public_release.py` | Export public par liste blanche + détection de secrets | Python |
| Base de données | Métadonnées des articles, scores, états de lecture | SQLite |
| Matrice de preuves | affirmation / citation / page / source / statut de revue / champs de vérification | CSV |
| Manuscrit | Chapitres de thèse, modifiés uniquement via l'application sous garde-fous | Quarto (.qmd) |
| Services externes | Analyse de PDF ; bibliographie canonique | GROBID (Docker), Zotero + Better BibTeX |

Les données circulent à sens unique avec un rapport d'audit à chaque étape
gardée : **API → base → PDF → texte analysé → candidats de preuve → (humain)
→ matrice de preuves → previews → (humain) → manuscrit**, garde-fous
relancés après chaque modification.

![Transitions d'état de vérification des preuves](assets/figures/fig_verification_states.svg)

## Démarrage rapide

```bash
git clone https://github.com/SakJaeLim/paperops.git && cd paperops
python -m venv .venv
# Windows : .venv\Scripts\activate | Unix : source .venv/bin/activate
pip install -r requirements.txt
python scripts/paperops.py init
python scripts/paperops.py status
```

Fonctionne immédiatement sans service externe : collecte, scoring, criblage,
audit de brouillon, garde-fous, sources de figures. Compléments optionnels :

| Dépendance | Permet | Installation |
|---|---|---|
| GROBID | Analyse PDF → texte structuré | `docker run -d -p 8070:8070 lfoppiano/grobid:0.8.0` |
| Zotero + Better BibTeX | Synchronisation bibliographique canonique | zotero.org + plugin Better BibTeX |
| Graphviz | Rendu des figures SVG/PNG | graphviz.org/download |

## Session type

```bash
# Collecte et tri
python scripts/paperops.py collect --limit 20
python scripts/paperops.py score && python scripts/paperops.py screen --limit 80

# Analyse et extraction de preuves
python scripts/paperops.py download-pdfs --limit 10
python scripts/paperops.py parse-grobid --paper-id <id> --apply
python scripts/paperops.py extract-evidence-candidates --paper-id <id> --apply

# Revue humaine, puis promotion sous garde-fous
python scripts/paperops.py review-evidence-candidates --paper-id <id>
python scripts/paperops.py promote-evidence --paper-id <id> --apply

# Rédaction sous garde-fous
python scripts/paperops.py manuscript-patch-preview
python scripts/paperops.py apply-manuscript-patch --from-preview <preview.csv> --dry-run
python scripts/paperops.py apply-manuscript-patch --from-preview <preview.csv> --apply

# Auditer son propre brouillon (docx/md/qmd)
python scripts/paperops.py audit-manuscript-draft --input my_thesis_draft.docx

# Figures et contrôles finaux
python scripts/paperops.py render-figures
python scripts/paperops.py guard-no-auto-verified --promoted-only
python scripts/paperops.py smoke-test
```

![Flux d'application du manuscrit sous garde-fous](assets/figures/fig_guarded_apply.svg)

## L'audit de brouillon en pratique

`audit-manuscript-draft` a été appliqué à un vrai manuscrit KCI
(413 paragraphes) : 378 phrases analysées, structure des chapitres vérifiée,
affirmations fortes sans source et formulations exagérées signalées, et
**les 178 valeurs numériques** du brouillon confrontées aux fichiers de
sortie d'expériences réels — 0 écart, 2 différences d'arrondi expliquées,
1 taux de base recalculé depuis les journaux bruts. L'audit ne modifie
jamais le brouillon et ne marque jamais rien comme vérifié ; il produit
uniquement un rapport (MD + CSV) pour l'auteur.

## Règles de gouvernance

1. La matrice de preuves n'est jamais modifiée à la légère.
2. `verified=true` n'est jamais posé automatiquement — il n'existe aucune
   transition automatisée vers l'état vérifié.
3. La correspondance citation/page est un *alignement de source*, pas une
   validation de vérité.
4. Les modifications du manuscrit passent uniquement par preview/apply sous
   garde-fous, avec sauvegardes et garde + smoke-test après application.
5. Les résultats des travaux connexes sont cités comme patrons de
   conception, jamais comme preuves de performance de PaperOps lui-même.

## Ce que ce dépôt NE contient PAS

Uniquement le code, la configuration, les documents de conception et les
sources de figures générées. Sont délibérément exclus, pour des raisons de
droits d'auteur et parce que votre base de preuves doit être construite à
partir de votre propre littérature : les PDF collectés, les textes intégraux
analysés, les matrices de preuves avec citations et les chapitres personnels
du manuscrit. L'export est en liste blanche, avec détection de secrets avant
chaque publication (`scripts/build_public_release.py`).

## Limites assumées

- L'extraction de preuves repose sur des heuristiques par mots-clés ;
  une extraction assistée par LLM est prévue comme étape gardée distincte.
- L'audit de brouillon est un signalement heuristique destiné à la revue
  humaine, pas une validation de vérité.
- L'alignement citation/page ne valide pas la véracité d'une affirmation —
  par conception.
- Aucune figure de résultats quantitatifs n'est générée sans fichier de
  données réel.

## Licence

MIT — voir [LICENSE](LICENSE).
