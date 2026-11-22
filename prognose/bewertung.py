"""Kennzahlen für die Trefferquote – gemeinsam genutzt von Rückwärtstest und Archiv."""
from __future__ import annotations

import math


def tendenz_von(h: int, g: int) -> int:
    return 0 if h > g else (1 if h == g else 2)


def kicktipp_punkte(tipp, h, g) -> int:
    """4 = exakt, 3 = richtige Tordifferenz, 2 = richtige Tendenz, 0 = falsch.

    Wie bei Kicktipp: Bei Unentschieden gibt es keine Tordifferenz-Punkte
    (Remis-Tipp mit falschem Ergebnis = 2 Punkte)."""
    th, tg = tipp
    if th == h and tg == g:
        return 4
    if tendenz_von(th, tg) != tendenz_von(h, g):
        return 0
    if th != tg and th - tg == h - g:
        return 3
    return 2


def kennzahlen(eintraege: list[dict]) -> dict:
    """eintraege: {"p": [h,d,a] oder None, "tipp": [x,y], "h": int, "g": int}"""
    n = len(eintraege)
    if n == 0:
        return {"n": 0}
    tendenz = exakt = differenz = punkte = 0
    brier = logloss = 0.0
    n_p = 0
    for e in eintraege:
        h, g, tipp = e["h"], e["g"], e["tipp"]
        wahr = tendenz_von(h, g)
        if tendenz_von(*tipp) == wahr:
            tendenz += 1
            if tipp[0] - tipp[1] == h - g:
                differenz += 1
        if tipp[0] == h and tipp[1] == g:
            exakt += 1
        punkte += kicktipp_punkte(tipp, h, g)
        p = e.get("p")
        if p:
            n_p += 1
            brier += sum((p[k] - (1 if k == wahr else 0)) ** 2 for k in range(3))
            logloss += -math.log(max(p[wahr], 1e-9))
    erg = {
        "n": n,
        "tendenz": round(tendenz / n, 4),
        "exakt": round(exakt / n, 4),
        "differenz": round(differenz / n, 4),
        "punkte_schnitt": round(punkte / n, 3),
    }
    if n_p:
        erg["brier"] = round(brier / n_p, 4)
        erg["logloss"] = round(logloss / n_p, 4)
    return erg


def grundregel(eintraege: list[dict]) -> dict:
    """Vergleich: immer 'Heimsieg 2:1' tippen, Wahrscheinlichkeiten = Durchschnitt."""
    if not eintraege:
        return {"n": 0}
    return kennzahlen([
        {"p": [0.44, 0.26, 0.30], "tipp": [2, 1], "h": e["h"], "g": e["g"]} for e in eintraege
    ])
