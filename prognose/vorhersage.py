"""Vom Datenbestand zur fertigen Prognose eines Spiels.

* trainiere(): baut die Trainingsdaten bis zu einem Stichtag und passt das Modell an.
* prognose(): Ergebnis-Matrix, Prozente, Tipp und Begründung für ein Spiel.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from . import config
from .daten import Datenbestand, Spiel
from .einfluesse import Einfluesse
from .modell import LIGEN, Modell, Trainingsdaten, anpassen, ergebnis_matrix, tendenz, zeitgewicht
from .quoten import mische


def trainiere(bestand: Datenbestand, einfl: Einfluesse, stichtag: datetime,
              start: np.ndarray | None = None) -> tuple[Modell, np.ndarray]:
    grenze = stichtag - timedelta(days=365 * (config.TRAININGS_VORJAHRE + 1))
    spiele = [s for s in bestand.spiele if s.gespielt and grenze <= s.anstoss < stichtag]
    team_ids: list[int] = []
    index: dict[int, int] = {}
    letzte_liga: dict[int, int] = {}
    for s in spiele:  # chronologisch -> am Ende steht die jüngste Liga
        for t in (s.heim_id, s.gast_id):
            if t not in index:
                index[t] = len(team_ids)
                team_ids.append(t)
            letzte_liga[t] = LIGEN.index(s.liga)
    k = len(einfl.aktiv)
    xh = np.zeros((len(spiele), k))
    xg = np.zeros((len(spiele), k))
    for i, s in enumerate(spiele):
        if k:
            xh[i], xg[i] = einfl.vektoren(s)
    daten = Trainingsdaten(
        heim=np.array([index[s.heim_id] for s in spiele]),
        gast=np.array([index[s.gast_id] for s in spiele]),
        liga=np.array([LIGEN.index(s.liga) for s in spiele]),
        tore_h=np.array([s.tore_heim for s in spiele], dtype=float),
        tore_g=np.array([s.tore_gast for s in spiele], dtype=float),
        gewicht=zeitgewicht([s.anstoss for s in spiele], stichtag),
        x_heim=xh, x_gast=xg,
    )
    team_liga = np.array([letzte_liga[t] for t in team_ids])
    return anpassen(daten, team_ids, team_liga, list(einfl.aktiv), start)


def passe_matrix_an(m: np.ndarray, ziel: tuple[float, float, float]) -> np.ndarray:
    """Skaliert die Ergebnis-Matrix so, dass sie die gemischten 1X2-Prozente trifft."""
    alt = tendenz(m)
    m = m.copy()
    unten = np.tril(np.ones_like(m, dtype=bool), -1)   # Heimsiege
    diag = np.eye(len(m), dtype=bool)
    oben = np.triu(np.ones_like(m, dtype=bool), 1)     # Gastsiege
    for maske, a, z in ((unten, alt[0], ziel[0]), (diag, alt[1], ziel[1]), (oben, alt[2], ziel[2])):
        if a > 0:
            m[maske] *= z / a
    return m / m.sum()


def waehle_tipp(m: np.ndarray) -> tuple[int, int]:
    """Wahrscheinlichster Spielstand innerhalb der wahrscheinlichsten Tendenz."""
    p = tendenz(m)
    t = int(np.argmax(p))
    maske = [np.tril(np.ones_like(m, dtype=bool), -1), np.eye(len(m), dtype=bool),
             np.triu(np.ones_like(m, dtype=bool), 1)][t]
    werte = np.where(maske, m, -1)
    x, y = np.unravel_index(int(np.argmax(werte)), m.shape)
    return int(x), int(y)


def prognose(modell: Modell, s: Spiel, einfl: Einfluesse,
             markt: tuple | None = None, quoten_gewicht: float = 0.0,
             mit_begruendung: bool = True) -> dict:
    xh, xg = einfl.vektoren(s) if einfl.aktiv else (None, None)
    lam, mu = modell.erwartete_tore(s.heim_id, s.gast_id, s.liga, xh, xg)
    m = ergebnis_matrix(lam, mu, modell.rho)
    p_basis = tendenz(m)
    tipp_markt = list(waehle_tipp(passe_matrix_an(m, markt))) if markt else None
    p = mische(p_basis, markt, quoten_gewicht) if markt else p_basis
    if p is not p_basis:
        m = passe_matrix_an(m, p)
    p = tendenz(m)
    tipp = waehle_tipp(m)
    flach = [(m[i, j], i, j) for i in range(6) for j in range(6)]
    flach.sort(reverse=True)
    erg = {
        "lam": round(lam, 3), "mu": round(mu, 3), "rho": round(modell.rho, 4),
        "p": [round(x, 4) for x in p],
        "p_modell": [round(x, 4) for x in p_basis],
        "p_markt": [round(x, 4) for x in markt] if markt else None,
        "tipp": list(tipp),
        "tipp_modell": list(waehle_tipp(ergebnis_matrix(lam, mu, modell.rho))),
        "tipp_markt": tipp_markt,
        "top": [{"e": f"{i}:{j}", "p": round(float(w), 4)} for w, i, j in flach[:6]],
        "ueber25": round(float(sum(m[i, j] for i in range(len(m)) for j in range(len(m)) if i + j > 2)), 4),
    }
    if mit_begruendung:
        erg["begruendung"] = begruende(modell, s, einfl, p_basis, p, markt, quoten_gewicht)
    return erg


def begruende(modell, s, einfl, p_basis, p_final, markt, quoten_gewicht) -> list[dict]:
    """Wie stark verschiebt jeder Einfluss die Heimsieg-Chance? (in Prozentpunkten)"""
    gruende = []
    info = einfl.beschreibung(s)
    if einfl.aktiv:
        # Modell ohne jeden Einfluss
        lam0, mu0 = modell.erwartete_tore(s.heim_id, s.gast_id, s.liga)
        p0 = tendenz(ergebnis_matrix(lam0, mu0, modell.rho))
        xh, xg = einfl.vektoren(s)
        for k, name in enumerate(einfl.aktiv):
            if xh[k] == 0 and xg[k] == 0:
                continue
            nur_h = np.zeros_like(xh); nur_g = np.zeros_like(xg)
            nur_h[k], nur_g[k] = xh[k], xg[k]
            l1, m1 = modell.erwartete_tore(s.heim_id, s.gast_id, s.liga, nur_h, nur_g)
            p1 = tendenz(ergebnis_matrix(l1, m1, modell.rho))
            delta = (p1[0] - p0[0]) * 100
            if abs(delta) < 0.5:
                continue
            if name == "ruhetage":
                text = f"Ruhetage: Heim {info['ruhe_heim']:.0f}, Gast {info['ruhe_gast']:.0f}"
            elif name == "nichts_eigen" or name == "nichts_gegner":
                wer = [w for w, f in (("Heim", info["nichts_heim"]), ("Gast", info["nichts_gast"])) if f]
                text = f"{' und '.join(wer)} hat tabellarisch nichts mehr zu spielen"
            else:
                text = name
            gruende.append({"text": text, "heimsieg_pp": round(delta, 1)})
        # doppelte Motivations-Texte zusammenfassen
        gesehen = {}
        for g in gruende:
            if g["text"] in gesehen:
                gesehen[g["text"]]["heimsieg_pp"] = round(gesehen[g["text"]]["heimsieg_pp"] + g["heimsieg_pp"], 1)
            else:
                gesehen[g["text"]] = g
        gruende = list(gesehen.values())
    if markt and quoten_gewicht > 0:
        delta = (p_final[0] - p_basis[0]) * 100
        gruende.append({
            "text": f"Buchmacher sehen Heimsieg bei {markt[0] * 100:.0f} % "
                    f"(Gewicht {quoten_gewicht * 100:.0f} %)",
            "heimsieg_pp": round(delta, 1),
        })
    return gruende
