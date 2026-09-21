<!--
  @authormark v1 -- do not remove (authorship watermark)⁠​​‌‌​​​‌​‌‌​‌​​‌​‌​​‌​‌​​‌​‌​​​​​​‌‌‌​​‌​‌‌‌​‌​‌​‌​​​‌‌‌​‌‌​​​​‌​‌‌​​​‌‌​‌​‌​​‌​​‌​​‌​​‌​​‌‌​‌‌‌​‌‌​​‌‌‌​‌​​‌‌‌​​​‌‌‌​​‌​‌​​‌​‌​​‌‌‌​‌‌​​‌‌‌‌​​‌​‌​‌​​​‌​‌​​​‌‌‌​‌​‌​‌‌​​‌‌​​​‌​⁠
  Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
  Author: https://github.com/Srinivasan-78
  SPDX-License-Identifier: MIT
  Fingerprint: AMK1.1iJP9uGacRI7gN9JvyQGVb
-->
<div align="center">

# repo2graph

**AST-gesteuerte Code-Graphen & abhängigkeitsfreies GraphRAG für KI-Coding-Agenten und Menschen**

<p align="center">
  <a href="../../README.md">English</a> ·
  <a href="README_zh-CN.md">简体中文</a> ·
  <a href="README_ja.md">日本語</a> ·
  <a href="README_fr.md">Français</a> ·
  <a href="README_es.md">Español</a> ·
  <a href="README_de.md">Deutsch</a>
</p>

<p align="center">
  <a href="https://glama.ai/mcp/servers/Srinivasan-78/repo2graph"><img src="https://glama.ai/mcp/servers/Srinivasan-78/repo2graph/badges/score.svg" alt="Glama MCP server score" /></a>
  <a href="https://pypi.org/project/repo2graph/"><img src="https://img.shields.io/pypi/v/repo2graph.svg?color=blue" alt="PyPI version" /></a>
  <a href="https://pypi.org/project/repo2graph/"><img src="https://img.shields.io/pypi/pyversions/repo2graph.svg" alt="Python versions" /></a>
  <a href="../../LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT" /></a>
  <a href="https://github.com/Srinivasan-78/repo2graph/actions/workflows/ci.yml"><img src="https://github.com/Srinivasan-78/repo2graph/actions/workflows/ci.yml/badge.svg" alt="CI status" /></a>
  <img src="https://img.shields.io/badge/MCP-Compatible-purple.svg" alt="MCP Compatible" />
  <a href="https://github.com/Srinivasan-78/repo2graph/stargazers"><img src="https://img.shields.io/github/stars/Srinivasan-78/repo2graph?style=social" alt="GitHub stars" /></a>
</p>

<p align="center">
  <img src="../images/demo.gif" alt="repo2graph baut im Terminal die Karte eines Repositorys auf und beantwortet dann eine Frage dazu" width="850" />
</p>

</div>

<!-- mcp-name: io.github.Srinivasan-78/repo2graph -->

> Diese Seite ist eine Übersetzung der englischen Original-[README.md](../../README.md). Im
> Zweifelsfall gilt die englische Fassung.

---

## ⚡ Was ist repo2graph?

Wenn ein KI-Coding-Agent eine Codebasis mit grep oder einfachem Keyword-Matching durchsucht, kippt
er entweder ganze passende Dateien in den Kontext — was das Token-Budget aufzehrt und jede Struktur
verliert — oder er übersieht die Implementierung völlig, weil die Suchanfrage andere Wörter
verwendet hat als der Code.

**repo2graph** parst Quellcode mit [tree-sitter](https://tree-sitter.github.io/tree-sitter/) zu
einem **abstrakten Syntaxbaum (AST)** und baut daraus einen Graphen echter Code-Beziehungen —
`CALLS` (Aufrufe), `IMPORTS` (Importe), `INHERITS` (Vererbung), `DEFINES` (Definitionen),
`CO_CHANGE` (gemeinsame Änderungen). Dieser Graph wird Agenten entweder direkt über das **Model
Context Protocol (MCP)** bereitgestellt, um die **Aufrufhierarchie von Symbolen** abzufragen, oder
in ein Markdown mit striktem **Token-Limit** gepackt, das jedem LLM übergeben werden kann. Jeder
zurückgegebene Block trägt einen exakten Zitat-Anker `[cite: Pfad:Start-Ende]`, sodass Antworten bis
zur Quelle nachvollziehbar sind — statt einer Umschreibung aus einer Vermutung.

```mermaid
flowchart LR
    A[dein Code] --> B[tree-sitter<br/>liest den Code]
    B --> C[Graph<br/>Knoten + Kanten]
    C --> D[graph.html<br/>das Bild]
    C --> E[chunks.jsonl<br/>Stücke für eine KI]
    C -->|MCP stdio| F[Claude / Cursor /<br/>jeder MCP-Client]
```

Kein Projekt-Setup, kein Language Server, kein Build-Schritt — auf einen Ordner zeigen, fertig.

<p align="center">
  <img src="../images/graph-overview.png" alt="Interaktiver Code-Graph eines von repo2graph kartierten Projekts" width="850" />
</p>

| Interaktive Leinwand, gezoomt | Filter- und Inspektor-Steuerung |
| :---: | :---: |
| <img src="../images/graph-zoom.png" alt="Hineingezoomt in die Karte: benannte Funktionen, Dateien und Bibliotheken, durch Pfeile verbunden" /> | <img src="../images/graph-sidebar.png" alt="Seitenleiste mit Suchfeld, Knotentypen und Beziehungstypen" /> |

`graph.html` ist eine einzelne, in sich geschlossene Datei — kein Server, kein Internet nötig,
Ziehen zum Verschieben, Scrollen zum Zoomen, Klick auf einen Knoten zeigt dessen Code und Nachbarn.

## 🚀 Schnellstart (unter 30 Sekunden)

Python 3.10+ erforderlich. Ausführen über [uv](https://docs.astral.sh/uv/), ohne Installation:

```bash
uvx repo2graph build . -o .r2g && open .r2g/human/graph.html
```

Oder regulär installieren:

```bash
pip install repo2graph
repo2graph build /path/to/project -o .r2g --git-history 200
repo2graph query "how does routing match a path" -o .r2g
```

## 🔌 MCP-Client-Konfiguration

`repo2graph-mcp` ist ein stdio-**MCP-Server**. Er baut seinen eigenen Index beim ersten Aufruf auf,
falls noch keiner existiert — vorher muss nichts ausgeführt werden.

**Claude Code**

```bash
claude mcp add repo2graph -- uvx --from "repo2graph[mcp]" repo2graph-mcp /path/to/project
```

**Claude Desktop** (`claude_desktop_config.json`) und **Cursor** (`.cursor/mcp.json`) — derselbe
Block:

```json
{
  "mcpServers": {
    "repo2graph": {
      "command": "uvx",
      "args": ["--from", "repo2graph[mcp]", "repo2graph-mcp", "/path/to/project"]
    }
  }
}
```

Jeder andere stdio-basierte MCP-Client (Windsurf, Zed, generische Clients) verwendet dasselbe
`command`/`args`-Paar — siehe **[docs/mcp.md](../mcp.md)** (Englisch) für Speicherorte der
Konfigurationsdateien je nach Plattform und Client.

## ✨ Kernfunktionen

| | |
|---|---|
| **Deterministischer Graph statt reiner Embedding-Suche** | Aufrufer, Aufgerufene, Importe und Klassenhierarchien werden aus dem tatsächlichen AST aufgelöst — keine Nächste-Nachbar-Schätzung. |
| **Hybride Suche** | Standardmäßig BM25 + Graph-Nachbarschaftserweiterung; optionale dichte Vektorfusion (`repo2graph embed`) ohne zwingend erforderliche zusätzliche Abhängigkeiten. |
| **Doppelt durchgesetzte Token-Obergrenzen** | Das Budget von `pack_context()` begrenzt das *gesamte* gerenderte Markdown, nicht nur den Chunk-Text — und der MCP-Server begrenzt und misst vor der Rückgabe erneut nach. |
| **15 Sprachen, vollständig unterstützt** | Python, JS/TS/TSX, Go, Rust, Java, Ruby, C, C++, C#, PHP, Kotlin, Swift, Scala, Bash erhalten volle Funktions-/Klassen-/Aufruf-Analyse. Alles andere erscheint trotzdem als Datei auf der Karte. |
| **CI-nativ** | Als GitHub Action veröffentlicht — bei jedem Push einen aktuellen Graphen neben dem Code committen. |
| **Standardmäßig lokal** | `build`, `query`, `rag` und der MCP-Server führen keinerlei Netzwerkaufrufe aus. Die einzige optionale Ausnahme (`rag --answer`) gibt Anbieter und Hostname aus, bevor irgendetwas gesendet wird. |
| **Export in echte Graph-Werkzeuge** | `graph.graphml` (yEd, Gephi, NetworkX) und `graph.cypher` (Neo4j, Memgraph) entstehen bei jedem Build, ohne zusätzlichen Schritt. |

## 🛠️ Bereitgestellte MCP-Tools

| Tool | Argumente | Was zurückkommt |
|---|---|---|
| `repo_map` | keine | Sprachen, zentrale Dateien und wichtigste Einstiegspunkte. Über Aufrufe hinweg stabil — zuerst lesen. |
| `repo_search` | `query`, optional `k` (Standard 8, max. 50), `hops` (Standard 1, max. 4), `budget_tokens` (Standard 6000, max. 12000) | Seed-Chunks plus ihre Graph-Nachbarn, jeder Block mit `[cite: Pfad:Start-Ende]` eingeleitet. |
| `repo_neighbours` | `node_id`, optional `hops` (max. 4), `limit` (Standard 20, max. 50) | Ein Graph-Sprung von einer Symbol-/Datei-/Verzeichnis-ID aus: Aufrufer, Aufgerufene, Basisklassen, definierende Datei. |

Geheimnisse werden bei jedem Tool-Aufruf bedingungslos ausgeschlossen — kein Flag schaltet das ab.
Vollständiger Vertrag inklusive der beiden Diagnose-Tools (`repo_cache_stats`, `repo_build_status`)
für langlaufende Server-Deployments: **[docs/mcp.md](../mcp.md)** (Englisch).

## 📐 Architektur & Token-Ökonomie

- **Knoten**: `repo`, `dir`, `file`, `symbol` (Funktion/Methode/Klasse/Struct/Trait/Interface/Typ),
  `module` (externe Abhängigkeit), `external` (ein nicht auflösbares Aufrufziel).
- **Kanten**: `CONTAINS`, `DEFINES`, `IMPORTS`, `CALLS` (trägt `count` + `confidence`),
  `CALLS_EXTERNAL`, `INHERITS`, `CO_CHANGE` (aus `--git-history`, erfordert 3+ gemeinsame
  Änderungen).
- **Die Aufrufauflösung erfolgt namensbasiert, nicht typbasiert** — ein bewusster Kompromiss, der
  repo2graph sprachunabhängig und ohne Konfiguration hält. Mehrdeutige Aufrufe fächern sich in bis
  zu 5 Kandidatenkanten mit `confidence = 1/n` auf; filtere auf `confidence == 1.0`, wenn du
  Gewissheit statt Vollständigkeit brauchst.
- **Zwei Budgetmodelle, bewusst getrennt**: `budget_chars` von `Index.retrieve()` begrenzt nur den
  eigenen Text der Chunks (eine Rückwärtskompatibilitäts-Schnittstelle); `budget_chars` von
  `Index.pack_context()` begrenzt das *gesamte* gerenderte Markdown — Zitat-Header, Trennzeichen,
  alles. Neuer Retrieval-Code sollte auf `pack_context()` aufbauen.
- **Chunking**: etwa ein Chunk pro Funktion/Klasse, bei ~4000 Zeichen geschnitten, mit 8 Zeilen
  Überlappung, damit an der Naht nichts verloren geht; der Header jedes Chunks nennt seine Aufrufer
  und Aufgerufenen — das macht graph-erweitertes Retrieval besser als eine einfache Top-k-Textsuche.

Vollständige Aufschlüsselung jeder Knoten-/Kantenart und des Chunk-Schemas:
**[docs/reference.md](../reference.md)** (Englisch). Die vollständige Pipeline, die Python-API, und
wo der Graph rät (und warum): **[TECHNICAL.md](../../TECHNICAL.md)** (Englisch).

## 📊 Im Einsatz an echten Repositories

Keine Spielzeug-Demo — fünf echte, große, öffentliche Repositories, jedes an einem fixierten Commit
indexiert, mit dem generierten Graphen im Repo und dem exakten Reproduktionsbefehl festgehalten.
Jede Zahl ist gemessen, aus [`benchmarks/results.json`](../../benchmarks/results.json), nicht
geschätzt.

| Repository | Sprache(n) | Umfang | Knoten | Kanten |
|---|---|---|---:|---:|
| [Kubernetes](https://github.com/kubernetes/kubernetes) | Go | eingegrenzt (Controller, Scheduler, API-Server) | 14.197 | 83.525 |
| [TensorFlow](https://github.com/tensorflow/tensorflow) | C++ / Python | eingegrenzt (Python/C++-Grenze) | 20.641 | 96.013 |
| [Django](https://github.com/django/django) | Python | vollständiges Repository | 54.544 | 228.461 |
| [VS Code](https://github.com/microsoft/vscode) | TypeScript | eingegrenzt (`src/vs/`) | 113.115 | 431.453 |
| [Linux-Kernel](https://github.com/torvalds/linux) | C | eingegrenzt (extreme Größenordnung) | 136.182 | 257.655 |

Siehe **[examples/README.md](../../examples/README.md)** für den vollständigen Index und die
Reproduktionsbefehle, **[docs/benchmarks.md](../benchmarks.md)** für die Methodik, und
**[docs/limitations.md](../limitations.md)** dafür, was der Lauf gegen fünf echte Repositories
tatsächlich zutage gefördert hat (Parse-Fehlerraten bei makro-lastigem C/C++, Mehrdeutigkeit von
Aufrufnamen, Grenzen der sprachübergreifenden Auflösung) — alles auf Englisch.

## 📖 CLI- & Server-Referenz

| Befehl | Macht |
|---|---|
| `repo2graph build <path> -o .r2g [--git-history N]` | Parst ein lokales Repository zu einem Graphen + Chunks. |
| `repo2graph github <owner/repo> -o <dir>` | Holt, baut und räumt auf — kein lokaler Clone nötig. |
| `repo2graph query "<question>" -o .r2g` | Lexikalische Suche + Graph-Erweiterung um einen Sprung. |
| `repo2graph rag "<question>" -o .r2g [--vectors] [--answer]` | Budgetbegrenztes GraphRAG-Paket; `--answer` sendet es an ein LLM (optional, Netzwerk). |
| `repo2graph embed -o .r2g [--verify-rag]` | Berechnet/prüft dichte Vektoren für hybride Suche. |
| `repo2graph map -o .r2g [--viz-nodes N]` | Erzeugt `graph.html` mit anderem Knoten-Limit neu. |
| `repo2graph stats -o .r2g` | Knoten-/Kanten-/Funktionszahlen für einen bestehenden Index. |
| `repo2graph-mcp <path> [--no-auto-build] [--async-build]` | stdio-MCP-Server über `.r2g`. |

**Umgebungsvariablen** (nur von `rag --answer` gelesen, in dieser Prioritätsreihenfolge):
`GEMINI_API_KEY` → `OPENAI_API_KEY` → `ANTHROPIC_API_KEY` → `OLLAMA_HOST`. Kein anderer Befehl
macht einen Netzwerkaufruf oder liest diese Variablen. Vollständige Flag-Tabellen und
Budgetberechnung: **[docs/cli.md](../cli.md)** (Englisch).

## 🔐 Sicherheit

`build`, `query`, `rag` und der MCP-Server führen keinerlei Netzwerkaufrufe aus. `rag --answer` ist
die einzige optionale Ausnahme — sie sendet das zusammengestellte Paket an einen LLM-Anbieter und
gibt vorher Anbieter und Hostname aus. Der MCP-Server schließt Dateien, die wie Zugangsdaten
aussehen, bedingungslos aus, ohne Flag zum Abschalten. Details: **[SECURITY.md](../../SECURITY.md)**
(Englisch).

## 🤝 Mitwirken & Community

```bash
git clone https://github.com/Srinivasan-78/repo2graph
cd repo2graph
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
make lint test   # oder: ruff check . && pytest
```

- **[.github/CONTRIBUTING.md](../../.github/CONTRIBUTING.md)** (Englisch) — vollständiges lokales
  Setup, Code-Stil und der Registry-/Glama-Veröffentlichungsprozess.
- **[docs/BACKLOG.md](../BACKLOG.md)** (Englisch) — bewusst zurückgestellte Arbeit und warum; kommt
  einer Roadmap am nächsten, inklusive eines Abschnitts „Gute erste Issues".
- **[AGENTS.md](../../AGENTS.md)** (Englisch) — die nicht offensichtlichen Konventionen dieser
  Codebasis (Windows-Encoding, Text-Slicing, die zwei Budgetmodelle), bevor du `repo2graph/`
  bearbeitest.
- **[CODE_OF_CONDUCT.md](../../CODE_OF_CONDUCT.md)** (Englisch) — Contributor Covenant v2.1.
- Bug gefunden oder eine Idee für ein Feature?
  [Issue eröffnen](https://github.com/Srinivasan-78/repo2graph/issues/new/choose).

## Lizenz

MIT. Siehe **[LICENSE](../../LICENSE)**.

---

<div align="center">

repo2graph nützlich gefunden? [Gib dem Repo einen Stern](https://github.com/Srinivasan-78/repo2graph)
— das ist der einfachste Weg, anderen zu helfen, es zu finden.

</div>
