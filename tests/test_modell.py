"""Wahrscheinlichkeiten, Mischen mit Quoten, Saisonwechsel."""
from datetime import date

import numpy as np
import pytest

from prognose import config
from prognose.modell import ergebnis_matrix, tendenz
from prognose.quoten import _wahrscheinlichkeiten, mische
from prognose.vorhersage import passe_matrix_an


@pytest.mark.parametrize("lam, mu, rho", [(1.5, 1.1, -0.08), (0.5, 0.5, 0.1), (3.0, 0.4, 0.0)])
def test_matrix_summe_eins(lam, mu, rho):
    m = ergebnis_matrix(lam, mu, rho)
    assert m.sum() == pytest.approx(1.0)
    assert (m >= 0).all()
    assert sum(tendenz(m)) == pytest.approx(1.0)


def test_staerkere_heimelf_hat_hoehere_siegchance():
    stark = tendenz(ergebnis_matrix(2.2, 0.8, -0.05))
    gleich = tendenz(ergebnis_matrix(1.3, 1.3, -0.05))
    assert stark[0] > gleich[0] > 0.3
    assert gleich[0] == pytest.approx(gleich[2])  # symmetrisch bei gleichen Toren


def test_passe_matrix_an_trifft_ziel():
    m = ergebnis_matrix(1.4, 1.2, -0.05)
    ziel = (0.6, 0.25, 0.15)
    neu = passe_matrix_an(m, ziel)
    assert tendenz(neu) == pytest.approx(ziel, abs=1e-9)


def test_mische():
    assert mische((0.5, 0.3, 0.2), (0.7, 0.2, 0.1), 0.5) == pytest.approx((0.6, 0.25, 0.15))
    assert mische((0.5, 0.3, 0.2), None, 0.5) == (0.5, 0.3, 0.2)
    assert mische((0.5, 0.3, 0.2), (0.7, 0.2, 0.1), 0.0) == (0.5, 0.3, 0.2)


def test_quoten_ohne_marge():
    p = _wahrscheinlichkeiten("1.80", "3.60", "4.50")
    assert sum(p) == pytest.approx(1.0)
    assert p[0] > p[2] > 0
    assert _wahrscheinlichkeiten("1.0", "3", "4") is None
    assert _wahrscheinlichkeiten("x", "3", "4") is None


@pytest.mark.parametrize("heute, saison", [
    (date(2026, 7, 9), 2025), (date(2026, 7, 10), 2026),
    (date(2026, 11, 22), 2026), (date(2027, 1, 5), 2026),
])
def test_saisonwechsel_10_juli(monkeypatch, heute, saison):
    class FakeDate(date):
        @classmethod
        def today(cls):
            return heute
    monkeypatch.setattr(config, "date", FakeDate)
    assert config._saison_heute() == saison
