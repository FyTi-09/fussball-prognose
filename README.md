# Fußball-Prognose

Ein privates Prognose-Tool für die 1. Bundesliga, die 2. Bundesliga und die 3. Liga, mit Live-Teil und KI-Einschätzungen.

## Was es macht

| Teil | Wie |
|---|---|
| Prognose pro Spiel | Dixon-Coles-Poisson-Modell, ein gemeinsames Modell für alle drei Ligen, neuere Spiele zählen stärker |
| Buchmacherquoten | football-data.co.uk (1./2. BL, kostenlos), The Odds API (alle Ligen, optional) |
| Zusatz-Einflüsse | Ruhetage und „nichts mehr zu spielen“ – nur aktiv, wenn der Rückwärtstest eine Verbesserung zeigt |
| Ergebnis-Tipp | wahrscheinlichster Spielstand innerhalb der wahrscheinlichsten Tendenz |
| Tabellenprognose | 10.000 simulierte Restsaisons |
| Stärkewert / Form | Elo-Rating über alle drei Ligen |
| Trefferquote | eingefrorene Prognosen (vor Anpfiff gespeichert) und Rückwärtstest über drei Spielzeiten |
| KI | Google News (RSS) → Gemini (kostenlos) oder Ollama lokal: Infos mit Quellenlink, eigener KI-Tipp, Vorschau |
| Live | die Seite holt während der Spiele jede Minute die Stände von OpenLigaDB und rechnet Siegchancen, Tabelle und Tabellenprognose im Browser neu |

## Lokal ausführen

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python run.py
python -m http.server 8765 --directory site
```

Dann <http://localhost:8765> öffnen.

Weitere Aufrufe:

| Befehl | Wirkung |
|---|---|
| `run.py --backtest` | Rückwärtstest erzwingen (läuft sonst automatisch alle 7 Tage) |
| `run.py --ohne-ki` | KI-Teil auslassen |
| `run.py --nur-ki` | nur den KI-Teil ausführen |

## Optionale Schlüssel

Als Umgebungsvariablen oder als GitHub-Secrets. Ohne Schlüssel läuft alles, nur ohne den jeweiligen Zusatz.

| Variable | Wofür | Anbieter |
|---|---|---|
| `GEMINI_API_KEY` | KI-Auswertung in der Cloud | Google AI Studio (kostenlos) |
| `ODDS_API_KEY` | aktuelle Quoten aller drei Ligen | the-odds-api.com (kostenlos, 500 Abrufe/Monat) |
| `API_FOOTBALL_KEY` | Verletzungen und Sperren | api-football.com (kostenlos, 100 Abrufe/Tag) |

## Aufbau

```
run.py                 ein kompletter Lauf
prognose/
  config.py            Ligen, Regeln, Einstellungen
  daten.py             OpenLigaDB laden
  modell.py            Dixon-Coles-Modell
  vorhersage.py        Training, Prognose, Tipp, Begründung
  einfluesse.py        Ruhetage, Motivation
  quoten.py            Buchmacherquoten
  backtest.py          Rückwärtstest, wählt Einflüsse und Quoten-Gewicht
  simulation.py        Tabellenprognose
  elo.py               Elo-Rating
  ki.py                Nachrichten + KI
  verletzungen.py      API-Football
  archiv.py            eingefrorene Prognosen, Trefferquote
site/                  statische Webseite (index.html, app.js, style.css)
data/                  Archiv, KI-Ergebnisse, Einstellungen, Zwischenspeicher
.github/workflows/     automatische Läufe und Veröffentlichung
ki_lokal.ps1           KI-Teil auf dem eigenen PC mit Ollama
```
