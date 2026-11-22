"""Kicktipp-Regel und Tipp-Auswahl: Das ist der Kern für möglichst viele Punkte."""
import itertools

import numpy as np
import pytest

from prognose.bewertung import grundregel, kennzahlen, kicktipp_punkte
from prognose.modell import ergebnis_matrix
from prognose.vorhersage import kicktipp_erwartung, waehle_kicktipp, waehle_tipp


@pytest.mark.parametrize("tipp, ergebnis, punkte", [
    ((2, 1), (2, 1), 4),   # exakt
    ((2, 1), (3, 2), 3),   # Tordifferenz
    ((2, 1), (1, 0), 3),
    ((2, 1), (3, 0), 2),   # nur Tendenz
    ((2, 1), (1, 1), 0),   # falsch
    ((2, 1), (0, 1), 0),
    ((1, 1), (1, 1), 4),
    ((1, 1), (2, 2), 2),   # Remis: KEINE Differenz-Punkte
    ((0, 0), (3, 3), 2),
    ((1, 1), (1, 0), 0),
    ((0, 2), (1, 3), 3),   # Auswärtssieg Differenz
    ((0, 2), (0, 1), 2),
    ((0, 0), (0, 0), 4),
])
def test_kicktipp_punkte(tipp, ergebnis, punkte):
    assert kicktipp_punkte(tipp, *ergebnis) == punkte


def _matrix(lam, mu, rho=-0.05):
    return ergebnis_matrix(lam, mu, rho)


@pytest.mark.parametrize("lam, mu", [(1.6, 1.1), (1.0, 1.0), (0.8, 2.1), (2.8, 0.6)])
def test_erwartung_passt_zur_punkteregel(lam, mu):
    """Erwartete Punkte = Summe über alle Ergebnisse von P(Ergebnis) * Punkte."""
    m = _matrix(lam, mu)
    erw = kicktipp_erwartung(m)
    n = len(m)
    for a, b in itertools.product(range(6), range(6)):
        soll = sum(m[i, j] * kicktipp_punkte((a, b), i, j) for i in range(n) for j in range(n))
        assert erw[a, b] == pytest.approx(soll, abs=1e-12), (a, b)


def test_kicktipp_tipp_ist_das_maximum():
    m = _matrix(1.5, 1.2)
    tipp, wert = waehle_kicktipp(m)
    assert wert == pytest.approx(kicktipp_erwartung(m).max())
    assert wert >= kicktipp_erwartung(m)[waehle_tipp(m)] - 1e-12


def test_favorit_heim_wird_heimsieg_getippt():
    tipp, _ = waehle_kicktipp(_matrix(2.4, 0.7))
    assert tipp[0] > tipp[1]


def test_favorit_gast_wird_gastsieg_getippt():
    tipp, _ = waehle_kicktipp(_matrix(0.7, 2.4))
    assert tipp[0] < tipp[1]


def test_waehle_tipp_liegt_in_wahrscheinlichster_tendenz():
    m = _matrix(1.9, 0.9)
    a, b = waehle_tipp(m)
    assert a > b


def test_kennzahlen_punkte_schnitt():
    e = [
        {"p": [0.5, 0.3, 0.2], "tipp": [2, 1], "h": 2, "g": 1},   # 4
        {"p": [0.5, 0.3, 0.2], "tipp": [1, 1], "h": 2, "g": 2},   # 2
        {"p": [0.5, 0.3, 0.2], "tipp": [2, 0], "h": 0, "g": 1},   # 0
        {"p": [0.5, 0.3, 0.2], "tipp": [2, 0], "h": 3, "g": 1},   # 3
    ]
    k = kennzahlen(e)
    assert k["n"] == 4
    assert k["punkte_schnitt"] == pytest.approx(9 / 4, abs=1e-3)
    assert k["exakt"] == 0.25
    assert k["tendenz"] == 0.75


def test_grundregel_tippt_immer_2_1():
    e = [{"p": None, "tipp": [0, 3], "h": 2, "g": 1}]
    assert grundregel(e)["punkte_schnitt"] == 4
