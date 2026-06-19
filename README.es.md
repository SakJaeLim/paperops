# PaperOps — Sistema Operativo de Investigación y Redacción de Tesis Centrado en la Evidencia

<details align="right">
  <summary>🌐 Language Translation / Selección de Idioma</summary>
  <br />
  <p>
    <a href="README.md">🇬🇧 English</a> | 
    <a href="README.ko.md">🇰🇷 한국어</a> | 
    <a href="README.de.md">🇩🇪 Deutsch</a> | 
    **🇪🇸 Español** | 
    <a href="README.zh.md">🇨🇳 中文</a> | 
    <a href="README.ja.md">🇯🇵 日本語</a> | 
    <a href="README.fr.md">🇫🇷 Français</a> | 
    <a href="README.ar.md">🇸🇦 العربية</a>
  </p>
</details>

![Pipeline de extremo a extremo de PaperOps](assets/figures/fig_pipeline.svg)

PaperOps automatiza el **ciclo de vida completo de la investigación y la redacción** (recopilación de literatura, filtrado, procesamiento de PDF, extracción de evidencia, sincronización bibliográfica, edición controlada del borrador, generación reproducible de figuras y auditoría de borradores) a través de una única CLI local-first con **más de 45 comandos**.

No es un redactor automático de artículos. El flujo de trabajo está automatizado, pero tres puntos de decisión críticos están reservados deliberadamente para los humanos: la adopción de evidencia, la aprobación de cambios en el borrador y la decisión de marcar algo como `verified=true`. Los mecanismos de control (guards) hacen imposible que cualquier paso automatizado falsifique estas decisiones.

## El ciclo de vida completo, paso a paso

| Etapa | Descripción | Comandos clave | Automatización |
|---|---|---|---|
| 1. Recopilar | Obtener artículos de arXiv / Semantic Scholar / OpenAlex mediante perfiles temáticos | `collect`, `digest` | Automático |
| 2. Clasificar | Evaluar la relevancia, filtrar por ejes de investigación y detectar brechas | `score`, `screen`, `gap`, `brief` | Automático |
| 3. Adquirir | Descargar archivos PDF, construir fichas de lectura y esquemas | `download-pdfs`, `cards`, `outline` | Automático |
| 4. Procesar | Convertir PDF a secciones/referencias estructuradas a través de GROBID | `parse-grobid`, `validate-grobid-artifacts` | Automático |
| 5. Extraer | Extraer propuestas de evidencia (afirmación/cita/página) del texto procesado | `extract-evidence-candidates`, `validate-evidence-candidates` | Automático |
| 6. Revisar | Decidir si aceptar, modificar o rechazar cada candidato | `review-evidence-candidates`, `promotion-plan` | **Filtro Humano** |
| 7. Promover | Mover la evidencia aprobada a la Matriz de Evidencia (`verified=false`) | `promote-evidence`, `audit-promoted-evidence` | Controlado |
| 8. Localizar | Encontrar y asociar las páginas exactas del PDF para cada cita | `locate-pdf-pages`, `apply-page-metadata` | Controlado |
| 9. Bibliografía | Sincronizar identificadores de cita con la bibliografía de Zotero / Better BibTeX | `sync-zotero`, `check-citekeys` | Automático |
| 10. Escribir | Generar parches para el manuscrito con vista previa y diferencias (diff) | `manuscript-patch-preview` | Automático |
| 11. Aplicar | Aplicar los parches aprobados con copia de seguridad, verificación SHA y escritura física | `apply-manuscript-patch` | **Filtro Humano** |
| 12. Figuras | Crear figuras de Graphviz/Mermaid basadas en especificaciones, sin datos inventados | `propose-figures`, `render-figures`, `apply-figure-placeholder` | Controlado |
| 13. Auditar | Evaluar cualquier borrador (docx/md/qmd): estructura, afirmaciones sin fuente, exageraciones y números vs. resultados reales | `audit-manuscript-draft` | Automático |
| 14. Verificar | Garantizar que ningún proceso automático establezca `verified=true` | `guard-no-auto-verified`, `guard-paperops-overclaim`, `smoke-test` | Control automático / **Veredicto humano** |

## ¿Por qué usar esto en lugar de un chat de IA convencional?

| Factor | Chat de IA / Agente convencional | PaperOps |
|---|---|---|
| ¿De dónde viene esta frase? | Desconocido | `paper_id` + `citekey` + cita + página en la Matriz de Evidencia |
| Precisión de citas | Basada en el mejor esfuerzo | Contrastada mediante `check-citekeys` contra la bibliografía real |
| Modificaciones del manuscrito | Sobrescritura directa | Vista previa ➔ diff ➔ aprobación ➔ aplicación verificada por SHA ➔ backup ➔ post-auditoría |
| Estado "Verificado" | Implícito | Solo un ser humano puede establecerlo; los controles lo garantizan |
| Números en el borrador | Sin verificar | Contrastados con los archivos reales de salida de los experimentos |
| Reproducibilidad | Limitada a la sesión | SQLite + Matrices CSV + informes de auditoría + registro de actividad + fuentes de figuras |

Los patrones de diseño se sintetizaron a partir de un análisis de más de 40 herramientas de investigación de código abierto (PaperQA2, STORM, GPT Researcher, AI-Scientist, ASReview, gpt_academic, ecosistema Zotero, servidores MCP — ver `docs/03_TOOL_SYNTHESIS.md`), reensamblados bajo un principio único: **ninguna afirmación entra al manuscrito sin evidencia trazable y revisada por un humano.**

## Arquitectura

![Arquitectura del sistema PaperOps](assets/figures/fig_architecture.svg)

| Componente | Rol | Tecnología |
|---|---|---|
| `scripts/paperops.py` | CLI Orquestador: gestiona todas las etapas y controles del flujo | Python |
| `scripts/paperops_figures.py` | Generación de figuras basada en especificaciones (guardando siempre las fuentes) | Graphviz + Mermaid |
| `scripts/paperops_draft_audit.py` | Auditoría de borradores: estructura, afirmaciones y validación cruzada de números | Python |
| `scripts/build_public_release.py` | Exportación pública basada en listas blancas con escaneo de secretos y PII | Python |
| BD de Artículos | Base de datos con metadatos de artículos, puntuaciones y estados de lectura | SQLite |
| Matriz de Evidencia | Archivo con afirmaciones, citas, páginas, ubicación de fuente y estados de revisión | CSV |
| Manuscrito | Capítulos de la tesis, modificados únicamente a través de la aplicación controlada | Quarto (.qmd) |
| Servicios externos | Procesamiento de PDF; gestión de bibliografía de origen | GROBID (Docker), Zotero + Better BibTeX |

Los datos fluyen en una sola dirección con informes de auditoría en cada paso controlado:
**APIs → base de datos → PDFs → texto procesado → candidatos a evidencia → (humano) → Matriz de Evidencia → vista previa de parches → (humano) → borrador**, ejecutando controles de validación en cada cambio.

![Transiciones de estado de verificación de evidencia](assets/figures/fig_verification_states.svg)

## Inicio rápido

```bash
git clone https://github.com/SakJaeLim/paperops.git && cd paperops
python -m venv .venv
# En Windows: .venv\Scripts\activate | En Unix: source .venv/bin/activate
pip install -r requirements.txt
python scripts/paperops.py init
python scripts/paperops.py status
```

Funciona de inmediato sin servicios externos para: recopilación, puntuación, filtrado, auditoría de borradores, controles de seguridad y generación de figuras. Adiciones opcionales:

| Dependencia | Función | Instalación |
|---|---|---|
| GROBID | Procesamiento de PDF a texto estructurado | `docker run -d -p 8070:8070 lfoppiano/grobid:0.8.0` |
| Zotero + Better BibTeX | Sincronización bibliográfica real | Descargar de zotero.org e instalar Better BibTeX |
| Graphviz | Renderizado de figuras SVG/PNG | Descargar e instalar de graphviz.org |

## Sesión típica

```bash
# Recopilar y clasificar
python scripts/paperops.py collect --limit 20
python scripts/paperops.py score && python scripts/paperops.py screen --limit 80

# Procesar PDF y extraer evidencia
python scripts/paperops.py download-pdfs --limit 10
python scripts/paperops.py parse-grobid --paper-id <id> --apply
python scripts/paperops.py extract-evidence-candidates --paper-id <id> --apply

# Revisión humana y promoción controlada
python scripts/paperops.py review-evidence-candidates --paper-id <id>
python scripts/paperops.py promote-evidence --paper-id <id> --apply

# Escritura controlada del borrador
python scripts/paperops.py manuscript-patch-preview
python scripts/paperops.py apply-manuscript-patch --from-preview <preview.csv> --dry-run
python scripts/paperops.py apply-manuscript-patch --from-preview <preview.csv> --apply

# Auditar tu propio borrador (docx/md/qmd)
python scripts/paperops.py audit-manuscript-draft --input mi_borrador_de_tesis.docx

# Renderizar figuras y verificaciones finales
python scripts/paperops.py render-figures
python scripts/paperops.py guard-no-auto-verified --promoted-only
python scripts/paperops.py smoke-test
```

![Flujo de aplicación controlada al borrador](assets/figures/fig_guarded_apply.svg)

## La auditoría en la práctica

El comando `audit-manuscript-draft` se utilizó en un borrador de tesis real (413 párrafos): escaneó 378 oraciones, verificó la estructura del capítulo, señaló afirmaciones fuertes sin fuente y lenguaje exagerado, y realizó una validación cruzada de **los 178 valores numéricos** del borrador contra los archivos reales de salida de los experimentos — 0 discrepancias, con 2 diferencias de redondeo explicadas y 1 tasa base recalculada a partir de los registros de predicción.

## Reglas de gobernanza

1. La Matriz de Evidencia nunca se modifica de forma casual.
2. `verified=true` nunca se establece automáticamente: no existe una transición automatizada hacia el estado verificado.
3. La alineación de cita/página es una comprobación de correspondencia de fuentes, no una validación de la verdad absoluta.
4. Las modificaciones al manuscrito ocurren únicamente a través de la aplicación controlada con copias de seguridad e informes posteriores.
5. Los hallazgos de trabajos relacionados se presentan como patrones de diseño, nunca como evidencia de rendimiento para el propio PaperOps.

## Lo que este repositorio NO incluye

Código, configuraciones, documentos de diseño y fuentes de figuras únicamente. Excluye deliberadamente los PDFs de artículos recopilados, los textos procesados completos, las matrices de evidencia con citas textuales y los capítulos del manuscrito personal, por razones de derechos de autor y porque cada base de evidencia debe construirse a partir de la propia literatura del investigador.

## Limitaciones honestas

- La extracción de evidencia se basa actualmente en reglas/heurísticas; un extractor asistido por LLM es una etapa planificada por separado.
- La auditoría de borradores es un filtrado heurístico para revisión humana, no una validación de la verdad.
- El acoplamiento de citas y páginas no valida la veracidad de una afirmación, por diseño.
- Las figuras con resultados cuantitativos nunca se generan sin un archivo de datos real que las respalde.

## Licencia

MIT — ver [LICENSE](LICENSE).
