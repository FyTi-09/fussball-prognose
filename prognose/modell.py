"""Das Herzstück: ein Dixon-Coles-Poisson-Modell für alle drei Ligen gemeinsam.

Idee in einem Satz: Jedes Team hat eine Angriffsstärke a und eine Abwehrstärke d.
Die erwarteten Tore der Heimmannschaft sind
    lambda = exp(basis + heimvorteil[liga] + a_heim - d_gast + Einflüsse)
und die der Gastmannschaft
    mu     = exp(basis + a_gast - d_heim + Einflüsse).
Die Tore selbst sind (fast) poissonverteilt; die Dixon-Coles-Korrektur rho
verschiebt etwas Wahrscheinlichkeit zwischen 0:0, 1:0, 0:1 und 1:1, weil
torarme Ergebnisse in echt etwas anders verteilt sind als beim reinen Poisson.

Warum ein gemeinsames Modell? Durch Auf- und Abstieg spielen Teams mal in der
einen, mal in der anderen Liga. Dadurch lernt das Modell, wie stark die Ligen
im Vergleich sind, und ein Aufsteiger startet nicht bei null.

Neuere Spiele zählen stärker (Halbwertszeit in config.py). Teams mit wenigen
Spielen werden sanft zum Schnitt ihrer Liga gezogen (Regularisierung).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
from scipy.optimize import minimize, minimize_scalar
from scipy.stats import poisson

from . import config

LIGEN = list(config.LIGEN)  # feste Reihenfolge: bl1, bl2, bl3


@dataclass
class Trainingsdaten:
    heim: np.ndarray       # Team-Index
    gast: np.ndarray
    liga: np.ndarray       # Liga-Index
    tore_h: np.ndarray
    tore_g: np.ndarray
    gewicht: np.ndarray
    x_heim: np.ndarray     # Einfluss-Merkmale für die Heimtore (n x k)
    x_gast: np.ndarray     # Einfluss-Merkmale für die Gasttore (n x k)


@dataclass
class Modell:
    team_index: dict[int, int]
    team_liga: np.ndarray          # Liga-Index je Team (für Regularisierung)
    basis: float
    heimvorteil: np.ndarray        # je Liga
    angriff: np.ndarray
    abwehr: np.ndarray
    liga_angriff: np.ndarray       # Schnitt je Liga
    liga_abwehr: np.ndarray
    beta: np.ndarray               # Gewichte der Einflüsse
    rho: float
    merkmale: list[str]

    def staerke(self, team_id: int, liga: str) -> tuple[float, float]:
        """Angriff/Abwehr eines Teams; unbekannte Teams bekommen den Liga-Schnitt."""
        i = self.team_index.get(team_id)
        if i is None:
            li = LIGEN.index(liga)
            # Neue Teams (z. B. Aufsteiger aus der Regionalliga) etwas schwächer
            return self.liga_angriff[li] - 0.1, self.liga_abwehr[li] - 0.1
        return self.angriff[i], self.abwehr[i]

    def erwartete_tore(self, heim_id, gast_id, liga, x_heim=None, x_gast=None):
        ah, dh = self.staerke(heim_id, liga)
        ag, dg = self.staerke(gast_id, liga)
        li = LIGEN.index(liga)
        lh = self.basis + self.heimvorteil[li] + ah - dg
        lg = self.basis + ag - dh
        if x_heim is not None and len(self.beta):
            lh += float(np.dot(x_heim, self.beta))
            lg += float(np.dot(x_gast, self.beta))
        return float(np.exp(lh)), float(np.exp(lg))


def tau(x, y, lam, mu, rho):
    """Dixon-Coles-Korrekturfaktor für torarme Ergebnisse."""
    t = np.ones_like(lam, dtype=float)
    t = np.where((x == 0) & (y == 0), 1 - lam * mu * rho, t)
    t = np.where((x == 0) & (y == 1), 1 + lam * rho, t)
    t = np.where((x == 1) & (y == 0), 1 + mu * rho, t)
    t = np.where((x == 1) & (y == 1), 1 - rho, t)
    return np.clip(t, 1e-9, None)


def ergebnis_matrix(lam: float, mu: float, rho: float, n: int = config.MAX_TORE) -> np.ndarray:
    """Wahrscheinlichkeit jedes Ergebnisses x:y (Zeilen = Heimtore)."""
    k = np.arange(n + 1)
    m = np.outer(poisson.pmf(k, lam), poisson.pmf(k, mu))
    m[0, 0] *= max(1 - lam * mu * rho, 1e-9)
    m[0, 1] *= max(1 + lam * rho, 1e-9)
    m[1, 0] *= max(1 + mu * rho, 1e-9)
    m[1, 1] *= max(1 - rho, 1e-9)
    return m / m.sum()


def tendenz(m: np.ndarray) -> tuple[float, float, float]:
    return float(np.tril(m, -1).sum()), float(np.trace(m)), float(np.triu(m, 1).sum())


def anpassen(daten: Trainingsdaten, team_ids: list[int], team_liga: np.ndarray,
             merkmale: list[str], start: np.ndarray | None = None) -> tuple[Modell, np.ndarray]:
    """Schätzt alle Parameter per gewichteter Maximum-Likelihood."""
    T = len(team_ids)
    L = len(LIGEN)
    K = daten.x_heim.shape[1]
    tau_reg = config.REGULARISIERUNG
    # Parametervektor: basis | heim[L] | a[T] | d[T] | A[L-1] | D[L-1] | beta[K]
    o_h, o_a, o_d = 1, 1 + L, 1 + L + T
    o_A, o_D, o_b = o_d + T, o_d + T + L - 1, o_d + T + 2 * (L - 1)
    n_par = o_b + K

    h, g, lg = daten.heim, daten.gast, daten.liga
    xh, xg, w = daten.tore_h, daten.tore_g, daten.gewicht

    def entpacke(p):
        A = np.concatenate([[0.0], p[o_A:o_A + L - 1]])
        D = np.concatenate([[0.0], p[o_D:o_D + L - 1]])
        return p[0], p[o_h:o_h + L], p[o_a:o_a + T], p[o_d:o_d + T], A, D, p[o_b:o_b + K]

    def ziel(p):
        b, hv, a, d, A, D, beta = entpacke(p)
        log_l = b + hv[lg] + a[h] - d[g] + daten.x_heim @ beta
        log_m = b + a[g] - d[h] + daten.x_gast @ beta
        lam, mu = np.exp(log_l), np.exp(log_m)
        ll = w * (xh * log_l - lam + xg * log_m - mu)
        ra = a - A[team_liga]
        rd = d - D[team_liga]
        f = -ll.sum() + 0.5 * tau_reg * (ra @ ra + rd @ rd) + 0.5 * beta @ beta

        gl = -w * (xh - lam)       # Ableitung nach log lambda
        gm = -w * (xg - mu)        # Ableitung nach log mu
        grad = np.zeros(n_par)
        grad[0] = gl.sum() + gm.sum()
        grad[o_h:o_h + L] = np.bincount(lg, gl, L)
        grad[o_a:o_a + T] = np.bincount(h, gl, T) + np.bincount(g, gm, T) + tau_reg * ra
        grad[o_d:o_d + T] = -np.bincount(g, gl, T) - np.bincount(h, gm, T) + tau_reg * rd
        gA = -tau_reg * np.bincount(team_liga, ra, L)
        gD = -tau_reg * np.bincount(team_liga, rd, L)
        grad[o_A:o_A + L - 1] = gA[1:]
        grad[o_D:o_D + L - 1] = gD[1:]
        grad[o_b:o_b + K] = daten.x_heim.T @ gl + daten.x_gast.T @ gm + beta
        return f, grad

    if start is None or len(start) != n_par:
        start = np.zeros(n_par)
        start[0] = np.log(max((xh * w).sum() + (xg * w).sum(), 1e-6) / (2 * w.sum()))
        start[o_h:o_h + L] = 0.25
    erg = minimize(ziel, start, jac=True, method="L-BFGS-B", options={"maxiter": 500})
    p = erg.x
    b, hv, a, d, A, D, beta = entpacke(p)

    # rho separat schätzen (bei festen lambda/mu) – einfacher und stabil
    lam = np.exp(b + hv[lg] + a[h] - d[g] + daten.x_heim @ beta)
    mu = np.exp(b + a[g] - d[h] + daten.x_gast @ beta)
    low = (xh <= 1) & (xg <= 1)

    def neg_rho(r):
        return -(w[low] * np.log(tau(xh[low], xg[low], lam[low], mu[low], r))).sum()

    rho = minimize_scalar(neg_rho, bounds=(-0.2, 0.2), method="bounded").x

    modell = Modell(
        team_index={tid: i for i, tid in enumerate(team_ids)},
        team_liga=team_liga, basis=float(b), heimvorteil=hv.copy(),
        angriff=a.copy(), abwehr=d.copy(), liga_angriff=A, liga_abwehr=D,
        beta=beta.copy(), rho=float(rho), merkmale=merkmale,
    )
    return modell, p


def zeitgewicht(anstoss: list[datetime], stichtag: datetime) -> np.ndarray:
    tage = np.array([(stichtag - t).total_seconds() / 86400 for t in anstoss])
    return 0.5 ** (np.clip(tage, 0, None) / config.HALBWERTSZEIT_TAGE)
