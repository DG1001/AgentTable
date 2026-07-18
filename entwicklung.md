# AgentTable — Entwicklungshistorie

> Fortlaufendes Logbuch: was wann umgesetzt wurde, welche Entscheidungen mit
> welcher Begründung getroffen wurden. Neueste Einträge oben.
> Fachlich: [fachlich.md](fachlich.md) · Technisch: [technisch.md](technisch.md).

## 2026-07-18 — Tisch-Emotes + reichere Sprites (prozedural, self-contained)

Userwunsch: Emotes am Tisch; prüfen, ob freie Grafiken/SVG die Darstellung
verbessern. Evaluierung: externe Assets (Kenney/CC0, DiceBear/OpenMoji) würden den
8-Bit-Look brechen bzw. Build/externe Requests erfordern → gegen das
„self-contained, kein Build"-Prinzip. Entscheidung: **prozedural aufwerten**.

- `AgentViz.react(agentId, emoji)` + `celebrate(emoji)`: schwebende Emote-Emojis
  über den Sprites (Aufsteigen + Ein/Ausblenden). `app.js` triggert das erste
  „echte" Emoji jeder Raum-Nachricht (Status-Glyphen wie ✅/📅 gefiltert) und ein
  🎉-Jubel beim Übergang auf `decided`.
- Sprites: pro Agent deterministische Varianz aus `agent.id` (Frisur/Haarfarbe/
  Bart/Brille/Hautton); Szene mit Stühlen, Bierkrügen, warmer Hängelampe.
- Rein Frontend → **kein Server-Neustart** (statische Dateien; Nutzer laden neu).
  Playwright-verifiziert.

## 2026-07-18 — Ort ändern + Tool-Routing dem LLM überlassen

Beobachtung (Screenshot): „bitte die location anpassen, midnightbazar raus" ging
an den **Rechercheur** (`ask_search`), der nur suchen kann und „entfernt"
halluzinierte; der Agent meldete falsch „erledigt". Drei Ursachen: falsches
Routing (Keyword „location" erzwang Suche), **fehlende Fähigkeit** (Ort eines
entschiedenen Termins ändern), Phantom-„erledigt".

Fixes:
- **Neue Fähigkeit `change_location(location)`** → `moderator.handle_location_change`:
  ändert/entfernt `result_json.location` (kein Status/decided_at-Wechsel, neue
  `repo.update_task_result`), postet „📍 Ort geändert …" im Raum, benachrichtigt;
  No-Op-Guard bei gleichem Ort.
- **Routing-Redesign (Userwunsch „soll das LLM entscheiden"):** Das
  Keyword→Tool-Mapping (`_forced_tool_for`, `_SEARCH/ADMIN/SMALLTALK_HINTS`,
  `_wants_location_change`) **entfernt**. Welches Tool läuft, entscheidet jetzt das
  Modell über die Tool-Beschreibungen. Nur noch ein **tool-agnostisches**
  Phantom-Netz (`_seems_to_promise_action`): kündigt das Modell eine Aktion an,
  ohne ein Tool zu rufen, → ein Durchgang mit `tool_choice="required"`, **Modell
  wählt das Tool selbst**.
- Prompt: `ask_search` nur zum FINDEN, `change_location` zum ÄNDERN.
- Real gegen DeepSeek verifiziert: „location ändern" routet nun korrekt zu
  change_location (Organisator ändert wirklich), keine falschen „erledigt"-Claims.
  Tests: `_seems_to_promise_action`, `handle_location_change` (38 grün).

## 2026-07-18 — Milestone 1: Ergebnis nutzbar machen (ICS + teilbare Karte)

Ziel: den entschiedenen Termin über die App hinaus nutzbar machen (User-Wunsch,
„allgemeiner/wertvoller"). Google-Calendar-MCP bewusst weggelassen.

- **ICS-Export** (`app/ics.py`, pure `build_ics`): VEVENT aus `result_json`,
  Berlin→UTC via `zoneinfo`, RFC-5545-Escaping + CRLF. Route
  `GET /api/task/{id}/ics` (token-gated).
- **Teilbare Ergebnis-Karte**: `task.share_token` (Schema **v2**, additive
  `ALTER TABLE … ADD COLUMN`, non-destruktiv), bei `decided` gesetzt bzw. lazy via
  `repo.ensure_share_token` (auch für vor-existierende decided Tasks). Öffentliche
  Routen `GET /share/{token}` (HTML-Karte + OG-Meta) und `GET /share/{token}.ics`.
- Frontend: im decided-Zustand Buttons „📅 Zum Kalender" (ICS) und „🔗 Ergebnis
  teilen" (kopiert `/share/<token>`-Link).
- Tests: `tests/test_ics.py` (ICS-Format/UTC, share_token lazy+idempotent,
  Migration v2) — 35 grün, mit isolierter Test-DB.
- **Datensicherheit:** Live-DB (`data/agenttable.db`, Gruppe „Die 4") vor Deploy
  gesichert; Migration additiv; E2E-Test in separater Demo-DB/Port (echte Instanz
  unberührt). Siehe [[agenttable-preserve-live-data]].

### Roadmap (weitere gewählte Richtungen)
Tisch-Emotes/Reaktionen · Persona-Gedächtnis über Sessions · EN/DE-i18n · Web-Push.

## 2026-07-18 — Open-Source-Vorbereitung

- Versehentlich committeten Screenshot komplett aus Datei + History entfernt;
  einzigen echten Vornamen aus einer Dev-Log-Notiz genericisiert; unpublizierte
  Commits zu sauberen Commits zusammengefasst. Voll-History-Scan: keine
  Namen/Keys/Subdomain/E-Mails/Bilder in getrackten Dateien.
- `LICENSE` (MIT) + Top-Level `README.md` (englisch) erstellt.
- `.gitignore`: Root-Bilder/Screenshots ignoriert.
- `.env.example` publikationsfreundlich: `SEARCH_PROVIDER=none` als Default,
  generische Kommentare (kein workspace-spezifisches `searxng:8080` mehr).

## Konventionen

- Sprache im Code: Englisch (Bezeichner/Kommentare). UI + Agenten: Deutsch.
- Umsetzung phasenweise (spec §9); jede Phase lauffähig + Tests grün.
- Diese drei Docs werden bei jeder relevanten Änderung mitgezogen.

---

## 2026-07-16 — Responsive-Feinschliff (Mobile)

Frage „ist das Design responsiv?" → Breakpoint (≤760px → einspaltig) gab's schon,
aber die Mobile-UX war eng: alles in `100vh` mit zwei winzigen Nested-Scroll-Panes
(je ~1 Nachricht sichtbar).

- Mobile: `#app`/`#layout` → Höhe auto, **Seite scrollt natürlich**; jeder Chat-Log
  `min-height: 220px` / `max-height: 55vh` (brauchbar + eigener Scroll); Input
  `font-size: 16px` (kein iOS-Zoom); Header umbruchfähig; Viz 200px.
- Breite Kandidatentabellen scrollen jetzt in der Bubble (`overflow-x`), statt die
  Seite zu verbreitern.
- Playwright-verifiziert bei 390px (Handy) und 768px (Tablet): kein horizontaler
  Overflow, Chats gut lesbar.
- **Nachtrag (echtes Handy, 1080px @ DPR 3 = 360 CSS-px):** war noch zu breit
  (scrollWidth 475). Ursache: Grid/Flex-Kinder haben `min-width: auto` (=min-content)
  und schrumpfen nicht unter ihren breitesten Token → ein langer URL/Wort zwang die
  Spalte auf 463px. Fix: `min-width: 0` auf `.pane`/`.log` **plus**
  `overflow-wrap: anywhere; word-break: break-word` auf `.msg`. Danach scrollWidth
  = Viewport (360), bei 360px verifiziert.

## 2026-07-16 — Reconnect-Resync für beide Chats

Beide Chatfenster sind Push (WebSocket) und reconnecten automatisch, luden aber
nach einem Reconnect verpasste Nachrichten bisher nicht nach (nur bei Page-Reload).

Umgesetzt:
- `GET /api/{private,room}/history?since=<id>` (Repo: `since`-Filter `id > since`).
- Client (`app.js`): trackt pro Kanal `lastPrivateId`/`lastRoomId` + Seen-Set
  (Dedup in `appendPrivate`/`appendRoom`). `connect()` ruft bei **Reconnect**
  (nicht beim ersten Open) `resyncPrivate`/`resyncRoom` → lädt `?since=<lastId>`
  nach und hängt nur Neues an (kein Speak-Flackern, da über `appendRoom`).
- End-to-End verifiziert (Playwright): verpasste Nachricht erscheint nach Resync
  genau einmal, zweiter Resync dupliziert nicht.

## 2026-07-16 — Bugfix: Smalltalk-Under-Triggering (LLM erfand Block-Grund)

Beobachtung: User sagte „starte Smalltalk über KI", Agent antwortete aber, der
Organisator lasse es nicht zu, weil eine Person beim Termin noch nicht fertig sei — und
rief das Tool gar nicht auf. Das LLM verwechselte Termin-Bereitschaft mit einem
Smalltalk-Block (Halluzination) und under-triggerte (nachdem Smalltalk-Forcing
wegen früherer spurious Trigger entfernt worden war).

Fix: Smalltalk wird wieder erzwungen — aber **nur aus der USER-Nachricht**
(`_forced_tool_for(text, include_smalltalk=True)`), nicht aus der Modell-Antwort.
So forced „mach Smalltalk" es zuverlässig, während ein zufälliges „quatschen" in
der Agenten-Antwort keinen spurious Smalltalk auslöst. Tool-Beschreibung + Prompt
klargestellt (Sammelphase erlaubt, Bereitschaft anderer irrelevant, keine Ausreden).

## 2026-07-16 — Bugfix: Smalltalk startete während laufender Terminfindung

Beobachtung: neue Terminfindung gestartet, dann dem Agenten geschrieben → ein
Smalltalk (Thema „Porsche") lief parallel im selben Raum los und überlagerte die
Terminfindung. Ursachen: (a) das Modell rief `start_smalltalk` zu eifrig auf, weil
ein Thema erwähnt wurde; (b) das Forcing konnte es zusätzlich auslösen.

Fixes:
- **Guard**: `start_smalltalk` verweigert nur während der **Verhandlung**
  (`negotiating`) → keine Überlagerung mit dem live postenden Moderator-Loop.
  (Nachträglich gelockert: in `collecting` wartet der Raum nur, Smalltalk dort ok —
  reiner Helfer `smalltalk_block_reason` für Testbarkeit.)
- **Kein Forcing** von `start_smalltalk` mehr (`_forced_tool_for` deckt nur noch
  ask_search/ask_admin ab) — loses Wort-Matching löste sonst spurious Smalltalk aus.
- Tool-Beschreibung + Prompt geschärft (nur auf ausdrücklichen Wunsch, nicht
  während Terminfindung). Test ergänzt (31 grün).

## 2026-07-16 — Smalltalk: Zwischen-Recherche am Thema verankern

Beobachtung: Bei einem Trump-Smalltalk brachte der Rechercheur mittendrin eine
themenfremde Recherche (Werbung/Marketing). Ursache: Der Such-Seed war die letzte
Nachricht (auf 80 Zeichen gekürzt) — driftete das Gespräch kurz ab, driftete die
Suche mit. Fix: Bei gesetztem Thema ankert die Zwischen-Recherche am **Thema**
(mit variierendem Adjektiv für Abwechslung); nur ohne Thema fällt sie auf die
letzte Nachricht zurück. Real verifiziert (Thema Trump → Trump-Fakten).

## 2026-07-16 — Smalltalk: Web-Recherche-Opener zum Thema

Wunsch: zu Beginn eine schnelle Web-Recherche zum Thema, damit aktuelle Dinge
einfließen. Umgesetzt: Bei gesetztem Thema macht der Rechercheur ganz am Anfang
eine Suche (`search_agent.speak_in_room`, via Tavily) und postet sie als Opener
(„📚 Kurz vorab, was gerade zum Thema läuft: …", mit Quellen-Chips). Die
Zusammenfassung wird zusätzlich als `research_line` in den `smalltalk_person`-Prompt
gegeben, damit die Agenten aktuelle Bezüge einbauen. Real verifiziert (Thema
„aktuelle KI-Entwicklungen" → echter aktueller Überblick, Agenten greifen es auf).

## 2026-07-16 — Smalltalk: zufällige Pausen zwischen Meldungen

Wunsch: realistischer wirken lassen. Zwischen den Smalltalk-Meldungen jetzt eine
**zufällige Pause** (`_ST_PAUSE = (2.0, 5.5)` s, `asyncio.sleep(random.uniform(...))`
vor jedem Zug außer dem ersten). Läuft im Hintergrund; die Nachrichten „ploppen"
gestaffelt per WebSocket im Raum auf. Test setzt `_ST_PAUSE = (0, 0)` (kein echtes
Schlafen; die Guard `_ST_PAUSE[1] > 0` lässt zudem den RNG unangetastet).

## 2026-07-16 — Gimmick: Smalltalk-Modus

Nutzerwunsch: Agent kann Smalltalk anstoßen; Agenten quatschen dann random
nacheinander (nie zweimal direkt hintereinander), dürfen den Rechercheur nutzen,
und nach einigen Runden (jeder ≥2×) beendet der Organisator es je nach Inhalt.

Umgesetzt:
- **Tool `start_smalltalk(topic?)`** (immer verfügbar, in `_ASK_NAMES`), Forcing-
  Hint dafür in `_forced_tool_for` (falls das LLM nur ankündigt).
- **`moderator._run_smalltalk`**: Hintergrund-Loop, `random`-Sprecherwahl mit
  No-Repeat + Bias auf noch-nicht-genug-dran; ~22 % Chance auf den Rechercheur
  (echter Fun-Fact via Tavily); Ende-Entscheidung des Admin-LLM (`smalltalk_end`,
  JSON) erst wenn alle ≥`_ST_MIN_PER_AGENT`; Schlussnachricht (`smalltalk_close`);
  harte Obergrenze `max_turns`. `_smalltalk_active`-Guard gegen Doppelstart.
- Prompts `smalltalk_person/_end/_close.md`.
- Real verifiziert (Personas in character, Rechercheur-Fakten, Admin-Abschluss);
  deterministischer Test (30 grün).

## 2026-07-16 — UI: Quellen als klickbare Chips rendern

Nutzerwunsch: die Recherche-Quellen schicker darstellen. Der Mini-Markdown-Renderer
konnte noch keine Links → `[text](url)` erschien als Rohtext.

- `inline()` in `app.js`: Markdown-Links werden erkannt und als klickbare
  **🔗-Chips** (`a.src`, `target=_blank`) gerendert. Links werden vor Bold/Italic
  über **Private-Use-Platzhalter** ausgelagert (URLs mit `_`/`*` bleiben heil);
  Platzhalter mit `…` statt Ziffern, damit echte Zahlen (z. B. „9.2")
  nicht getroffen werden.
- CSS: dezente Chip-Optik (`.msg a.src`), passend auch auf User-Blasen.
- Visuell mit Playwright verifiziert (Quellen-Zeile als Chips, „9.2" intakt).

## 2026-07-16 — Suche: Tavily als Primär-Provider + Auto-Fallback auf SearXNG

Nutzerwunsch: auf Tavily umstellen, bei erreichtem Tavily-Limit automatisch auf
SearXNG zurückfallen (Brave-API abgelehnt — verlangt trotz Free-Tier Kreditkarte).

Umgesetzt:
- **`FallbackProvider(primary, secondary)`**: nutzt Tavily; bei Fehler ODER leerem
  Ergebnis (z. B. Quota erreicht) automatisch SearXNG. `SearchProvider.search`
  bekommt einheitlich den `engines`-Parameter durchgereicht.
- `get_provider`/`_build_provider`: bei `SEARCH_PROVIDER=tavily` + Key + SearXNG-URL
  → `FallbackProvider(Tavily, SearXNG)`; ohne Key → sauber SearXNG allein.
- `.env`: `SEARCH_PROVIDER=tavily`, `TAVILY_API_KEY=…` (Free-Tier, keine Kreditkarte).
- Verifiziert: Tavily liefert echte, quellenbelegte Treffer (Bars/Kneipen Stuttgart);
  Tests für FallbackProvider (empty/error/propagate/engines) — 29 grün.

## 2026-07-16 — Such-Agent: Retry auf CAPTCHA-freie Engines (OSM/Wikipedia)

Der Container hat 85 Engines aktiv, aber die „general"-Web-Engines (google, brave,
duckduckgo, startpage) werfen CAPTCHA/Rate-Limit. **openstreetmap** (Kategorie map)
und **wikipedia** dagegen nicht — perfekt für Location-Fragen.

Umgesetzt (dreistufig, degradiert sauber):
1. Normale Web-Suche über alle Engines.
2. Bei Block/leer: **Retry mit `&engines=openstreetmap,wikipedia`** (kein CAPTCHA;
   liefert für knappe Ortsanfragen echte Venues, z. B. „Maulwurf Kneipe", „Pils Bar
   Alt Stuttgart"). `SearchProvider.search` nimmt jetzt einen `engines`-Parameter.
3. Wenn weiterhin nichts: Wissens-Fallback mit Hinweis.

## 2026-07-16 — Such-Agent: Wissens-Fallback bei blockierten Suchmaschinen

Beobachtung: „Rechercheur findet nichts Brauchbares mehr." Diagnose (kein
Code-Bug): SearXNGs Upstream-Engines waren durch das viele Testen alle
rate-limited/geblockt (brave: too many requests, duckduckgo/startpage: CAPTCHA,
google: access denied) → 0 Treffer.

Umgesetzt:
- `SearxngProvider` unterscheidet jetzt **echte Fehlanzeige** von **blockierten
  Engines**: bei 0 Treffern + `unresponsive_engines` → `SearchUnavailable`.
- Such-Agent hat einen **Wissens-Fallback** (`search_fallback.md`): wenn keine
  Live-Treffer da sind (Provider fehlt / blockiert / leer), antwortet er aus
  eigenem Kenntnisstand mit **klarem Hinweis „ohne aktuelle Web-Recherche"** und
  ohne erfundene Detailfakten. So bleibt der Rechercheur nützlich.
- **Entscheidung:** Nützlichkeit > Strenge — statt „nichts gefunden" lieber
  plausible Vorschläge mit ehrlichem Disclaimer. Sobald die Engines wieder frei
  sind, greift automatisch wieder die echte Web-Recherche.

## 2026-07-16 — Bugfix: „Phantom-Aktionen" (Agent behauptet Tool-Call, ohne ihn zu machen)

Beobachtung: Chris' Agent sagte „ich hab den Rechercheur gefragt", aber es ging
keine `ask_search`-Anfrage raus (kein Raum-Post). Ursache: deepseek-chat setzt den
Tool-Call unzuverlässig ab und erzählt die Aktion stattdessen nur (gleiches Muster
wie bei `mark_ready`).

Zwei Ursachen + Fix:
1. **Tool-Verfügbarkeit:** Die „Anfrage"-Tools (ask_admin/ask_agent/ask_search)
   waren nur bei aktivem Task verfügbar. Der Task war aber schon `decided` →
   gar keine Tools → nur Phantom-Text. **Fix:** `ASK_TOOL_SCHEMAS` sind jetzt
   **immer** verfügbar (Rechercheur/andere Agenten kann man auch nach der
   Entscheidung fragen), Scheduling-Tools nur bei aktivem Task.
2. **Erzwingen:** Erkennt `handle_private_message` die Absicht (primär aus der
   **User-Nachricht**, robuster als der schwankende Modell-Wortlaut) und wurde das
   passende Tool in diesem Zug nicht aufgerufen, wird es per `tool_choice`
   (OpenAI-kompatibel, von DeepSeek unterstützt) **erzwungen** — genau das Tool
   (ask_search / ask_admin), kein spuriches `mark_ready`.
- `tool_choice` durch die LLM-Abstraktion gereicht (`client`, `parsing`, Mock).
- Prompt `person_private.md`: „keine Phantom-Aktionen" als oberste Regel.
- Real 3/3 zuverlässig gegen DeepSeek; deterministischer Test ergänzt (24 grün).

## 2026-07-16 — Budget-Default angehoben (60 → 200)

Nutzerhinweis: bisher ~1 Cent verbraucht — Kosten sind kein Thema. Das Budget ist
damit primär ein Runaway-Loop-Backstop, keine Kostenbremse. Default von 60 auf
**200** pro Verhandlung angehoben (`config.py`, `.env.example`), damit es im
Normalbetrieb nie stört; die Loop-Guards (max-Messages, 2×kein-Fortschritt) bleiben
die eigentliche Bremse.

## 2026-07-16 — Bugfix: Budget nur auf die Verhandlungsphase anwenden

Beobachtung: „Der Organisator hängt." Diagnose: (a) Chris war noch nicht `ready`
(Orga wartete korrekt), und (b) der Task hatte durch die vielen `ask_*`-Gespräche
in der Sammelphase bereits **85 LLM-Calls** verbraucht — **Budget 60**. Sobald
die Verhandlung startet, hätte `budget_ok` sofort `False` geliefert und die
Verhandlung **pausiert, bevor ein Vorschlag entsteht** → der eigentliche Hänger.

Fix:
- `budget_ok` zählt jetzt nur Calls **seit Verhandlungsstart** dieses Versuchs
  (`neg_base:<task_id>`, in `run_negotiation` gesetzt). Das Budget schützt damit
  gezielt den **autonomen Moderator-Loop** (§10-Absicht), nicht die
  user-getriebenen Anfragen (Onboarding, ask_admin/ask_agent/ask_search).
- **Grund:** Eine gesprächige Sammelphase darf das Verhandlungsbudget nicht
  aufbrauchen; sonst blockiert genau das Feature (Agenten-Fragen) den Abschluss.
- Test: Heavy Collecting-Usage > Budget → Verhandlung erreicht trotzdem `decided`.

## 2026-07-16 — Nutzer-Feedback: Agent kann andere Personen-Agenten fragen

Wunsch: Der eigene Agent soll auf Bitte hin den Agenten einer *anderen* Person
direkt fragen können (z. B. „frag Beas Agent, ob Dienstag geht", „wie lange
braucht Chris noch?").

Umgesetzt:
- **Neues Tool `ask_agent(agent_name, question)`**: löst das Ziel per Agenten-
  oder Personen-Name auf (`_resolve_person_agent`, fuzzy), postet die Frage im
  Raum (Asker) und **eine** Antwort des Ziel-Agenten (`person.answer_question`,
  gestützt auf dessen gespeicherte Verfügbarkeiten/Persona, keine erfundenen
  Zusagen). Antwort wird dem fragenden User privat weitergegeben.
- Prompt `person_answer.md` (knappe, ehrliche Antwort), `person_private.md` ergänzt.
- Tests: Zielauflösung + Q&A im Raum, unbekanntes Ziel (22 grün).
- **Entscheidung / Leitplanke (§10):** Bewusst **eine** user-getriggerte Frage →
  **eine** Antwort, sichtbar im Raum, aufs Task-Budget gezählt. Kein autonomes,
  gegenseitiges Agenten-Triggern → kein ungedrosselter Loop.

## 2026-07-16 — Nutzer-Feedback: deterministischer „Bereit"-Button

Beobachtung: Beim Versuch, „fertig" zu melden, blieb der User auf `ready=False`,
obwohl Slots vorhanden waren. Ursache: **der LLM-Agent rief das `mark_ready`-Tool
schlicht nicht auf** (er „glaubte", schon fertig zu sein) — die bekannte
Unzuverlässigkeit von Tool-Calls bei der kritischen Statusänderung.

Umgesetzt:
- **`POST /api/ready`**: setzt die Bereitschaft deterministisch (mit derselben
  Guard: mindestens ein Slot nötig), triggert `check_and_advance`, bestätigt im
  Privatchat. `/api/me` liefert jetzt zusätzlich `ready`.
- **Frontend „✓ Ich bin bereit"-Button** im Privatchat, sichtbar im Status
  `collecting`; nach Klick „Bereit gemeldet" (disabled). Aktualisiert sich über
  `task_update`-Events (Refetch von `/api/me`).
- **Entscheidung:** Kritische Statusübergänge (bereit) laufen nicht mehr über das
  LLM, sondern über eine explizite UI-Aktion — zuverlässig und für den User klar.
  Das NL-Tool `mark_ready` bleibt als bequemer Zusatzweg erhalten.

## 2026-07-16 — Nutzer-Feedback: Agent kann Orga/Such-Agent ansprechen + mark_ready-Guard

Beobachtung aus erstem echten Test: „Der Orga läuft nicht an." Diagnose über
DB-Zustand: Der initiierende User (Alex) hatte **0 Verfügbarkeiten**, war aber als
`ready` markiert. Der Orga lief dadurch an, fand **keine Schnittmenge für alle**,
postete „Kein Termin passt für alle" und **setzte alle auf nicht-bereit zurück**
(iteration→1). Der User übersah das und bekam vom Privatchat den (nicht
umsetzbaren) Rat, „den Organisator direkt zu fragen".

Umgesetzt:
- **Neues Tool `ask_admin(request)`** (Personen-Agent): stößt auf Userwunsch den
  Organisator im Raum an. Der Orga meldet den echten Stand zurück — inkl. **wer
  noch fehlt** („Ich warte noch auf: Alex") — oder startet, wenn alle bereit sind.
  → `moderator.handle_admin_request(...)`.
- **Neues Tool `ask_search(query)`** (Personen-Agent): stellt dem Such-Agenten auf
  Userwunsch eine Zwischenfrage; Frage + Antwort landen im Raum, Antwort wird dem
  User im Privatchat weitergegeben. `search.speak_in_room(...)` nimmt jetzt
  `group_id`/`task_id` statt eines vollen Task-Objekts (auch ohne aktiven Task nutzbar).
- **`mark_ready`-Guard:** verweigert „bereit", solange der User keinen einzigen
  Slot genannt hat. **Grund:** „ready" ohne Verfügbarkeit führt garantiert zum
  No-Intersection-Reset, der alle anderen mitreißt — genau der beobachtete Bug.
- Prompt `person_private.md` geschärft (sofort `set_availability`, `mark_ready`
  erst nach Slots, neue Tools nur auf Userwunsch).
- Tests: mark_ready-Guard + `handle_admin_request`-Statusmeldung (20 grün).

## 2026-07-16 — Phase 4: 8-Bit-Visualisierung

- `frontend/viz.js` (`AgentViz`): Canvas-2D-Szene mit einem prozeduralen
  Pixel-Sprite pro Agent an einem gemeinsamen Tisch. Organisator = Krone,
  Such-Agent = Lupe, Personen = Haare; Farbe je Agent aus dem Backend.
  Sprech-Animation (Hüpfen, Mundbewegung, Sprechblase mit Punkten), Blinzeln,
  Namensschilder auf der Tischfront, dunkler Karo-Boden.
- Integration in `app.js`: `AgentViz.init(canvas, me.agents)` beim Laden;
  `AgentViz.speak(msg.agent_id)` bei jeder eingehenden `room_message` (kind≠system).
  Layout um eine Canvas-Zeile unten rechts ergänzt (`index.html`, `style.css`).
- **Entscheidung:** Strikt clientseitig, nur WebSocket-Konsument — **keine
  Backend-Änderung** (wie in spec §8/§9 vorgesehen). Sprites werden prozedural
  aus Blöcken gezeichnet (kein Asset-Handling, kein Build-Step).
- **Verifiziert:** Playwright/Chromium (headless) — JS ohne Console-Errors,
  6 Agenten korrekt platziert (Krone/Lupe/Haare sichtbar), Sprechblasen genau
  über den getriggerten Agenten; realer Live-Pfad (echter `POST /task/start` →
  DeepSeek-Ansage → Broadcast → Sprite spricht, Animation stoppt nach 2,6 s).
  Screenshots im Scratchpad (nicht eingecheckt).

## 2026-07-16 — Such-Agent an SearXNG-Container angebunden

- Der Workspace stellt einen SearXNG-Container bereit
  (`http://searxng:8080/search?q=<begriff>&format=json`). Der bereits gebaute
  `SearxngProvider` erzeugt genau dieses URL-Schema — daher nur Konfiguration:
  `SEARCH_PROVIDER=searxng`, `SEARXNG_BASE_URL=http://searxng:8080` in `.env`
  (+ als Default in `.env.example` dokumentiert).
- **Verifiziert:** Container erreichbar (HTTP 200, JSON), `get_provider()` wählt
  `SearxngProvider`, echte Ergebnisse; Such-Agent liefert über DeepSeek eine
  kompakte, quellenreferenzierte deutsche Zusammenfassung (< 150 Wörter).
- **Entscheidung:** SearXNG als Default statt Tavily — kein API-Key nötig, läuft
  lokal im Workspace.

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
