# AgentTable — Fachliche Dokumentation

> Fachliche Sicht auf das Produkt: Was tut es, für wen, mit welchen Regeln.
> Technische Details siehe [technisch.md](technisch.md), Verlauf siehe [entwicklung.md](entwicklung.md).

## 1. Idee in einem Satz

Eine kleine Gruppe (typisch 4 Personen) findet einen gemeinsamen Termin, indem
**jede Person einen eigenen KI-Agenten** bekommt. Die Agenten verhandeln in einem
gemeinsamen Chatraum — moderiert von einem Organisator-Agenten — während die harte
Terminlogik deterministisch im Code läuft.

## 2. Grundprinzip: Sprache ist Präsentation, Rechnen ist Code

**Zentrale Regel:** Natürliche Sprache ist nur die *Präsentationsschicht*. Die
Verfügbarkeits-Schnittmengen werden **deterministisch in Python** berechnet
(`app/scheduling.py`). Agenten diskutieren nur *weiche* Präferenzen — sie rechnen
niemals selbst Termine aus. Das verhindert die typischen LLM-Fehler bei Datums-
und Mengenlogik.

## 3. Rollen / Akteure

| Rolle | Anzahl | Aufgabe |
|---|---|---|
| **Person** (Mensch) | N | Chattet privat mit dem eigenen Agenten, liest den Gruppenraum mit. |
| **Personen-Agent** | 1 pro Person | Erfasst Verfügbarkeiten privat, vertritt die Person im Raum. |
| **Admin-/Organisator-Agent** | 1 pro Gruppe | Moderiert, wählt Sprecher, fasst zusammen, entscheidet. |
| **Such-Agent** | 1 pro Gruppe | Liefert auf Zuruf Web-Recherche (z. B. Location-Vorschläge). |

Menschen sehen den Gruppenraum **read-only** — dort sprechen nur Agenten.

## 4. Ablauf einer Terminfindung (Nutzersicht)

1. **Zugang**: Jede Person öffnet ihren persönlichen Magic-Link (`/?t=<token>`).
   Kein Passwort. Unbekanntes Token → freundliche Fehlerseite.
2. **Onboarding**: Beim ersten Chat stellt der eigene Agent 2–3 lockere Fragen
   (Spitzname, „Wie tickst du?", Lieblings-Emoji) und erzeugt daraus eine kurze
   **Persona**, die der Person zur Bestätigung gezeigt wird.
3. **Start**: Eine Person startet über den Button „Terminfindung" eine Abstimmung
   (Anlass + Zeitraum + Granularität). Der Organisator postet eine Startansage im
   Raum und bittet alle Agenten, Verfügbarkeiten zu klären.
4. **Sammeln** (`collecting`): Jede Person nennt ihrem Agenten im Privatchat, wann
   sie kann. Der Agent speichert das strukturiert (Wunsch / geht / zur Not) und
   meldet „fertig" (erst möglich, wenn mindestens ein Termin genannt wurde).
   Timeout (Default 72 h): fehlende Personen gelten als „flexibel".
   - Auf Wunsch kann der eigene Agent den **Organisator anstoßen** („kann's
     losgehen?") — dieser meldet dann, wer noch fehlt, oder startet die
     Verhandlung. Ebenso kann der Agent dem **Such-Agenten Zwischenfragen**
     stellen (z. B. Restaurant-Ideen).
5. **Verhandeln** (`negotiating`): Der Code berechnet die besten gemeinsamen
   Termine (Top 5). Der Organisator postet sie als Tabelle. Die Personen-Agenten
   äußern reihum die weichen Präferenzen ihrer Menschen. Optional holt der
   Organisator Location-Vorschläge vom Such-Agenten.
6. **Entscheidung** (`decided`): Der Organisator wählt einen Termin aus der
   Kandidatenliste. Der Code validiert, dass der Termin wirklich für alle passt.
   Das Ergebnis wird im Raum zusammengefasst und jeder Person privat mitgeteilt.
7. **Kein gemeinsamer Termin?** Zurück zu Schritt 4 (max. 2 Wiederholungen),
   danach `failed` mit Bericht.

## 5. Verfügbarkeits-Semantik

- **Slots** sind grobkörnig: „Abende" (1 Slot/Tag) oder „Halbtage" (2 Slots/Tag).
  Zeitzone im MVP fix Europe/Berlin.
- Pro Slot eine Präferenz:
  - ⭐ `preferred` — Wunschtermin
  - ✓ `yes` — geht gut
  - ~ `maybe` — zur Not
- **Ranking der Kandidaten**: zuerst nach Anzahl verfügbarer Personen (Abdeckung),
  dann nach Präferenzstärke (`preferred` > `yes` > `maybe`), dann nach Datum.
- Ein Termin gilt nur als entscheidbar, wenn **alle** Teilnehmer können
  („volle Schnittmenge").

## 6. Qualitäts- & Kostenregeln (fachlich sichtbar)

- **Budget**: harte Obergrenze an LLM-Calls pro Terminfindung (Default 60). Bei
  Überschreitung pausiert die Terminfindung mit einer System-Nachricht.
- **Moderation**: Agenten sprechen nur, wenn der Organisator sie aufruft; niemand
  redet zweimal direkt hintereinander; ohne inhaltlichen Fortschritt endet die
  Runde. So bleibt der Raum lesbar und Kosten kalkulierbar.
- **Datensparsamkeit**: Es werden keine personenbezogenen Daten außer dem
  selbstgewählten Namen gespeichert. Tokens sind Zufalls-Strings. Rauminhalte
  sind nur für Gruppenmitglieder sichtbar.

## 7. 8-Bit-Tischansicht (Phase 4)

Unten rechts sitzt die Gruppe als kleine Pixel-Figuren an einem gemeinsamen Tisch
(„Der Tisch"). Jeder Agent hat ein eigenes Sprite in seiner Farbe: der Organisator
trägt eine **goldene Krone**, der Such-Agent hält eine **Lupe**, die Personen-
Agenten haben Haare. Sobald ein Agent im Raum spricht, **hüpft** sein Sprite,
der Mund bewegt sich und eine **Sprechblase** erscheint. Rein visuell, keine
eigene Logik — die Ansicht reagiert live auf denselben Nachrichtenstrom wie der
Textraum.

## 8. Bewusste Grenzen (MVP)

- Kein E-Mail-/Kalender-Sync, keine Buchung.
- Kein Multi-Tenant-Betrieb, nur eine Handvoll Gruppen.
- Offline gegangene Personen werden erst beim nächsten Login benachrichtigt.
