# AgentTable — Technische Dokumentation

> Alles Technische: Architektur, Module, Datenmodell, Betrieb.
> Fachliche Sicht: [fachlich.md](fachlich.md) · Verlauf: [entwicklung.md](entwicklung.md).

## 1. Tech-Stack

- **Backend:** Python 3.12+ (getestet 3.13), FastAPI, Uvicorn — ein Prozess.
- **Persistenz:** SQLite über `sqlite3` direkt (kein ORM), WAL-Modus, eigene
  Mini-Migrationen (`app/db.py`).
- **Realtime:** FastAPI-native WebSockets (Privatchat + Gruppenraum).
- **Frontend:** statisches Vanilla-JS-SPA (`frontend/`), kein Build-Step, kein npm.
- **LLM:** OpenAI-kompatibler Client (`openai`-SDK mit `base_url`), Default
  DeepSeek. Ohne API-Key automatisch deterministischer `MockLLM`.
- **Web-Suche:** Provider-Interface (Tavily / SearXNG / None).
- **Tests:** pytest + pytest-asyncio; Kernlogik LLM-frei testbar.

## 2. Projektstruktur

```
app/
  config.py         # .env + config.toml -> settings, LLM-Rollen
  db.py             # SQLite-Handle, Schema-Migrationen
  repo.py           # sämtliche SQL (Repository-Funktionen)
  service.py        # Gruppen-Bootstrap (Agenten anlegen)
  scheduling.py     # PURE: Slot-Generierung + Schnittmengen-Engine  ← Kern
  tools.py          # Function-Calling der Personen-Agenten + Validierung
  moderator.py      # Task-Zustandsmaschine + Moderator-Loop          ← Kern
  serialize.py      # Row -> JSON (REST/WS-Payloads), Agentenfarben
  realtime.py       # WebSocket-Registry + Broadcast
  prompts.py        # Laden/Rendern der Prompt-Templates
  main.py           # FastAPI-App: REST, WS, Token-Login, Static
  llm/
    client.py       # LLMClient-Protocol, OpenAICompatibleClient, MockLLM, Factory
    parsing.py      # robustes JSON-Parsing + Usage-Logging
  agents/
    person.py       # Onboarding, Persona, Privatchat, Raum-Beitrag
    search.py       # Such-Agent (web_search -> Zusammenfassung)
    context.py      # Prompt-Kontext-Builder (geteilt)
prompts/            # deutsche System-Prompts (versionierbar)
frontend/           # index.html, app.js, style.css
tests/              # scheduling, tools, moderator (state machine)
seed.py             # Gruppe + N User + Magic-Links
run.py              # Uvicorn-Entrypoint (0.0.0.0)
config.toml         # statische Defaults
.env.example        # Konfigurationsvorlage
```

## 3. Datenmodell (SQLite)

Tabellen (siehe `MIGRATIONS` in `app/db.py`), Schema-Version 1:

- `group(id, name, created_at)`
- `user(id, group_id, token UNIQUE, display_name, persona, created_at, last_seen_at)`
- `agent(id, group_id, kind['person'|'admin'|'search'], user_id?, name, persona?)`
- `private_message(id, user_id, role['user'|'agent'|'system'], content, created_at)`
- `room_message(id, group_id, agent_id?, kind['agent'|'system'], content, created_at)`
- `availability(id, user_id, slot_start, slot_end, preference['yes'|'maybe'|'preferred'], note?, updated_at)`
  — **Single Source of Truth** für Termine, nur via Tool-Calls beschrieben.
- `agent_state(agent_id, key, value_json)` — z. B. `onboarding_done`, `ready:<task_id>`, `summary:<task_id>`.
- `task(id, group_id, type, status['collecting'|'negotiating'|'decided'|'failed'], params_json, result_json?, iteration, created_at, decided_at)`
- `llm_usage(id, group_id?, task_id?, role, model, prompt_tokens, completion_tokens, created_at)` — Kostenzähler.

**Slot-Zeiten** sind naive ISO-Strings `YYYY-MM-DDTHH:MM` (Europe/Berlin
Wall-Clock). **Timestamps** aus `datetime('now')` sind UTC.

## 4. Scheduling-Engine (`app/scheduling.py`) — reine Funktionen

Kein DB-/LLM-/Uhr-Zugriff, vollständig unit-getestet.

- `generate_slots(range_start, range_end, granularity)` → Liste diskreter `Slot`s.
- `compute_candidates(availability, participant_ids, top_n)` → gerankte `Candidate`s.
  - Gruppierung über `(slot_start, slot_end)`; Duplikate je User behalten die
    stärkste Präferenz.
  - Sortierschlüssel: `(-coverage, -weighted, start)` mit Gewichten
    `preferred=3, yes=2, maybe=1`.
- `has_full_intersection(candidates)` → gibt es einen Termin für alle?
- `find_candidate(...)` → Validierung der Admin-Entscheidung.
- `render_candidate_table(...)` → Markdown-Tabelle für den Raum.

## 5. Task-Zustandsmaschine & Moderator-Loop (`app/moderator.py`)

Zustandsübergänge macht **Code**, das Admin-LLM formuliert nur Text und trifft
zwei strukturierte Entscheidungen (Sprecherwahl, finale Wahl).

```
collecting ──(alle ready | Timeout)──► negotiating ──► decided
     ▲                                      │
     └────(keine Schnittmenge, <2 Iter.)────┘ ──(≥2 Iter.)──► failed
```

**Harte Guards im Code (spec §5.2, §10):**
- Budget pro Verhandlung (`budget_ok`) → bei Überschreitung `_pause_over_budget`.
  Gezählt werden nur Calls **seit Verhandlungsstart** (`neg_base:<task_id>`),
  damit die user-getriebene Sammelphase (Onboarding, ask_*) das autonome
  Verhandlungsbudget nicht aufbraucht.
- `max_messages_per_round` begrenzt eine Verhandlungsrunde.
- Kein Agent zweimal direkt hintereinander (`_pick_fallback`).
- Zwei aufeinanderfolgende `progress=false` → Runde endet.

**Sprecherwahl** (`_select_speaker`): Admin-LLM-Call mit `response_format=json`
→ `{next_speaker, progress, reason}`, robust geparst.

**Entscheidung** (`_decide_and_finalize`): Admin-LLM liefert `candidate_index`;
Code validiert, dass der Kandidat existiert **und volle Abdeckung** hat, sonst
deterministischer Fallback auf den besten vollen Kandidaten.

**Robustheit:** Alles wird erst persistiert, dann gebroadcastet; Zustand liegt in
der DB → Wiederaufnahme nach Neustart möglich.

## 6. LLM-Abstraktion (`app/llm/`)

- `LLMClient`-Protocol: `chat(messages, tools?, response_format?) -> LLMResponse`.
- Konfiguration **pro Rolle** (`person`, `admin`, `search`) aus `.env`/`config.toml`:
  Modell, Base-URL, API-Key, Temperatur. Admin kann ein stärkeres Modell nutzen.
- `OpenAICompatibleClient`: Retry mit Backoff, Timeout, Tool-Calls, JSON-Mode.
- `MockLLM`: deterministisch, offline; per `handler`/`queue` skriptbar (Tests).
- `call_and_log(...)`: führt den Call aus und schreibt Token-Usage in `llm_usage`.
- Factory `get_client(role)` wählt real vs. mock anhand des API-Keys;
  `set_client_factory` überschreibt sie in Tests.

## 7. Personen-Agent-Tools (`app/tools.py`)

### Such-Provider

`SearxngProvider` (Default in diesem Workspace) ruft den Container unter
`SEARXNG_BASE_URL` auf: `GET {base}/search?q=<begriff>&format=json` und mappt
`results[]` auf `SearchResult(title, url, snippet)`. Ohne erreichbaren Provider
meldet sich der Such-Agent ehrlich als „nicht verfügbar".

## 7b. Personen-Agent-Tools (`app/tools.py`)

Function-Calling-Schemas (deutsch beschrieben), Validierung strikt in Code:
- `set_availability(slots[])` — **ersetzt** alle Slots; prüft Zeitraum, `end>start`,
  Präferenz-Enum; snappt aufs Raster; merged Duplikate. Fehler → deutsche
  Fehlermeldung als Tool-Result zurück an den Agenten.
- `add_note(text)` — weiche Präferenz.
- `mark_ready()` — setzt `ready:<task_id>` und triggert `check_and_advance`.
  **Guard:** wird verweigert, solange der User keinen Slot hat (verhindert den
  No-Intersection-Reset durch „bereit ohne Verfügbarkeit").
- `ask_admin(request)` — stößt auf Userwunsch den Organisator an
  (`moderator.handle_admin_request`): Orga postet den Stand (wer fehlt) oder
  startet die Verhandlung. Frage + Antwort erscheinen im Raum.
- `ask_search(query)` — stellt dem Such-Agenten auf Userwunsch eine Zwischenfrage;
  Antwort im Raum + im Privatchat.
- `ask_agent(agent_name, question)` — fragt auf Userwunsch den Agenten einer
  anderen Person (Ziel per Name/Anzeigename aufgelöst); postet Frage + eine
  gestützte Antwort (`person.answer_question`) im Raum. **Bewusst gedrosselt**:
  eine Frage → eine Antwort, kein autonomer Agent-Loop (§10).
- Die raumseitigen Tools (`ask_admin`/`ask_search`/`ask_agent`) werden in
  `person.py` async behandelt (nicht in `apply_tool_call`).

## 8. HTTP-/WS-API (`app/main.py`)

REST (unter `/api`):
- `GET /me` (inkl. `ready`-Flag), `GET /private/history`, `GET /room/history`,
  `GET /task`, `POST /task/start`.
- `POST /ready` — markiert den User deterministisch als bereit (Guard: ≥1 Slot),
  triggert `check_and_advance`. Umgeht die Unzuverlässigkeit des LLM-`mark_ready`.
- Auth: Token als `?t=` oder Cookie `at_token`.

WebSockets:
- `GET /ws/private?t=…` — bidirektional (User schreibt, Agent antwortet).
- `GET /ws/room?t=…` — für Menschen read-only (nur Empfang).

Static & Login: `GET /` liefert das SPA und setzt bei gültigem `?t=` das Cookie;
unbekanntes Token → 404-Fehlerseite. `GET /health` für Checks.

Optionaler `BASE_PATH` mountet die App unter einem Sub-Pfad (nginx).

## 9. Frontend (`frontend/`)

Vanilla JS, kein Build. Zwei-Spalten-Layout: links Privatchat, rechts Task-Panel
+ Gruppenraum + 8-Bit-Tischansicht. Mini-Markdown-Renderer (Bold/Italic/Pipe-
Tabellen) für die Kandidatentabelle. Reconnutende WebSockets. Agentenfarben aus
dem Backend.

- `app.js` — Boot, REST-Load, WebSockets, Chat-Composer, Start-Dialog.
- `viz.js` — **8-Bit-Visualisierung (Phase 4)**, gekapselt als `AgentViz`
  (`init(canvas, agents)`, `speak(agentId)`, `setAgents(...)`). Canvas-2D-Renderer
  mit prozeduralen Pixel-Sprites (Blöcke, begrenzte Palette), Tisch, Namensschildern,
  Blinzeln und Sprech-Animation. **Rein clientseitig, keine Backend-Änderung** —
  `app.js` ruft `AgentViz.speak(msg.agent_id)` bei jeder `room_message` (kind≠system).
  Die Animation läuft `TALK_MS` (2,6 s) und stoppt dann automatisch.

## 10. Betrieb / Deployment

**Lokal / im XaresAICoder-Workspace** (siehe `AGENTS.md` — Bind an `0.0.0.0`):
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # optional: LLM_API_KEY setzen
python seed.py --members Alex,Bea,Chris,Dana --port 8000   # Magic-Links
python run.py                 # http://0.0.0.0:8000
```
`run.py` bindet `0.0.0.0` und schaltet den Reloader **aus** (hält WS-Verbindungen
und In-Memory-Task-Loops am Leben). Externe URL via `VSCODE_PROXY_URI`.

**Docker** (ein Volume für SQLite): siehe `Dockerfile`. Hinter nginx den
WebSocket-Upgrade durchreichen.

## 11. Tests

```bash
pytest            # 17 Tests: scheduling (pure), tools (validierung), moderator (state machine)
```
Der Moderator-Loop wird ohne Netz gegen einen skriptbaren Mock-LLM getestet
(`tests/conftest.py`), inkl. Happy-Path bis `decided` und No-Intersection-Fallback
bis `failed`.

## 12. Konfiguration (Auszug)

| Variable | Default | Zweck |
|---|---|---|
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | – / DeepSeek / deepseek-chat | LLM-Zugang |
| `LLM_{PERSON,ADMIN,SEARCH}_MODEL` | (Fallback auf `LLM_MODEL`) | Rollen-Override |
| `SEARCH_PROVIDER` | `searxng` | `tavily` / `searxng` / `none` |
| `SEARXNG_BASE_URL` | `http://searxng:8080` | SearXNG-Container (JSON: `/search?q=…&format=json`) |
| `TASK_LLM_BUDGET` | 60 | harte Call-Obergrenze pro Task |
| `DB_PATH` | `data/agenttable.db` | SQLite-Datei |
| `BASE_PATH` | (leer) | Sub-Pfad hinter Proxy |
| `config.toml [app]` | `slot_granularity`, `collecting_timeout_hours`, `max_messages_per_round`, `candidate_count`, `max_reschedule_iterations` | Ablauf-Parameter |
