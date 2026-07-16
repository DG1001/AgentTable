# AgentTable — Entwicklungshistorie

> Fortlaufendes Logbuch: was wann umgesetzt wurde, welche Entscheidungen mit
> welcher Begründung getroffen wurden. Neueste Einträge oben.
> Fachlich: [fachlich.md](fachlich.md) · Technisch: [technisch.md](technisch.md).

## Konventionen

- Sprache im Code: Englisch (Bezeichner/Kommentare). UI + Agenten: Deutsch.
- Umsetzung phasenweise (spec §9); jede Phase lauffähig + Tests grün.
- Diese drei Docs werden bei jeder relevanten Änderung mitgezogen.

---

## 2026-07-16 — Phasen 0–3 im Grunddurchstich umgesetzt

Ausgangslage: leeres Repo mit `spec.md`, `AGENTS.md`, `README_coder.md`.
Umsetzung von Phase 0 bis Phase 3 in einem Durchgang, Gerüst + Kernlogik + Tests.

### Phase 0 — Gerüst
- Projektstruktur, `app/config.py` (`.env` + `config.toml`, LLM-Rollen),
  `app/db.py` (SQLite, WAL, Mini-Migrationen, Schema v1 nach spec §4),
  `app/repo.py` (gesamtes SQL), `app/service.py` (Gruppen-Bootstrap).
- FastAPI-App (`app/main.py`) mit Token-Login (Cookie/`?t=`), `seed.py`
  (Magic-Links über `VSCODE_PROXY_URI`), `run.py` (Bind `0.0.0.0`, kein Reloader).
- **Entscheidung:** SQLite via `sqlite3` direkt statt SQLAlchemy — kleiner,
  weniger Abhängigkeiten; Migrations-Fähigkeit über eigene `MIGRATIONS`-Liste.
- **Entscheidung:** Ein einzelner geteilter DB-Connection-Handle mit RLock (WAL)
  reicht für „eine Handvoll Gruppen" (spec Nicht-Ziele) und hält es simpel.

### Phase 1 — Privatchat & Onboarding
- WebSocket-Privatchat (`/ws/private`), `app/agents/person.py`:
  Onboarding (3 User-Turns) → Persona-Generierung (ein LLM-Call) → freier Chat
  mit Tool-Calls.
- **Entscheidung:** Onboarding-Ende über Turn-Zähler (nach 3 User-Antworten
  Persona bauen) statt LLM-Selbsteinschätzung — deterministisch und billig.

### Phase 2 — Gruppenraum & Terminfindung (Kern)
- `app/scheduling.py` als **reine** Schnittmengen-Engine (umfassend unit-getestet):
  Slot-Generierung, Kandidaten-Ranking, volle Schnittmenge, Tabellen-Rendering.
- `app/tools.py`: strikte Code-Validierung der Personen-Agent-Tools.
- `app/moderator.py`: Task-Zustandsmaschine + Moderator-Loop mit Speaker-Selection
  und harten Guards (Budget, max-Messages, kein Doppel-Sprecher, 2×`false`→Ende).
- **Entscheidung:** Zustandsübergänge strikt im Code, LLM nur für Text + zwei
  strukturierte Entscheidungen (Sprecherwahl, finale Wahl). Umsetzung von spec §15
  („Agenten rechnen nie selbst").
- **Entscheidung:** Ranking `(-coverage, -weighted, start)` — Abdeckung schlägt
  Präferenz (ein Termin für alle ist wichtiger als starke Wünsche weniger).
- **Entscheidung:** Entscheidung nur unter *voll abgedeckten* Kandidaten gültig;
  bei ungültiger LLM-Antwort deterministischer Fallback auf den besten vollen
  Kandidaten → nie ein Termin, an dem jemand nicht kann.
- **Entscheidung:** Timeout markiert fehlende User als „flexibel" (füllt `maybe`
  über den ganzen Zeitraum), statt sie aus der Teilnehmermenge zu entfernen —
  so bleibt die Abdeckungs-Semantik intakt.
- **Entscheidung:** Laufende Zusammenfassung wird **nicht** per LLM erzeugt,
  sondern günstig als gekappte Sprecher-Snippet-Liste im `agent_state` gehalten
  (Kostenleitplanke §10).

### Phase 3 — Such-Agent
- `app/search_provider.py` (Interface + Tavily/SearXNG/Null),
  `app/agents/search.py`; Einbindung in den Moderator-Loop (Admin darf den
  Such-Agenten für Location-Vorschläge zum Favoriten aufrufen).
- **Entscheidung:** Ohne konfigurierten Provider meldet sich der Such-Agent
  ehrlich als „nicht verfügbar" statt zu scheitern (spec §3).

### Querschnitt
- LLM-Abstraktion (`app/llm/`): Rollen-Config, OpenAI-kompatibler Client mit
  Retry/Backoff/Usage-Logging, `MockLLM` (offline, in Tests skriptbar).
- **Entscheidung:** Ohne API-Key läuft alles gegen `MockLLM` — Tests, CI und eine
  Offline-Demo funktionieren ohne Kosten/Netz. Provider ist über `.env`
  austauschbar (DeepSeek/OpenRouter/Ollama/…).
- Deutsche System-Prompts als Dateien unter `prompts/` (spec §10 Prompt-Hygiene).
- Frontend-SPA (Vanilla JS, kein Build) mit Zwei-Spalten-Layout und Mini-Markdown.

### Verifikation
- `pytest`: 17 Tests grün (scheduling pur, tools-validierung, moderator state
  machine inkl. Happy-Path→`decided` und No-Intersection→`failed`).
- Live-Smoke gegen laufenden Server: `/health`, Token-Login + Cookie, `/api/me`,
  unbekanntes Token abgewiesen, WS-Privatchat (User→Agent), `POST /task/start`
  postet Admin-Ansage in den Raum. Seed erzeugt korrekte Proxy-Magic-Links.

### Realer End-to-End-Lauf (DeepSeek)
- Nach Setzen des `LLM_API_KEY` (DeepSeek) kompletter Durchstich in-process mit
  3 Mitgliedern: Onboarding + Persona, `set_availability`-Tool-Calls, `mark_ready`,
  Verhandlung, Entscheidung → `decided` (Di 21.07. abends, 3/3) mit sinnvoller
  deutscher Begründung. 28 LLM-Calls, ~27k Tokens (im 60er-Budget).
- **Robustheits-Fix (dabei entdeckt):** Das LLM setzte als Slot-Ende `23:59` statt
  des kanonischen `22:00`. Zwei Agenten mit leicht unterschiedlichen Zeiten hätten
  denselben „Abend" nicht als Schnittmenge erkannt. → `tools._snap()` snappt jede
  freie Zeitangabe aufs kanonische Raster (Abend/Halbtag), bevor gemerged wird.
  Zwei neue Tests sichern das ab. **Grund:** Verlässlichkeit der Schnittmenge darf
  nicht von der exakten Uhrzeit abhängen, die das LLM zufällig wählt.

### Dockerfile
- `Dockerfile` (python:3.12-slim, ein Prozess, Volume `/app/data` für SQLite,
  `DB_PATH` gesetzt) + `.dockerignore`. Reloader aus (WS/Task-Loops bleiben aktiv).

### Offen / bewusst verschoben
- **Phase 4 (8-Bit-Visualisierung):** noch nicht umgesetzt — reine Client-View
  auf den `room_message`-Stream, keine Backend-Änderung nötig.
- Periodischer Collecting-Timeout-Check ist implementiert, wird aber nur bei
  Events ausgewertet (kein Hintergrund-Scheduler) — für MVP ausreichend.
- Such-Provider (Tavily/SearXNG) real noch ungetestet (kein Key gesetzt); Pfad
  „nicht verfügbar" verifiziert.
