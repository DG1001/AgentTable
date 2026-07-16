# SPEC: AgentTable — Multiagenten-System zur Gruppenterminfindung

> Arbeitsanweisung / Projektbriefing für Claude Code.
> Sprache im Code: Englisch (Bezeichner, Kommentare). Sprache der UI und der Agenten: Deutsch.
> Arbeitsweise: Phasenweise umsetzen (siehe §9). Nach jeder Phase: lauffähiger Zustand, Tests grün, kurzes Fazit. Nicht vorgreifen.

---

## 1. Projektidee (Kontext)

Eine kleine Gruppe von Personen (typisch 4, generisch N) möchte ein persönliches Treffen abstimmen. Statt Doodle/Signal-Umfrage bekommt jede Person einen **eigenen KI-Agenten**. Die Person unterhält sich privat und asynchron mit ihrem Agenten (Verfügbarkeiten, Präferenzen). Die Agenten treffen sich in einem **gemeinsamen Chatraum** und verhandeln dort in natürlicher Sprache den optimalen Termin — moderiert von einem **Admin-/Organisator-Agenten**. Ein **Such-Agent** kann bei Bedarf Webrecherche beisteuern (z. B. Location-Vorschläge).

Das Projekt ist bewusst ein **Spaß- und Lernprojekt** (Multi-Agent-Orchestrierung, Lehr-/Demomaterial). Es soll trotzdem robust genug sein, um real einen Termin für 4 Personen zu finden.

**Zentrale Designentscheidung:** Natürliche Sprache ist die *Präsentationsschicht*. Die harte Terminlogik (Verfügbarkeits-Schnittmengen) läuft **deterministisch in Code** über strukturierte Daten. Agenten diskutieren Präferenzen und weiche Faktoren; sie dürfen niemals selbst Slot-Arithmetik "im Kopf" machen.

## 2. Ziele / Nicht-Ziele

**Ziele (MVP):**
- Token-basierter Zugang ohne Passwort (Magic-Link).
- Onboarding-Chat (2–3 lockere Fragen) → kurze Persona pro User-Agent.
- Privater 1:1-Chat User ↔ eigener Agent, asynchron, persistent.
- Gruppenraum: alle User-Agenten + Admin-Agent + Such-Agent; für Menschen live mitlesbar (read-only).
- Terminfindung: strukturierte Slot-Erfassung, deterministische Schnittmenge, Verhandlung weicher Präferenzen im Chat, Ergebnis-Zusammenfassung durch Admin-Agent.
- Such-Agent mit Web-Suche als Tool.
- Günstiges LLM (DeepSeek-Klasse) über OpenAI-kompatible API; Provider austauschbar.

**Nicht-Ziele (MVP):**
- Keine E-Mail-/Buchungs-Agenten (spätere Erweiterung, Architektur soll es nur nicht verbauen).
- Keine 8-Bit-Visualisierung in Phase 1–3 (Phase 4, reine View auf den Event-Stream).
- Kein Multi-Tenant-Betrieb, keine Skalierung über eine Handvoll Gruppen hinaus.
- Kein OAuth/Kalender-Sync.

## 3. Tech-Stack

- **Backend:** Python 3.12, FastAPI, Uvicorn, ein Prozess.
- **Persistenz:** SQLite (via SQLAlchemy oder sqlite3 direkt — einfach halten, aber Migrations-fähig, z. B. mit Alembic oder simplen Schema-Versionen).
- **Realtime:** WebSockets (FastAPI nativ) für beide Chats; Fallback HTTP-Polling nicht nötig.
- **Frontend:** Ein statisches SPA-Frontend (Vanilla JS oder Preact via CDN — kein Build-Step, kein npm-Zwang). Layout siehe §8.
- **LLM:** OpenAI-kompatibler Client (`openai`-SDK mit `base_url`), Standard: DeepSeek-API. Modellnamen/Keys via `.env`. Abstraktion siehe §7.
- **Web-Suche:** austauschbarer Provider hinter Interface `SearchProvider` (Start: eine einfache API wie Tavily/Brave/SearxNG-Instanz, konfigurierbar; wenn kein Key gesetzt: Such-Agent meldet sich als "nicht verfügbar").
- **Tests:** pytest; Kernlogik (Scheduling, Moderator-Loop-Zustandsmaschine) ohne LLM testbar; LLM-Aufrufe hinter Interface gemockt.
- **Deployment-Ziel:** Docker-Container, ein Volume für SQLite. Muss hinter nginx-Reverse-Proxy laufen (WebSocket-Upgrade beachten, relative Pfade / konfigurierbarer Base-Path).

## 4. Datenmodell (SQLite)

```
group(id, name, created_at)
user(id, group_id, token TEXT UNIQUE, display_name, persona TEXT NULL, created_at, last_seen_at)
agent  -- implizit 1:1 zu user; Admin- und Such-Agent sind Systemagenten ohne user
  → modelliert als: agent(id, group_id, kind ENUM('person','admin','search'), user_id NULL, name, persona TEXT NULL)
private_message(id, user_id, role ENUM('user','agent'), content, created_at)
room_message(id, group_id, agent_id NULL, kind ENUM('agent','system'), content, created_at)
availability(id, user_id, slot_start DATETIME, slot_end DATETIME, preference ENUM('yes','maybe','preferred'), note TEXT NULL, updated_at)
agent_state(agent_id, key, value_json)   -- z. B. onboarding_done, ready_for_task, task_id
task(id, group_id, type ENUM('schedule_meeting'), status ENUM('collecting','negotiating','decided','failed'), params_json, result_json, created_at, decided_at)
```

Hinweise:
- `availability` ist die **Single Source of Truth** für Termine. Der Personen-Agent schreibt sie via Tool-Call (§6), niemals als Freitext.
- Slots: Granularität halbe Tage oder Abende reicht (konfigurierbar); Zeitzone fix Europe/Berlin im MVP.
- `room_message` ist der Event-Stream — Basis für UI-Live-Ansicht und später die Pixel-Visualisierung.

## 5. Agenten & Rollen

### 5.1 Personen-Agent (einer pro User)
- System-Prompt = Basis-Prompt + generierte Persona (aus Onboarding) + aktueller Task-Kontext.
- Zwei Betriebsmodi:
  - **Privatmodus** (Chat mit dem eigenen User): Fragen stellen, Verfügbarkeiten & Präferenzen erfassen → Tool-Calls `set_availability`, `set_preference_note`, `mark_ready`.
  - **Raummodus** (Gruppenraum): vertritt seinen User, argumentiert für dessen Präferenzen, antwortet **nur wenn vom Moderator aufgerufen**.
- Kontext-Disziplin: Der Agent erhält im Raummodus NICHT das volle Transkript, sondern: eigene Persona, Task-Beschreibung, strukturierte Zwischenergebnisse (z. B. Schnittmengen-Tabelle) und die letzten K Raum-Nachrichten (K ≈ 10) plus eine laufende Kurzzusammenfassung.

### 5.2 Admin-Agent (Moderator + Organisator)
- Wird vom initiierenden User (via dessen Agenten oder direkt per UI-Aktion) mit einem Task beauftragt.
- Führt die **Zustandsmaschine des Tasks** (siehe §6) — aber: Zustandsübergänge macht deterministischer Code; das LLM des Admin-Agenten formuliert nur Ansagen, Zusammenfassungen und trifft die **Speaker-Selection**.
- Speaker-Selection: Nach jeder Raum-Nachricht entscheidet der Moderator-Loop, wer als Nächstes spricht (oder `END_ROUND`). Implementierung: LLM-Call mit strukturierter Antwort (`next_speaker: <agent_name> | END_ROUND`, JSON), plus harte Guards in Code:
  - max. Nachrichten pro Runde (z. B. 20),
  - kein Agent zweimal direkt hintereinander,
  - Floskel-Erkennung ist NICHT nötig — stattdessen: Agenten-Prompts verbieten reine Höflichkeitsantworten; Moderator beendet, sobald kein inhaltlicher Fortschritt (heuristisch: LLM-Urteil im selben Selection-Call: `progress: true|false`, zwei aufeinanderfolgende `false` → END_ROUND).
- Optional besseres Modell als die Personen-Agenten (konfigurierbar pro Agenten-Rolle).

### 5.3 Such-Agent
- Normaler Raumteilnehmer, spricht nur wenn aufgerufen.
- Tool: `web_search(query)` → Provider-Interface. Antwortet im Raum mit kompakter, quellenreferenzierter Zusammenfassung (max. ~150 Wörter).

## 6. Task-Flow "schedule_meeting" (Kernablauf)

```
Status: collecting
  1. Admin postet Task-Ansage in den Raum (Zeitraum, z. B. "nächste 4 Wochen, Abende + Wochenenden").
  2. Jeder Personen-Agent bekommt Auftrag: "Kläre mit deinem User Verfügbarkeiten."
  3. Personen-Agent führt das im PRIVATCHAT asynchron (wenn User online kommt).
     Tool-Calls: set_availability(slots[]), mark_ready().
  4. Admin-Loop prüft periodisch (und bei jedem mark_ready): alle ready? → negotiating.
     Timeout (konfigurierbar, z. B. 72 h): weiter mit vorhandenen Daten, fehlende User als "flexibel" markieren + Hinweis im Raum.

Status: negotiating
  5. CODE berechnet Schnittmengen aus `availability` → Kandidatenliste (Top 5, sortiert
     nach Anzahl 'preferred' > 'yes' > 'maybe').
  6. Admin postet Kandidatenliste als Tabelle in den Raum.
  7. Verhandlungsrunde(n): Moderator ruft Agenten auf; Agenten äußern Präferenzen ihrer
     User zu den KANDIDATEN (nicht zu freien Terminen). Optional ruft Admin den
     Such-Agenten für Location-Vorschläge zum Favoriten auf.
  8. Konvergenz: Admin-LLM schlägt Entscheidung vor → CODE validiert (Kandidat existiert,
     ist in Schnittmenge) → status = decided, result_json = {slot, location?, summary}.
     Keine Schnittmenge vorhanden → Admin postet das Problem, fordert Agenten auf, mit
     ihren Usern Alternativen zu klären → zurück zu collecting (max. 2 Iterationen,
     danach status = failed mit Bericht).

Status: decided
  9. Admin postet Ergebnis-Zusammenfassung im Raum; jeder Personen-Agent informiert
     seinen User im Privatchat bei dessen nächstem Besuch (persistente Notification).
```

**Tool-Definitionen der Personen-Agenten (Function Calling):**
- `set_availability(slots: [{start, end, preference}])` — ersetzt bestehende Slots des Users im Task-Zeitraum.
- `add_note(text)` — weiche Präferenz ("lieber nicht Freitag", "vegetarisches Restaurant").
- `mark_ready()` — signalisiert Vollständigkeit.
- Validierung strikt in Code (Datumsbereich, Überlappungen mergen); bei invalidem Call: Fehlermeldung als Tool-Result zurück an den Agenten.

## 7. LLM-Abstraktion

```python
class LLMClient(Protocol):
    async def chat(self, messages, tools=None, response_format=None, model_role: str = "person") -> LLMResponse: ...
```
- Konfiguration pro **Rolle** (`person`, `admin`, `search`): `model`, `base_url`, `api_key`, `temperature` aus `.env`/`config.toml`. Default alle auf dasselbe günstige Modell.
- Implementierung: OpenAI-SDK-kompatibel (deckt DeepSeek, Ollama, OpenRouter, Anthropic-kompatible Gateways ab).
- Retry mit Backoff, Timeout, Kostenzähler (Token-Log pro Call in Tabelle `llm_usage`).
- Strukturierte Antworten: JSON-Mode wenn Provider es kann, sonst Prompt + robustes Parsen (Fences strippen, ein Retry bei Parse-Fehler).

## 8. Frontend / UI

Layout (Desktop, responsive best effort):
```
┌────────────────────┬──────────────────────────────┐
│  Linke Spalte      │  Rechte Spalte (2/3)         │
│  (1/3)             │ ┌──────────────────────────┐ │
│  Privatchat mit    │ │ Gruppenraum (live,       │ │
│  eigenem Agenten   │ │ read-only für Menschen)  │ │
│                    │ ├──────────────────────────┤ │
│                    │ │ Phase 4: 8-Bit-Ansicht   │ │
│                    │ │ (vorerst: Task-Status-   │ │
│                    │ │  Panel + Kandidatenliste)│ │
└────────────────────┴──────────────────────────────┘
```
- Zugang: `https://host/?t=<token>` → Session-Cookie; unbekanntes Token → freundliche Fehlerseite.
- Gruppenraum-Nachrichten mit Agenten-Name, Farbe pro Agent, System-Nachrichten dezent.
- Admin-UI minimal: Der initiierende User kann per Button "Terminfindung starten" (mit Zeitraum-Eingabe) den Task anlegen — kein separates Admin-Login.
- Phase 4 (später): Canvas/PixiJS-View, Sprite pro Agent am Tisch, "speaking"-Animation getriggert durch `room_message`-Events. Keine Backend-Änderung nötig — nur WebSocket-Konsument.

## 9. Umsetzungsphasen (in dieser Reihenfolge, je Phase lauffähig + getestet)

**Phase 0 — Gerüst:** Projektstruktur, Config, SQLite-Schema, FastAPI-App, Token-Login, Docker. Seed-Skript: legt Gruppe + N User mit Tokens an, gibt Magic-Links aus.

**Phase 1 — Privatchat & Onboarding:** WebSocket-Privatchat, LLM-Anbindung, Onboarding (2–3 Fragen: Name/Spitzname, "Wie tickst du?", Lieblings-Emoji o. ä.) → Persona-Generierung (ein LLM-Call, 3–4 Sätze, wird gespeichert und dem User zur Bestätigung gezeigt). Danach freier Chat mit dem Agenten.

**Phase 2 — Gruppenraum & Terminfindung:** Raum-Stream, Admin-Agent, Moderator-Loop mit Speaker-Selection + Guards, Task-Zustandsmaschine, Availability-Tools, deterministische Schnittmengen-Engine (pure function, umfassend unit-getestet), Verhandlungsrunden, Ergebnis. **Das ist der Kern — hier die meiste Sorgfalt.**

**Phase 3 — Such-Agent:** SearchProvider-Interface + eine Implementierung, Einbindung in Moderator-Loop ("Admin darf Such-Agent aufrufen"), Zusammenfassung im Raum.

**Phase 4 — 8-Bit-Visualisierung:** Canvas-View unten rechts, Sprites, Sprech-Animation. Rein clientseitig.

## 10. Qualitäts- und Kostenleitplanken

- **Kosten:** hartes Budget pro Task (z. B. max. 60 LLM-Calls); Zähler in `llm_usage`; bei Überschreitung: Task pausiert mit System-Nachricht.
- **Loops:** alle Agenten-Interaktionen laufen durch den Moderator-Loop; es gibt keinen Pfad, auf dem Agenten einander direkt und ungedrosselt triggern.
- **Robustheit:** Jeder LLM-Call kann scheitern → Task-Zustand bleibt konsistent (Zustandsmaschine in DB, idempotente Schritte, Wiederaufnahme nach Neustart).
- **Prompt-Hygiene:** System-Prompts der Agenten als Dateien unter `prompts/` (versionierbar, deutsch), nicht im Code verstreut.
- **Sicherheit:** Tokens sind Zufalls-UUIDs; keine personenbezogenen Daten außer selbstgewähltem Namen; Raum-Inhalte nur für Gruppenmitglieder sichtbar.
- **Logging:** strukturiertes Log aller Moderator-Entscheidungen (wer, warum, progress-Flag) — wichtig für Debugging und als Lehrmaterial.

## 11. Offene Punkte (bei Bedarf im Verlauf klären, nicht blockierend)

- Slot-Granularität final (Abend/Halbtag vs. Stunden).
- Such-Provider-Wahl abhängig von verfügbarem API-Key.
- Benachrichtigung offline gegangener User (MVP: nur beim nächsten Login; später evtl. E-Mail-Agent).