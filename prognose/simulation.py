"""Tabellenprognose: die Restsaison viele tausend Mal ausspielen.

Für jedes noch offene Spiel wird aus seiner Ergebnis-Matrix zufällig ein
Ergebnis gezogen. Danach wird die Abschlusstabelle sortiert (Punkte,
Tordifferenz, erzielte Tore, sonst Los). Wie oft ein Team auf welchem Platz
landet, ergibt die Wahrscheinlichkeiten für Meister, Aufstieg, Abstieg usw.
"""
from __future__ import annotations

import numpy as np

from . import config
from .daten import Spiel


def tabelle(spiele: list[Spiel], team_ids: list[int]) -> list[dict]:
    t = {tid: {"id": tid, "sp": 0, "s": 0, "u": 0, "n": 0, "tore": 0, "gegentore": 0, "pkt": 0}
         for tid in team_ids}
    for s in spiele:
        if not s.gespielt:
            continue
        h, g = t[s.heim_id], t[s.gast_id]
        h["sp"] += 1; g["sp"] += 1
        h["tore"] += s.tore_heim; h["gegentore"] += s.tore_gast
        g["tore"] += s.tore_gast; g["gegentore"] += s.tore_heim
        if s.tore_heim > s.tore_gast:
            h["s"] += 1; g["n"] += 1; h["pkt"] += 3
        elif s.tore_heim < s.tore_gast:
            g["s"] += 1; h["n"] += 1; g["pkt"] += 3
        else:
            h["u"] += 1; g["u"] += 1; h["pkt"] += 1; g["pkt"] += 1
    liste = list(t.values())
    liste.sort(key=lambda x: (x["pkt"], x["tore"] - x["gegentore"], x["tore"]), reverse=True)
    for i, z in enumerate(liste, 1):
        z["platz"] = i
    return liste


def simuliere(liga: str, spiele: list[Spiel], matrizen: dict[int, np.ndarray],
              n: int = config.SIMULATIONEN, seed: int = 42) -> dict:
    rng = np.random.default_rng(seed)
    team_ids = sorted({t for s in spiele for t in (s.heim_id, s.gast_id)})
    idx = {t: i for i, t in enumerate(team_ids)}
    T = len(team_ids)
    pkt = np.zeros((n, T)); tore = np.zeros((n, T)); gegen = np.zeros((n, T))
    for z in tabelle(spiele, team_ids):
        i = idx[z["id"]]
        pkt[:, i] = z["pkt"]; tore[:, i] = z["tore"]; gegen[:, i] = z["gegentore"]
    for s in spiele:
        if s.gespielt or s.id not in matrizen:
            continue
        m = matrizen[s.id]
        k = m.shape[0]
        zug = rng.choice(k * k, size=n, p=m.ravel() / m.sum())
        th, tg = zug // k, zug % k
        h, g = idx[s.heim_id], idx[s.gast_id]
        tore[:, h] += th; gegen[:, h] += tg
        tore[:, g] += tg; gegen[:, g] += th
        pkt[:, h] += np.where(th > tg, 3, np.where(th == tg, 1, 0))
        pkt[:, g] += np.where(tg > th, 3, np.where(th == tg, 1, 0))
    schluessel = pkt * 1e7 + (tore - gegen + 500) * 1e4 + tore * 10 + rng.random((n, T))
    reihenfolge = np.argsort(-schluessel, axis=1)
    plaetze = np.empty_like(reihenfolge)
    zeilen = np.arange(n)[:, None]
    plaetze[zeilen, reihenfolge] = np.arange(1, T + 1)
    zonen = config.LIGEN[liga]["zonen"]
    erg = {}
    for t, i in idx.items():
        verteilung = np.bincount(plaetze[:, i], minlength=T + 1)[1:] / n
        erg[t] = {
            "punkte_schnitt": round(float(pkt[:, i].mean()), 1),
            "punkte_p10": int(np.percentile(pkt[:, i], 10)),
            "punkte_p90": int(np.percentile(pkt[:, i], 90)),
            "platz_schnitt": round(float(plaetze[:, i].mean()), 2),
            "plaetze": [round(float(x), 4) for x in verteilung],
            "zonen": {name: round(float(np.isin(plaetze[:, i], p).mean()), 4)
                      for name, p in zonen.items()},
        }
    return erg
