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

**Provider-Kette:** Bei `SEARCH_PROVIDER=tavily` (+ Key + `SEARXNG_BASE_URL`) baut
`get_provider` einen `FallbackProvider(Tavily, SearXNG)` — Tavily primär, SearXNG
automatisch als Fallback bei Fehler/leer/Quota. Ohne Tavily-Key → SearXNG allein.

## 7. Personen-Agent-Tools (`app/tools.py`)

### Such-Provider

`SearxngProvider` (Default in diesem Workspace) ruft den Container unter
`SEARXNG_BASE_URL` auf: `GET {base}/search?q=<begriff>&format=json` und mappt
`results[]` auf `SearchResult(title, url, snippet)`. Bei 0 Treffern **mit**
`unresponsive_engines` (Rate-Limit/CAPTCHA) wirft er `SearchUnavailable` —
unterscheidet also echte Fehlanzeige von blockierten Engines.

**Robustheit der Suche (dreistufig):** 1) normale Web-Suche; 2) bei Block/leer
Retry mit `&engines=openstreetmap,wikipedia` (CAPTCHA-frei, ideal für Locations —
`SearchProvider.search(engines=…)`); 3) sonst **Wissens-Fallback** aus Modellwissen
mit klarem „ohne Live-Recherche"-Hinweis (`prompts/search_fallback.md`).

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
- `start_smalltalk(topic?)` — Gimmick: startet `moderator._run_smalltalk`, einen
  Hintergrund-Loop mit **zufälliger** Sprecherwahl (No-Repeat, Bias auf Untervertretene,
  ~22 % Rechercheur mit echtem Fun-Fact). Admin-LLM entscheidet das Ende
  (`smalltalk_end`) erst wenn alle ≥`_ST_MIN_PER_AGENT` dran waren; harte
  `max_turns`-Grenze; `_smalltalk_active`-Guard gegen Doppelstart.
- Die raumseitigen Tools (`ask_admin`/`ask_search`/`ask_agent`/`start_smalltalk`)
  werden in `person.py` async behandelt (nicht in `apply_tool_call`) und sind
  **immer** verfügbar (`ASK_TOOL_SCHEMAS`); Scheduling-Tools nur bei aktivem Task.

- `change_location(location)` — ändert/entfernt den Ort des **entschiedenen**
  Termins (`moderator.handle_location_change` aktualisiert `result_json`, postet
  im Raum, benachrichtigt); No-Op-Guard bei gleichem Ort. Leer = entfernen.

**Tool-Routing = LLM-Sache.** Welches Tool aufgerufen wird, entscheidet das Modell
anhand der Tool-Beschreibungen (kein Keyword→Tool-Mapping — das führte zu
Fehl-Routing, z. B. „location ändern" → fälschlich Suche). **Anti-Phantom-Netz:**
behauptet das Modell eine Aktion, ohne ein Tool aufzurufen
(`_seems_to_promise_action`, tool-*agnostisch*), wird **ein** Folge-Durchgang per
fester System-Nachricht angestoßen — **das Modell wählt weiterhin selbst, welches
Tool** (kein `tool_choice="required"`, da der DeepSeek-Thinking-Modus das nicht
unterstützt). Kritische Statusänderungen (bereit) laufen zusätzlich über den
deterministischen `POST /api/ready`.

**DeepSeek Thinking-Modus:** Default `deepseek-v4-flash` läuft im Thinking-Modus
(zuverlässiger bei Tool-Calls als das abgekündigte `deepseek-chat`). Der Client
**pinnt** das explizit (DeepSeek-gated über die Base-URL): `extra_body={"thinking":
{"type":"enabled"}, "reasoning_effort":"high"}` — schützt vor Default-Änderungen
und vor versehentlichem `max` (langsam). Weitere Beachtung: kein
`tool_choice="required"` (Thinking lehnt es ab); `reasoning_content` einer
Tool-Call-Runde wird erfasst und im Folge-Kontext zurückgegeben (sonst leere
Schlussantwort); Fallback — läuft ein Tool ohne Schlussantwort, wird dessen
Rückmeldung als Agenten-Antwort ausgegeben (`app/agents/person.py`,
`app/llm/client.py`). `temperature` ist im Thinking-Modus wirkungslos.

## 8. HTTP-/WS-API (`app/main.py`)

REST (unter `/api`):
- `GET /me` (inkl. `ready`-Flag), `GET /private/history`, `GET /room/history`,
  `GET /task`, `POST /task/start`.
- `POST /ready` — markiert den User deterministisch als bereit (Guard: ≥1 Slot),
  triggert `check_and_advance`. Umgeht die Unzuverlässigkeit des LLM-`mark_ready`.
- `GET /task/{id}/ics` — `.ics`-Export des entschiedenen Termins (token-gated).

Öffentliche Ergebnis-Routen (am `app`-Mount, **ohne** Auth, über `task.share_token`):
- `GET /share/{token}` — self-contained HTML-Ergebnis-Karte (OG-Meta, Kalender-Link).
- `GET /share/{token}.ics` — derselbe ICS-Export ohne Login.

**ICS-Export** (`app/ics.py`, pure): `build_ics(task, group, member_names)` baut ein
VEVENT aus `result_json`; Slot-Zeiten (naive Europe/Berlin) werden via `zoneinfo`
nach UTC (`…Z`) konvertiert. `share_token` (Schema v2, additiv) wird bei `decided`
gesetzt bzw. lazy via `repo.ensure_share_token` für Alt-Tasks.
- Auth: Token als `?t=` oder Cookie `at_token`.

WebSockets:
- `GET /ws/private?t=…` — bidirektional (User schreibt, Agent antwortet).
- `GET /ws/room?t=…` — für Menschen read-only (nur Empfang).

**Reconnect-Resync:** Beide Chats sind Push (kein Polling) und reconnecten
automatisch. Nach einem Reconnect lädt der Client nur die verpassten Nachrichten
nach — `GET /api/{private,room}/history?since=<id>` liefert alle Nachrichten mit
`id > since`. Der Client trackt pro Kanal die höchste ID + ein Seen-Set (Dedup),
sodass beim Nachladen nichts doppelt erscheint.

Static & Login: `GET /` liefert das SPA und setzt bei gültigem `?t=` das Cookie;
unbekanntes Token → 404-Fehlerseite. `GET /health` für Checks.

**Admin-Dashboard** `GET /admin` (HTTP Basic Auth, Passwort aus `ADMIN_PASSWORD`;
leer → 404/deaktiviert): read-only Übersicht — Gruppen, Termin-Status, Mitglieder
mit Persona-/Memory-Zähler und klickbaren Magic-Links, LLM-Calls/Tokens gesamt.

**Langzeit-Gedächtnis:** Tabelle `user_memory` (Schema v3), Tool `remember(fact)`
(via `apply_tool_call`, immer verfügbar), injiziert als `{memory}` in die
person-/smalltalk-Prompts (`context.user_memory_text`), in `/api/me` als `memories`.

Optionaler `BASE_PATH` mountet die App unter einem Sub-Pfad (nginx).

## 9. Frontend (`frontend/`)

Vanilla JS, kein Build. Zwei-Spalten-Layout: links Privatchat, rechts Task-Panel
+ Gruppenraum + 8-Bit-Tischansicht. Mini-Markdown-Renderer (Bold/Italic/Pipe-
Tabellen **und Links** `[text](url)` → klickbare Quellen-Chips mit 🔗) für
Kandidatentabelle und Recherche-Quellen. Reconnutende WebSockets. Agentenfarben
aus dem Backend.

- `app.js` — Boot, REST-Load, WebSockets, Chat-Composer, Start-Dialog.
- `viz.js` — **8-Bit-Visualisierung (Phase 4)**, gekapselt als `AgentViz`
  (`init`, `speak`, `react(agentId, emoji)`, `celebrate(emoji)`, `setAgents`).
  Canvas-2D-Renderer mit **prozeduralen** Pixel-Sprites (keine externen Assets):
  pro Agent deterministische Varianz (Frisur/Haarfarbe/Bart/Brille/Hautton aus der
  agent.id), Tisch mit Stühlen/Krügen, warme Lampe, Namensschilder, unregelmäßiges
  Blinzeln, Sprech-Animation und **Emotes** (schwebende Emojis). **Rein
  clientseitig** — `app.js` ruft `speak` + `react(erstes Emoji der Nachricht)` bei
  jeder `room_message` und `celebrate('🎉')` beim Übergang auf `decided`.

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
| `TASK_LLM_BUDGET` | 200 | Call-Obergrenze pro Verhandlung (Runaway-Backstop, keine Kostenbremse) |
| `DB_PATH` | `data/agenttable.db` | SQLite-Datei |
| `BASE_PATH` | (leer) | Sub-Pfad hinter Proxy |
| `config.toml [app]` | `slot_granularity`, `collecting_timeout_hours`, `max_messages_per_round`, `candidate_count`, `max_reschedule_iterations` | Ablauf-Parameter |
