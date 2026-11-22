"""Zentrale Einstellungen: Ligen, Spielzeiten, Regeln, Pfade.

Alles, was man ohne Programmierkenntnisse anpassen möchte, steht hier.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

# --- Pfade -----------------------------------------------------------------
WURZEL = Path(__file__).resolve().parent.parent
DATEN = WURZEL / "data"            # Rohdaten, Archiv, Einstellungen (wird committet)
ROH = DATEN / "roh"                # Zwischenspeicher der Abrufe
SEITE = WURZEL / "site"            # statische Webseite
SEITE_DATEN = SEITE / "data"       # vom Skript erzeugte JSON-Dateien für die Seite

# --- Spielzeiten -----------------------------------------------------------
# OpenLigaDB zählt eine Saison nach ihrem Startjahr: 2026 = Saison 2026/27.
# Wechselt automatisch am 10. Juli (dann stehen die neuen Spielpläne aller drei
# Ligen fest). Mit der Umgebungsvariable SAISON lässt sie sich fest einstellen.
def _saison_heute() -> int:
    heute = date.today()
    return heute.year if (heute.month, heute.day) >= (7, 10) else heute.year - 1


AKTUELLE_SAISON = int(os.environ.get("SAISON") or _saison_heute())
# Wie viele Vorjahre ins Training einfließen (ältere Spiele zählen dank
# Zeitgewichtung ohnehin kaum noch).
TRAININGS_VORJAHRE = 4
# Spielzeiten, an denen der Rückwärtstest die Trefferquote misst.
BACKTEST_SAISONS = [AKTUELLE_SAISON - 3, AKTUELLE_SAISON - 2, AKTUELLE_SAISON - 1]

# --- Ligen -----------------------------------------------------------------
# zonen: Name -> Tabellenplätze (1-basiert). Reihenfolge = Anzeige-Reihenfolge.
LIGEN = {
    "bl1": {
        "name": "Bundesliga",
        "kurz": "1. BL",
        "teams": 18,
        "football_data": "D1",
        "odds_api": "soccer_germany_bundesliga",
        "api_football": 78,
        "zonen": {
            "Meister": [1],
            "Champions League": [1, 2, 3, 4],
            "Relegation": [16],
            "Abstieg": [17, 18],
        },
    },
    "bl2": {
        "name": "2. Bundesliga",
        "kurz": "2. BL",
        "teams": 18,
        "football_data": "D2",
        "odds_api": "soccer_germany_bundesliga2",
        "api_football": 79,
        "zonen": {
            "Meister": [1],
            "Aufstieg": [1, 2],
            "Relegation oben": [3],
            "Relegation unten": [16],
            "Abstieg": [17, 18],
        },
    },
    "bl3": {
        "name": "3. Liga",
        "kurz": "3. Liga",
        "teams": 20,
        "football_data": None,           # football-data.co.uk hat keine 3. Liga
        "odds_api": "soccer_germany_liga3",
        "api_football": 80,
        "zonen": {
            "Meister": [1],
            "Aufstieg": [1, 2],
            "Relegation": [3],
            "Abstieg": [17, 18, 19, 20],
        },
    },
}
# Pokalspiele zählen nur für Ruhetage / Belastung, nicht fürs Modell.
POKAL = "dfb"

# --- Modell ----------------------------------------------------------------
HALBWERTSZEIT_TAGE = 240      # nach so vielen Tagen zählt ein Spiel nur noch halb
REGULARISIERUNG = 6.0         # zieht Teams mit wenig Daten Richtung Liga-Schnitt
# Höchstes Gewicht der Buchmacherquoten beim Mischen mit dem Modell.
# Begrenzt auf 50 %, damit das eigene Modell sichtbar bleibt (Entscheidung vom
# 22.11.2026; der Rückwärtstest hätte 100 % Quoten gewählt). 1.0 = keine Grenze.
MAX_QUOTEN_GEWICHT = float(os.environ.get("MAX_QUOTEN_GEWICHT", "0.5"))
SIMULATIONEN = 10_000         # Durchläufe der Restsaison
MAX_TORE = 10                 # Obergrenze der Ergebnis-Matrix

# --- Optionale Schlüssel (als GitHub-Secrets oder Umgebungsvariablen) -------
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "").strip()
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODELL = os.environ.get("GEMINI_MODELL", "gemini-flash-latest")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODELL = os.environ.get("OLLAMA_MODELL", "qwen3.8:latest")

# Wie viele Tage im Voraus die KI Infos sammelt.
KI_VORLAUF_TAGE = 4
