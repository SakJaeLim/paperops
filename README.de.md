# PaperOps — Evidenzbasierte Forschungs- & Abschlussarbeits-Betriebssystem

<details align="right">
  <summary>🌐 Language Translation / Sprachauswahl</summary>
  <br />
  <p>
    <a href="README.md">🇬🇧 English</a> | 
    <a href="README.ko.md">🇰🇷 한국어</a> | 
    **🇩🇪 Deutsch** | 
    <a href="README.es.md">🇪🇸 Español</a> | 
    <a href="README.zh.md">🇨🇳 中文</a> | 
    <a href="README.ja.md">🇯🇵 日本語</a> | 
    <a href="README.fr.md">🇫🇷 Français</a> | 
    <a href="README.ar.md">🇸🇦 العربية</a>
  </p>
</details>

![End-to-End-Pipeline von PaperOps](assets/figures/fig_pipeline.svg)

PaperOps automatisiert den **gesamten Forschungs- und Schreibprozess** — Literaturerfassung, Relevanzprüfung, PDF-Parsing, Evidenzextraktion, Bibliographie-Synchronisation, kontrollierte Manuskriptbearbeitung, reproduzierbare Grafikerstellung und Entwurfsprüfung — über ein einziges, lokal orientiertes CLI mit **über 45 Befehlen**.

Es ist *kein* automatischer Artikelschreiber. Die Pipeline ist automatisiert, aber drei Entscheidungsschritte sind bewusst dem Menschen vorbehalten: Evidenzübernahme, Freigabe von Manuskriptänderungen und die Einstufung als `verified=true`. Sicherheitsmechanismen (Guards) verhindern, dass diese Schritte gefälscht werden können.

## Der gesamte Lebenszyklus, Schritt für Schritt

| Phase | Beschreibung | Hauptbefehle | Automatisierung |
|---|---|---|---|
| 1. Erfassen | Abrufen von Artikeln aus arXiv / Semantic Scholar / OpenAlex über Themenprofile | `collect`, `digest` | Automatisch |
| 2. Selektieren | Bewerten der Relevanz, Filtern nach Forschungsachsen, Erkennen von Lücken | `score`, `screen`, `gap`, `brief` | Automatisch |
| 3. Sichern | PDFs herunterladen, Literaturkarten und Manuskriptgliederung erstellen | `download-pdfs`, `cards`, `outline` | Automatisch |
| 4. Parsen | PDF → strukturierte Abschnitte/Referenzen via GROBID konvertieren | `parse-grobid`, `validate-grobid-artifacts` | Automatisch |
| 5. Extrahieren | Thesen/Zitate/Seitenzahlen als Evidenzkandidaten aus dem Text extrahieren | `extract-evidence-candidates`, `validate-evidence-candidates` | Automatisch |
| 6. Prüfen | Entscheiden, ob jeder Kandidat übernommen, überarbeitet oder verworfen wird | `review-evidence-candidates`, `promotion-plan` | **Menschliche Freigabe** |
| 7. Befördern | Übertragen freigegebener Evidenzen in die Evidenzmatrix (`verified=false`) | `promote-evidence`, `audit-promoted-evidence` | Kontrolliert |
| 8. Verorten | Genaue PDF-Seitenzahlen für jedes Zitat ermitteln und verknüpfen | `locate-pdf-pages`, `apply-page-metadata` | Kontrolliert |
| 9. Bibliographie | Zitiercodes mit Zotero / Better BibTeX synchronisieren | `sync-zotero`, `check-citekeys` | Automatisch |
| 10. Schreiben | Manuskript-Patches als Vorschau mit Git-Diff erzeugen | `manuscript-patch-preview` | Automatisch |
| 11. Anwenden | Patches mit Backup, SHA-Prüfung und physischem Schreiben anwenden | `apply-manuscript-patch` | **Menschliche Freigabe** |
| 12. Abbildungen | Spezifikationsgestützte Figuren über Graphviz/Mermaid ohne Datenfälschung erstellen | `propose-figures`, `render-figures`, `apply-figure-placeholder` | Kontrolliert |
| 13. Auditieren | Entwürfe (docx/md/qmd) auf Struktur, unbelegte Thesen und Zahlen vs. Experiment-Rohdaten prüfen | `audit-manuscript-draft` | Automatisch |
| 14. Verifizieren | Sicherstellen, dass kein Skript automatisiert `verified=true` setzen kann | `guard-no-auto-verified`, `guard-paperops-overclaim`, `smoke-test` | Automatische Guards / **Menschliche Entscheidung** |

## Warum dieses System statt eines KI-Chats?

| Herausforderung | Klassischer KI-Chat / Agent | PaperOps |
|---|---|---|
| Woher stammt dieser Satz? | Unbekannt | `paper_id` + `citekey` + Zitat + Seite in der Evidenzmatrix |
| Korrektheit der Zitate | Best-Effort | Abgeglichen mit realer Bibliographie via `check-citekeys` |
| Manuskript-Änderungen | Direkte Überschreibung | Vorschau ➔ Diff ➔ Freigabe ➔ SHA-verifizierter Apply ➔ Backup ➔ Post-Audit |
| Status "Verifiziert" | Implizit | Nur durch Menschen setzbar; Guards erzwingen dies |
| Zahlen im Manuskript | Ungeprüft | Abgeglichen mit den Original-Ausgabedateien der Experimente |
| Reproduzierbarkeit | Sitzungsgebunden | SQLite + CSV-Matrizen + Audit-Berichte + Aktivitätsprotokoll + Grafikquellen |

Die Entwurfsmuster wurden aus der Analyse von über 40 Open-Source-Forschungstools (PaperQA2, STORM, GPT Researcher, AI-Scientist, ASReview, gpt_academic, Zotero-Ökosystem, MCP-Server — siehe `docs/03_TOOL_SYNTHESIS.md`) abgeleitet und unter einem Leitprinzip neu zusammengesetzt: **Keine Behauptung gelangt ohne nachvollziehbare, von Menschen geprüfte Belege in das Manuskript.**

## Architektur

![Systemarchitektur von PaperOps](assets/figures/fig_architecture.svg)

| Komponente | Rolle | Technologie |
|---|---|---|
| `scripts/paperops.py` | Haupt-CLI: koordiniert alle Pipeline-Stufen und Guards | Python |
| `scripts/paperops_figures.py` | Spezifikationsgestützte Grafikgenerierung (Quellcode wird immer gesichert) | Graphviz + Mermaid |
| `scripts/paperops_draft_audit.py` | Entwurfsprüfung: Struktur, Belege und Abgleich numerischer Angaben | Python |
| `scripts/build_public_release.py` | Whitelist-basierter öffentlicher Export mit Secret- & PII-Scanner | Python |
| Literatur-DB | Datenbank für Metadaten der gesammelten Artikel und Lesestände | SQLite |
| Evidenzmatrix | These / Originalzitat / Seitenzahl / Fundort / Prüfstatus | CSV |
| Manuskript | Kapitel der Abschlussarbeit, Änderungen nur via kontrolliertem Apply | Quarto (.qmd) |
| Externe Dienste | PDF-Parsing; bibliographische Hauptdatenbank | GROBID (Docker), Zotero + Better BibTeX |

Die Daten fließen in einer einzigen Richtung mit Audit-Berichten bei jedem kontrollierten Schritt:
**APIs → Literatur-DB → PDFs → Text-Parsing → Evidenzkandidaten → (Mensch) → Evidenzmatrix → Patch-Vorschau → (Mensch) → Manuskript**, gefolgt von einer automatischen erneuten Ausführung der Validierungs-Guards.

![Zustandsübergänge der Evidenzverifizierung](assets/figures/fig_verification_states.svg)

## Schnellstart

```bash
git clone https://github.com/SakJaeLim/paperops.git && cd paperops
python -m venv .venv
# Unter Windows: .venv\Scripts\activate | Unter Unix: source .venv/bin/activate
pip install -r requirements.txt
python scripts/paperops.py init
python scripts/paperops.py status
```

Funktioniert sofort ohne externe Dienste für: Erfassung, Bewertung, Selektion, Entwurfsprüfung, Guards und Grafikquellengenerierung. Optionale Erweiterungen:

| Abhängigkeit | Ermöglicht | Installation |
|---|---|---|
| GROBID | PDF-Parsing zu strukturiertem Text | `docker run -d -p 8070:8070 lfoppiano/grobid:0.8.0` |
| Zotero + Better BibTeX | Synchronisation mit Zotero-Bibliothek | zotero.org + Better BibTeX Plugin installieren |
| Graphviz | Rendering von SVG/PNG-Grafiken | Herunterladen über graphviz.org |

## Typischer Ablauf

```bash
# Erfassen und Selektieren
python scripts/paperops.py collect --limit 20
python scripts/paperops.py score && python scripts/paperops.py screen --limit 80

# PDFs parsen und Evidenz extrahieren
python scripts/paperops.py download-pdfs --limit 10
python scripts/paperops.py parse-grobid --paper-id <id> --apply
python scripts/paperops.py extract-evidence-candidates --paper-id <id> --apply

# Menschliche Prüfung und Beförderung
python scripts/paperops.py review-evidence-candidates --paper-id <id>
python scripts/paperops.py promote-evidence --paper-id <id> --apply

# Manuskript kontrolliert aktualisieren
python scripts/paperops.py manuscript-patch-preview
python scripts/paperops.py apply-manuscript-patch --from-preview <preview.csv> --dry-run
python scripts/paperops.py apply-manuscript-patch --from-preview <preview.csv> --apply

# Eigenen Entwurf auditieren (docx/md/qmd)
python scripts/paperops.py audit-manuscript-draft --input mein_entwurf.docx

# Figuren rendern und Abschlussprüfungen
python scripts/paperops.py render-figures
python scripts/paperops.py guard-no-auto-verified --promoted-only
python scripts/paperops.py smoke-test
```

![Workflow des kontrollierten Applys](assets/figures/fig_guarded_apply.svg)

## Praxisbeispiel der Entwurfsprüfung

Der Befehl `audit-manuscript-draft` wurde auf ein echtes Manuskript (413 Absätze) angewendet: Er scannte 378 Sätze, überprüfte die Kapitelstruktur, markierte unbelegte starke Thesen und übertriebene Formulierungen und glich **alle 178 numerischen Werte** im Entwurf mit den tatsächlichen Experiment-Ausgabedateien ab — 0 Diskrepanzen, wobei 2 Rundungsdifferenzen dokumentiert und 1 Basisrate aus den Rohprotokollen neu berechnet wurde.

## Governance-Regeln

1. Die Evidenzmatrix wird niemals unbedacht geändert.
2. `verified=true` wird niemals automatisch gesetzt — es gibt keinen automatisierten Übergang in den verifizierten Zustand.
3. Der Abgleich von Zitat/Seite dient der *Quellenverortung*, nicht der absoluten Wahrheitsprüfung.
4. Manuskript-Änderungen erfolgen ausschließlich über den kontrollierten Preview/Apply-Prozess mit Backups und Post-Apply-Guards.
5. Ergebnisse verwandter Arbeiten werden als Design-Patterns dargestellt, niemals als Leistungsbelege für PaperOps selbst.

## Was dieses Repository NICHT enthält

Ausschließlich Code, Konfigurationen, Design-Dokumente und generierte Grafikquellen. Es schließt gesammelte PDF-Artikel, geparste Volltexte, Evidenzmatrizen mit Rohzitaten und persönliche Manuskriptkapitel bewusst aus — aus Urheberrechtsgründen und weil Ihre Evidenzbasis aus Ihrer eigenen Literaturrecherche aufgebaut werden sollte.

## Ehrliche Einschränkungen

- Die Evidenzextraktion basiert derzeit auf Regeln/Heuristiken; ein LLM-gestützter Extraktor ist als separater, kontrollierter Schritt geplant.
- Die Entwurfsprüfung ist eine heuristische Kennzeichnung für die menschliche Durchsicht, keine absolute Wahrheitsprüfung.
- Die Verortung von Zitat und Seite validiert konstruktionsbedingt nicht den Wahrheitsgehalt einer Behauptung.
- Quantitative Grafiken werden niemals ohne eine reale Datendatei generiert.

## Lizenz

MIT — siehe [LICENSE](LICENSE).
