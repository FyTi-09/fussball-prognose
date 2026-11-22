/* Fußball-Prognose – Oberfläche und Live-Teil.
 *
 * Die schweren Berechnungen macht Python (run.py) und legt sie in data/*.json ab.
 * Dieses Skript zeigt sie an und ergänzt während der Spiele den Live-Teil:
 * Es holt alle 60 Sekunden die Spielstände von OpenLigaDB und rechnet
 * Live-Siegchancen, Live-Tabelle und Live-Tabellenprognose direkt im Browser.
 */
"use strict";

const API = "https://api.openligadb.de";
const LIVE_INTERVALL = 60_000;
const LIVE_SIMULATIONEN = 4000;
const ZUSTAND = {
  index: null, daten: {}, treffer: null, backtest: null,
  liga: null, ansicht: "spieltag", spieltag: {}, team: null,
  offen: new Set(), liveVerlauf: {}, liveTimer: null, liveSim: {}, liveStand: null,
};
const $ = (s, el = document) => el.querySelector(s);
const inhalt = $("#inhalt");

/* ---------- Hilfsfunktionen ---------- */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x, d = 0) => (x == null ? "–" : (x * 100).toFixed(d).replace(".", ",") + " %");
const zahl = (x, d = 1) => (x == null ? "–" : Number(x).toFixed(d).replace(".", ","));
const sicherUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
const TAGE = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
function datum(iso) {
  const d = new Date(iso);
  return `${TAGE[d.getDay()]} ${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}. ` +
    `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
const tendenz = (h, g) => (h > g ? 0 : h === g ? 1 : 2);
const daten = () => ZUSTAND.daten[ZUSTAND.liga];
const team = (id) => daten().teams[id] || { name: "?", kurz: "?", logo: "" };
const logo = (t) => (t.logo ? `<img src="${esc(t.logo)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : "");

async function ladeJson(pfad) {
  const r = await fetch(pfad, { cache: "no-cache" });
  if (!r.ok) throw new Error(pfad + ": " + r.status);
  return r.json();
}

/* ---------- Mathe: Ergebnis-Matrix wie in Python ---------- */
function poissonPmf(k, l) {
  let p = Math.exp(-l);
  for (let i = 1; i <= k; i++) p *= l / i;
  return p;
}
function matrix(lam, mu, rho = 0, n = 10) {
  const m = [];
  let s = 0;
  for (let i = 0; i <= n; i++) {
    m.push([]);
    for (let j = 0; j <= n; j++) {
      let v = poissonPmf(i, lam) * poissonPmf(j, mu);
      if (i === 0 && j === 0) v *= Math.max(1 - lam * mu * rho, 1e-9);
      if (i === 0 && j === 1) v *= Math.max(1 + lam * rho, 1e-9);
      if (i === 1 && j === 0) v *= Math.max(1 + mu * rho, 1e-9);
      if (i === 1 && j === 1) v *= Math.max(1 - rho, 1e-9);
      m[i].push(v); s += v;
    }
  }
  return m.map((z) => z.map((v) => v / s));
}
function tendenzAus(m) {
  const t = [0, 0, 0];
  m.forEach((z, i) => z.forEach((v, j) => { t[tendenz(i, j)] += v; }));
  return t;
}
function passeAn(m, ziel) {
  const alt = tendenzAus(m);
  return m.map((z, i) => z.map((v, j) => { const k = tendenz(i, j); return alt[k] > 0 ? v * ziel[k] / alt[k] : v; }));
}

/* ---------- Live ---------- */
function geschaetzteMinute(anstoss) {
  const min = (Date.now() - new Date(anstoss).getTime()) / 60000;
  if (min < 0) return 0;
  if (min <= 47) return min;
  if (min <= 62) return 45;               // Halbzeitpause
  return Math.min(min - 17, 93);
}
function liveWahrscheinlichkeit(sp) {
  const p = sp.prognose;
  if (!p || !sp.erg) return null;
  const minute = geschaetzteMinute(sp.anstoss);
  const rest = Math.max(0, 90 - minute) / 90;
  const m = matrix(p.lam * rest, p.mu * rest, 0, 8);
  const t = [0, 0, 0];
  m.forEach((z, i) => z.forEach((v, j) => { t[tendenz(sp.erg[0] + i, sp.erg[1] + j)] += v; }));
  return { p: t, minute };
}
function istLiveFenster(sp) {
  const start = new Date(sp.anstoss).getTime();
  return !sp.beendet && Date.now() >= start && Date.now() <= start + 150 * 60000;
}
function standAus(m) {
  const res = (m.matchResults || []).slice().sort((a, b) => a.resultOrderID - b.resultOrderID);
  const end = res.filter((r) => r.resultTypeID === 2);
  const r = end.length ? end[end.length - 1] : null;
  if (m.matchIsFinished && r) return [r.pointsTeam1, r.pointsTeam2];
  const tore = m.goals || [];
  if (tore.length) { const g = tore[tore.length - 1]; return [g.scoreTeam1 ?? 0, g.scoreTeam2 ?? 0]; }
  if (r) return [r.pointsTeam1, r.pointsTeam2];
  return Date.now() >= new Date(m.matchDateTimeUTC || m.matchDateTime).getTime() ? [0, 0] : null;
}

async function liveAktualisieren() {
  const d = daten();
  if (!d) return;
  const spieltage = new Set([d.aktueller_spieltag]);
  d.spiele.forEach((s) => { if (istLiveFenster(s)) spieltage.add(s.st); });
  let geaendert = false;
  for (const st of spieltage) {
    let liste;
    try { liste = await ladeJson(`${API}/getmatchdata/${d.liga}/${d.saison}/${st}`); } catch { continue; }
    for (const m of liste) {
      const sp = d.spiele.find((s) => s.id === m.matchID);
      if (!sp) continue;
      const erg = standAus(m);
      const beendet = !!m.matchIsFinished;
      const neueTore = (m.goals || []).map((g) => ({ minute: g.matchMinute, name: g.goalGetterName, heim: g.scoreTeam1, gast: g.scoreTeam2, elfmeter: g.isPenalty, eigentor: g.isOwnGoal }));
      if (JSON.stringify(erg) !== JSON.stringify(sp.erg) || beendet !== sp.beendet) geaendert = true;
      sp.erg = erg; sp.beendet = beendet; sp.tore = neueTore;
      sp.live = !beendet && erg != null && Date.now() >= new Date(sp.anstoss).getTime();
      if (sp.live) {
        const lw = liveWahrscheinlichkeit(sp);
        if (lw) (ZUSTAND.liveVerlauf[sp.id] ||= []).push({ min: lw.minute, p: lw.p });
      }
    }
  }
  const nochLive = d.spiele.some((s) => s.live || istLiveFenster(s));
  if (geaendert || nochLive) {
    ZUSTAND.liveSim[d.liga] = simuliere(d);
    ZUSTAND.liveStand = new Date();
  }
  zeichne();
  clearTimeout(ZUSTAND.liveTimer);
  if (nochLive) ZUSTAND.liveTimer = setTimeout(liveAktualisieren, LIVE_INTERVALL);
  else {
    // nächste Prüfung kurz vor dem nächsten Anstoß
    const naechster = d.spiele.filter((s) => !s.beendet && new Date(s.anstoss) > Date.now())
      .map((s) => new Date(s.anstoss).getTime()).sort((a, b) => a - b)[0];
    if (naechster && naechster - Date.now() < 6 * 3600e3) {
      ZUSTAND.liveTimer = setTimeout(liveAktualisieren, Math.max(naechster - Date.now() + 60000, 30000));
    }
  }
}

/* Live-Tabelle (laufende Spiele zählen mit ihrem aktuellen Stand) */
function tabelleJetzt(d) {
  const t = {};
  Object.keys(d.teams).forEach((id) => { t[id] = { id: +id, sp: 0, s: 0, u: 0, n: 0, tore: 0, gegentore: 0, pkt: 0, live: false }; });
  for (const s of d.spiele) {
    if (!s.erg || !(s.beendet || s.live)) continue;
    const h = t[s.heim], g = t[s.gast], [a, b] = s.erg;
    if (!h || !g) continue;
    h.sp++; g.sp++; h.tore += a; h.gegentore += b; g.tore += b; g.gegentore += a;
    if (s.live) { h.live = g.live = true; }
    if (a > b) { h.s++; g.n++; h.pkt += 3; } else if (a < b) { g.s++; h.n++; g.pkt += 3; } else { h.u++; g.u++; h.pkt++; g.pkt++; }
  }
  const liste = Object.values(t).sort((x, y) => y.pkt - x.pkt || (y.tore - y.gegentore) - (x.tore - x.gegentore) || y.tore - x.tore);
  liste.forEach((z, i) => { z.platz = i + 1; });
  return liste;
}

/* Live-Tabellenprognose: Restsaison im Browser simulieren */
function simuliere(d) {
  const ids = Object.keys(d.teams).map(Number);
  const idx = Object.fromEntries(ids.map((id, i) => [id, i]));
  const T = ids.length, N = LIVE_SIMULATIONEN;
  const pkt0 = new Float64Array(T), tor0 = new Float64Array(T), geg0 = new Float64Array(T);
  // laufende Spiele NICHT als fertig zählen: Basis nur aus beendeten
  for (const s of d.spiele) {
    if (!s.beendet || !s.erg) continue;
    const h = idx[s.heim], g = idx[s.gast], [a, b] = s.erg;
    tor0[h] += a; geg0[h] += b; tor0[g] += b; geg0[g] += a;
    if (a > b) pkt0[h] += 3; else if (a < b) pkt0[g] += 3; else { pkt0[h]++; pkt0[g]++; }
  }
  // Verteilungen der offenen Spiele vorbereiten (kumulativ)
  const offen = [];
  for (const s of d.spiele) {
    if (s.beendet || !s.prognose) continue;
    const p = s.prognose;
    let m, plusH = 0, plusG = 0;
    if (s.live && s.erg) {
      const rest = Math.max(0, 90 - geschaetzteMinute(s.anstoss)) / 90;
      m = matrix(p.lam * rest, p.mu * rest, 0, 8);
      [plusH, plusG] = s.erg;
    } else {
      m = passeAn(matrix(p.lam, p.mu, p.rho, 8), p.p);
    }
    const k = m.length, kum = new Float64Array(k * k);
    let acc = 0;
    for (let i = 0; i < k; i++) for (let j = 0; j < k; j++) { acc += m[i][j]; kum[i * k + j] = acc; }
    offen.push({ h: idx[s.heim], g: idx[s.gast], k, kum, plusH, plusG });
  }
  const platzZaehler = ids.map(() => new Float64Array(T));
  const pktSumme = new Float64Array(T);
  const pkt = new Float64Array(T), tor = new Float64Array(T), geg = new Float64Array(T);
  const ordnung = ids.map((_, i) => i);
  const schluessel = new Float64Array(T);
  for (let n = 0; n < N; n++) {
    pkt.set(pkt0); tor.set(tor0); geg.set(geg0);
    for (const o of offen) {
      const r = Math.random() * o.kum[o.kum.length - 1];
      let lo = 0, hi = o.kum.length - 1;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (o.kum[mid] < r) lo = mid + 1; else hi = mid; }
      const a = Math.floor(lo / o.k) + o.plusH, b = (lo % o.k) + o.plusG;
      tor[o.h] += a; geg[o.h] += b; tor[o.g] += b; geg[o.g] += a;
      if (a > b) pkt[o.h] += 3; else if (a < b) pkt[o.g] += 3; else { pkt[o.h]++; pkt[o.g]++; }
    }
    for (let i = 0; i < T; i++) schluessel[i] = pkt[i] * 1e7 + (tor[i] - geg[i] + 500) * 1e4 + tor[i] * 10 + Math.random();
    ordnung.sort((x, y) => schluessel[y] - schluessel[x]);
    for (let r = 0; r < T; r++) platzZaehler[ordnung[r]][r]++;
    for (let i = 0; i < T; i++) pktSumme[i] += pkt[i];
  }
  const erg = {};
  ids.forEach((id, i) => {
    const plaetze = Array.from(platzZaehler[i], (c) => c / N);
    const zonen = {};
    for (const [name, pl] of Object.entries(d.zonen)) zonen[name] = pl.reduce((s, p) => s + plaetze[p - 1], 0);
    erg[id] = {
      punkte_schnitt: pktSumme[i] / N,
      platz_schnitt: plaetze.reduce((s, p, r) => s + p * (r + 1), 0),
      plaetze, zonen,
      punkte_p10: null, punkte_p90: null,
    };
  });
  return erg;
}

/* ---------- Zeichnen ---------- */
function balken(p) {
  return `<div class="balken" role="img" aria-label="Heimsieg ${pct(p[0])}, Remis ${pct(p[1])}, Gastsieg ${pct(p[2])}">
    <div class="h" style="width:${p[0] * 100}%"></div><div class="x" style="width:${p[1] * 100}%"></div><div class="a" style="width:${p[2] * 100}%"></div></div>
    <div class="prozente"><span>${pct(p[0])}</span><span class="m">Remis ${pct(p[1])}</span><span>${pct(p[2])}</span></div>`;
}

function sparkline(verlauf) {
  if (!verlauf || verlauf.length < 2) return "";
  const W = 300, H = 46;
  const x = (m) => (m / 93) * W;
  const lin = (k) => verlauf.map((v, i) => `${i ? "L" : "M"}${x(v.min).toFixed(1)},${(H - v.p[k] * H).toFixed(1)}`).join("");
  return `<svg class="live-verlauf" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Verlauf der Siegchancen">
    <line x1="0" x2="${W}" y1="${H / 2}" y2="${H / 2}" stroke="var(--border)" stroke-width="1"/>
    <path d="${lin(0)}" fill="none" stroke="var(--heim)" stroke-width="2" vector-effect="non-scaling-stroke"/>
    <path d="${lin(2)}" fill="none" stroke="var(--gast)" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
}

function kiBox(ki, sp) {
  if (!ki) return "";
  const infos = (ki.infos || []).map((i) => {
    const wer = i.team === "heim" ? team(sp.heim).kurz : i.team === "gast" ? team(sp.gast).kurz : "";
    const quellen = (i.quellen || []).map((q) => `<a class="quelle" href="${esc(sicherUrl(q.link))}" target="_blank" rel="noopener noreferrer nofollow" title="${esc(q.titel)}">${esc(q.quelle || "Quelle")}</a>`).join(", ");
    return `<li>${wer ? `<b>${esc(wer)}:</b> ` : ""}${esc(i.text)} <span class="muted">(${quellen})</span></li>`;
  }).join("");
  return `<div class="ki-box"><h4>KI-Einschätzung <span class="badge">${esc(ki.anbieter || "KI")}</span></h4>
    ${ki.vorschau ? `<div>${esc(ki.vorschau)}</div>` : ""}
    ${ki.tipp ? `<div style="margin-top:6px">KI-Tipp: <b>${ki.tipp[0]}:${ki.tipp[1]}</b>${ki.p ? ` <span class="muted small">(${pct(ki.p[0])} / ${pct(ki.p[1])} / ${pct(ki.p[2])})</span>` : ""}</div>` : ""}
    ${infos ? `<ul>${infos}</ul>` : `<div class="muted small">Keine belegten Meldungen gefunden.</div>`}
    <div class="muted small" style="margin-top:4px">Automatisch erstellt, Stand ${esc(datum(ki.stand))}. Nur Aussagen mit Quelle werden angezeigt.</div></div>`;
}

function spielKarte(sp) {
  const h = team(sp.heim), g = team(sp.gast);
  const p = sp.prognose;
  const vorher = sp.prognose_vorher;
  const live = sp.live ? liveWahrscheinlichkeit(sp) : null;
  let badges = "";
  if (sp.live) badges += `<span class="badge live">Live ~${Math.round(live?.minute ?? 0)}'</span>`;
  else if (sp.beendet) badges += `<span class="badge">Beendet</span>`;
  if (sp.ki) badges += ` <span class="badge">KI</span>`;
  let mitte;
  if (sp.erg && (sp.beendet || sp.live)) mitte = `<div class="ergebnis">${sp.erg[0]}:${sp.erg[1]}</div>`;
  else if (p) mitte = `<div class="ergebnis muted">–:–</div>`;
  else mitte = `<div class="ergebnis muted">–:–</div>`;

  let unten = "";
  if (live) {
    unten += balken(live.p) + `<div class="tipp" style="margin-top:6px">Vor dem Spiel: Heimsieg ${pct(p.p[0])} · Tipp <b>${p.tipp[0]}:${p.tipp[1]}</b></div>`;
  } else if (p && !sp.beendet) {
    const tipps = [`Tipp <b>${p.tipp[0]}:${p.tipp[1]}</b>`];
    if (sp.ki?.tipp) tipps.push(`KI <b>${sp.ki.tipp[0]}:${sp.ki.tipp[1]}</b>`);
    unten += balken(p.p) + `<div class="tipp" style="margin-top:6px">${tipps.join(" · ")}</div>`;
  } else if (vorher && sp.erg) {
    const ep = vorher.endprognose;
    const treffer = (t) => {
      if (!t) return "";
      const ok = tendenz(t[0], t[1]) === tendenz(sp.erg[0], sp.erg[1]);
      const exakt = t[0] === sp.erg[0] && t[1] === sp.erg[1];
      return `<span class="badge ${ok ? "ok" : "falsch"}">${exakt ? "exakt ✓" : ok ? "Tendenz ✓" : "✗"}</span>`;
    };
    unten += (ep?.p ? balken(ep.p) : "") +
      `<div class="tipp" style="margin-top:6px">Prognose vorher: <b>${ep.tipp[0]}:${ep.tipp[1]}</b> ${treffer(ep.tipp)}` +
      (vorher.ki?.tipp ? ` · KI <b>${vorher.ki.tipp[0]}:${vorher.ki.tipp[1]}</b> ${treffer(vorher.ki.tipp)}` : "") + `</div>`;
  }

  // Details
  const teile = [];
  if (live && ZUSTAND.liveVerlauf[sp.id]?.length > 1) {
    teile.push(`<div><h4>Verlauf der Siegchancen (seit Öffnen der Seite)</h4>${sparkline(ZUSTAND.liveVerlauf[sp.id])}</div>`);
  }
  if (sp.tore?.length) {
    teile.push(`<div><h4>Tore</h4><div class="tore-liste">${sp.tore.map((t) => `${t.minute ?? "?"}' ${esc(t.name || "?")} (${t.heim}:${t.gast})${t.elfmeter ? " Elfmeter" : ""}${t.eigentor ? " Eigentor" : ""}`).join(" · ")}</div></div>`);
  }
  if (p) {
    teile.push(`<div><h4>Wahrscheinlichste Ergebnisse</h4><div class="chips">${p.top.map((t) => `<span class="chip">${t.e} · ${pct(t.p)}</span>`).join("")}</div>
      <div class="small muted" style="margin-top:6px">Erwartete Tore ${zahl(p.lam, 2)} : ${zahl(p.mu, 2)} · Über 2,5 Tore ${pct(p.ueber25)}</div></div>`);
    let quellen = `<div class="grund"><span>Modell allein</span><span class="wert">${pct(p.p_modell[0])} / ${pct(p.p_modell[1])} / ${pct(p.p_modell[2])}</span></div>`;
    if (p.p_markt) quellen += `<div class="grund"><span>Buchmacher</span><span class="wert">${pct(p.p_markt[0])} / ${pct(p.p_markt[1])} / ${pct(p.p_markt[2])}</span></div>`;
    if (sp.ki?.p) quellen += `<div class="grund"><span>KI</span><span class="wert">${pct(sp.ki.p[0])} / ${pct(sp.ki.p[1])} / ${pct(sp.ki.p[2])}</span></div>`;
    teile.push(`<div><h4>Heim / Remis / Gast nach Quelle</h4>${quellen}</div>`);
    const gruende = (p.begruendung || []).map((g) => `<div class="grund"><span>${esc(g.text)}</span><span class="wert">${g.heimsieg_pp > 0 ? "+" : ""}${zahl(g.heimsieg_pp)} %-Pkt. Heimsieg</span></div>`).join("");
    const info = sp.info ? `<div class="small muted">Ruhetage vor dem Spiel: ${esc(h.kurz)} ${zahl(sp.info.ruhe_heim, 0)}, ${esc(g.kurz)} ${zahl(sp.info.ruhe_gast, 0)}</div>` : "";
    teile.push(`<div><h4>Begründung</h4>${gruende || `<div class="small">Reines Modell aus Angriffs- und Abwehrstärke, Heimvorteil und Liga-Niveau.</div>`}${info}</div>`);
  }
  const ausf = [[h, sp.heim], [g, sp.gast]].filter(([t]) => t.ausfaelle?.length);
  if (ausf.length) {
    teile.push(`<div><h4>Ausfälle (API-Football)</h4>${ausf.map(([t]) => `<div><b>${esc(t.kurz)}:</b> ${t.ausfaelle.map((a) => `${esc(a.name)}${a.grund ? ` (${esc(a.grund)})` : ""}`).join(", ")}</div>`).join("")}</div>`);
  }
  if (sp.ki) teile.push(kiBox(sp.ki, sp));

  const offen = ZUSTAND.offen.has(sp.id) ? " open" : "";
  return `<article class="card spiel" data-id="${sp.id}">
    <div class="kopf"><span class="small muted">${datum(sp.anstoss)}</span><span>${badges}</span></div>
    <div class="paarung">
      <div class="team">${logo(h)}<span>${esc(h.kurz)}</span></div>
      <div class="mitte">${mitte}</div>
      <div class="team gast"><span>${esc(g.kurz)}</span>${logo(g)}</div>
    </div>
    ${unten}
    ${teile.length ? `<details class="mehr"${offen}><summary>Details</summary><div class="mehr-inhalt">${teile.join("")}</div></details>` : ""}
  </article>`;
}

function zeichneSpieltag() {
  const d = daten();
  const st = ZUSTAND.spieltag[d.liga] ?? d.aktueller_spieltag;
  const spiele = d.spiele.filter((s) => s.st === st).sort((a, b) => a.anstoss.localeCompare(b.anstoss) || a.id - b.id);
  const live = spiele.some((s) => s.live);
  inhalt.innerHTML = `
    <div class="spieltag-nav">
      <button class="icon-btn" data-st="${st - 1}" ${st <= 1 ? "disabled" : ""} aria-label="Vorheriger Spieltag">‹ ${st - 1 >= 1 ? st - 1 : ""}</button>
      <h2>${st}. Spieltag ${live ? '<span class="badge live">Live</span>' : ""}</h2>
      <button class="icon-btn" data-st="${st + 1}" ${st >= d.spieltage ? "disabled" : ""} aria-label="Nächster Spieltag">${st + 1 <= d.spieltage ? st + 1 : ""} ›</button>
    </div>
    <div class="legende"><span><i style="background:var(--heim)"></i>Heimsieg</span><span><i style="background:var(--remis)"></i>Remis</span><span><i style="background:var(--gast)"></i>Gastsieg</span></div>
    <div class="spiele">${spiele.map(spielKarte).join("") || '<div class="leer card">Keine Spiele.</div>'}</div>`;
  inhalt.querySelectorAll("[data-st]").forEach((b) => b.addEventListener("click", () => {
    ZUSTAND.spieltag[d.liga] = +b.dataset.st; zeichne();
  }));
  inhalt.querySelectorAll("details.mehr").forEach((det) => det.addEventListener("toggle", () => {
    const id = +det.closest(".spiel").dataset.id;
    det.open ? ZUSTAND.offen.add(id) : ZUSTAND.offen.delete(id);
  }));
}

function zonenKlasse(d, platz) {
  const z = d.zonen;
  const oben = new Set([...(z["Aufstieg"] || []), ...(z["Champions League"] || []), ...(z["Meister"] || [])]);
  const mitte = new Set([...(z["Relegation"] || []), ...(z["Relegation oben"] || []), ...(z["Relegation unten"] || [])]);
  const unten = new Set(z["Abstieg"] || []);
  if (unten.has(platz)) return "zone-unten";
  if (mitte.has(platz)) return "zone-mitte";
  if (oben.has(platz)) return "zone-oben";
  return "";
}
const heat = (p) => `background: rgba(var(--seq-rgb), ${Math.min(0.85, p * 0.9).toFixed(3)}); color: ${p > 0.55 ? "#fff" : "inherit"}`;

function zeichneTabelle() {
  const d = daten();
  const tab = tabelleJetzt(d);
  const liveAktiv = d.spiele.some((s) => s.live);
  const sim = ZUSTAND.liveSim[d.liga] || d.simulation;
  const simLive = !!ZUSTAND.liveSim[d.liga] && liveAktiv;
  const zonen = Object.keys(d.zonen);
  const T = Object.keys(d.teams).length;
  const sortiert = Object.keys(sim).map(Number).sort((a, b) => sim[a].platz_schnitt - sim[b].platz_schnitt);

  const zeilenJetzt = tab.map((z) => `<tr class="${zonenKlasse(d, z.platz)}">
    <td>${z.platz}</td><td class="l teamzelle">${logo(team(z.id))}<span>${esc(team(z.id).kurz)}</span>${z.live ? ' <span class="badge live">Live</span>' : ""}</td>
    <td>${z.sp}</td><td>${z.s}</td><td>${z.u}</td><td>${z.n}</td><td>${z.tore}:${z.gegentore}</td><td>${z.tore - z.gegentore > 0 ? "+" : ""}${z.tore - z.gegentore}</td><td><b>${z.pkt}</b></td></tr>`).join("");

  const zeilenProg = sortiert.map((id, i) => {
    const s = sim[id];
    const spanne = s.punkte_p10 != null ? `<span class="muted small"> (${s.punkte_p10}–${s.punkte_p90})</span>` : "";
    const verteilung = `<div class="pos-heat" style="grid-template-columns: repeat(${T}, 1fr)" title="Platzverteilung 1 bis ${T}">${s.plaetze.map((p, r) => `<div style="${heat(Math.min(1, p * 2.2))}" data-tip="Platz ${r + 1}: ${pct(p, 1)}"></div>`).join("")}</div>`;
    return `<tr class="${zonenKlasse(d, i + 1)}"><td>${i + 1}</td>
      <td class="l teamzelle">${logo(team(id))}<span>${esc(team(id).kurz)}</span></td>
      <td>${zahl(s.punkte_schnitt, 1)}${spanne}</td>
      ${zonen.map((z) => `<td class="heat" style="${heat(s.zonen[z])}">${s.zonen[z] < 0.001 ? "–" : s.zonen[z] > 0.999 ? "✓" : pct(s.zonen[z], s.zonen[z] < 0.1 ? 1 : 0)}</td>`).join("")}
      <td class="l">${verteilung}</td></tr>`;
  }).join("");

  inhalt.innerHTML = `
    <div class="abschnitt"><h3>Tabellenprognose ${simLive ? '<span class="badge live">Live</span>' : ""}</h3>
      <span class="small muted">${simLive ? `Live neu simuliert (${LIVE_SIMULATIONEN.toLocaleString("de")} Durchläufe), ${ZUSTAND.liveStand.toLocaleTimeString("de")}` : `10.000 simulierte Restsaisons, Stand ${datum(d.stand)}`}</span></div>
    <p class="hinweis">Wahrscheinlichkeit für jede Zone am Saisonende. Rechts die Verteilung über alle Plätze (links = Platz 1); dunkler = wahrscheinlicher.</p>
    <div class="card tabelle-wrap"><table class="t">
      <thead><tr><th>#</th><th class="l">Team</th><th>Ø Pkt.</th>${zonen.map((z) => `<th>${esc(z)}</th>`).join("")}<th class="l">Plätze 1–${T}</th></tr></thead>
      <tbody>${zeilenProg}</tbody></table></div>
    <div class="abschnitt"><h3>Aktuelle Tabelle ${liveAktiv ? '<span class="badge live">Live</span>' : ""}</h3>
      <span class="small muted">${liveAktiv ? "laufende Spiele mit aktuellem Stand" : ""}</span></div>
    <div class="card tabelle-wrap"><table class="t">
      <thead><tr><th>#</th><th class="l">Team</th><th>Sp</th><th>S</th><th>U</th><th>N</th><th>Tore</th><th>Diff</th><th>Pkt</th></tr></thead>
      <tbody>${zeilenJetzt}</tbody></table></div>`;
  bindeTooltips(inhalt);
}

function eloChart(t) {
  const v = t.elo_verlauf;
  if (!v.length) return '<div class="muted small">Noch keine Spiele in dieser Saison.</div>';
  const W = 600, H = 220, L = 44, R = 10, O = 12, U = 26;
  const werte = v.map((x) => x.elo);
  let min = Math.min(...werte), max = Math.max(...werte);
  const pad = Math.max(15, (max - min) * 0.15); min -= pad; max += pad;
  const x = (i) => L + (v.length === 1 ? (W - L - R) / 2 : (i / (v.length - 1)) * (W - L - R));
  const y = (e) => O + (1 - (e - min) / (max - min)) * (H - O - U);
  const ticks = []; const schritt = Math.max(10, Math.round((max - min) / 4 / 10) * 10);
  for (let e = Math.ceil(min / schritt) * schritt; e <= max; e += schritt) ticks.push(e);
  const pfad = v.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.elo).toFixed(1)}`).join("");
  return `<div class="chart-box"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Elo-Verlauf von ${esc(t.name)}">
    ${ticks.map((e) => `<line x1="${L}" x2="${W - R}" y1="${y(e)}" y2="${y(e)}" stroke="var(--border)" stroke-width="1"/><text x="${L - 6}" y="${y(e) + 4}" text-anchor="end" font-size="11" fill="var(--text-3)">${e}</text>`).join("")}
    ${v.map((p, i) => (i % Math.ceil(v.length / 10) === 0 || i === v.length - 1) ? `<text x="${x(i)}" y="${H - 6}" text-anchor="middle" font-size="11" fill="var(--text-3)">${p.st}</text>` : "").join("")}
    <path d="${pfad}" fill="none" stroke="var(--heim)" stroke-width="2" stroke-linejoin="round"/>
    ${v.map((p, i) => `<circle cx="${x(i)}" cy="${y(p.elo)}" r="4" fill="var(--heim)" stroke="var(--surface)" stroke-width="2"/>
      <rect x="${x(i) - (W - L - R) / Math.max(v.length, 1) / 2}" y="${O}" width="${(W - L - R) / Math.max(v.length, 1)}" height="${H - O - U}" fill="transparent"
        data-tip="${esc(`${p.st}. Spieltag: ${p.heim ? "gegen" : "bei"} ${team(p.geg).kurz} ${p.tore} (${p.e}) · Elo ${Math.round(p.elo)}`)}"/>`).join("")}
  </svg></div><div class="small muted">Elo-Wert nach jedem Spiel (x-Achse: Spieltag). Fahre über die Punkte für Details.</div>`;
}

function zeichneTeams() {
  const d = daten();
  const ids = Object.keys(d.teams).map(Number).sort((a, b) => d.teams[b].elo - d.teams[a].elo);
  const form = (t) => t.elo_verlauf.slice(-5).map((v) => `<span class="${v.e}" title="${esc(`${v.st}. ST ${v.tore}`)}">${v.e}</span>`).join("");
  let detail = "";
  if (ZUSTAND.team && d.teams[ZUSTAND.team]) {
    const t = d.teams[ZUSTAND.team];
    const relA = t.angriff - d.liga_staerke.angriff, relD = t.abwehr - d.liga_staerke.abwehr;
    const proz = (x) => `${x >= 0 ? "+" : ""}${Math.round((Math.exp(x) - 1) * 100)} %`;
    const offen = d.spiele.filter((s) => !s.beendet && (s.heim === ZUSTAND.team || s.gast === ZUSTAND.team)).slice(0, 5);
    detail = `<section class="card detail">
      <div class="zeile">${logo(t)}<h3>${esc(t.name)}</h3><button class="icon-btn" id="team-zu">Schließen</button></div>
      <div class="staerke"><span>Elo <b>${t.elo}</b></span><span>Angriff ${proz(relA)} vs. Liga-Schnitt</span><span>Abwehr ${proz(relD)} (weniger Gegentore)</span></div>
      ${eloChart(t)}
      ${t.ausfaelle?.length ? `<div style="margin-top:10px"><b>Ausfälle:</b> ${t.ausfaelle.map((a) => esc(a.name)).join(", ")}</div>` : ""}
      ${offen.length ? `<div style="margin-top:12px"><b>Nächste Spiele</b>${offen.map((s) => {
        const heim = s.heim === ZUSTAND.team, geg = team(heim ? s.gast : s.heim);
        const p = s.prognose?.p; const eigen = p ? (heim ? p[0] : p[2]) : null;
        return `<div class="grund small"><span>${datum(s.anstoss)} · ${heim ? "gegen" : "bei"} ${esc(geg.kurz)}</span><span class="wert">Siegchance ${pct(eigen)}</span></div>`;
      }).join("")}</div>` : ""}
    </section>`;
  }
  inhalt.innerHTML = `${detail}
    <p class="hinweis">Sortiert nach Elo-Wert (gemeinsame Skala über alle drei Ligen; Bundesliga-Schnitt etwa 1500). Klick auf ein Team für den Verlauf.</p>
    <div class="team-grid">${ids.map((id) => {
      const t = d.teams[id];
      return `<button class="card team-karte" data-team="${id}"><div class="zeile">${logo(t)}<span class="name">${esc(t.kurz)}</span><span class="elo">${t.elo}</span></div>
        <div class="form">${form(t)}</div></button>`;
    }).join("")}</div>`;
  inhalt.querySelectorAll("[data-team]").forEach((b) => b.addEventListener("click", () => {
    ZUSTAND.team = +b.dataset.team; zeichne(); window.scrollTo({ top: 0, behavior: "smooth" });
  }));
  $("#team-zu")?.addEventListener("click", () => { ZUSTAND.team = null; zeichne(); });
  bindeTooltips(inhalt);
}

function kennzahlTabelle(zeilen) {
  const spalten = [["n", "Spiele", (x) => x], ["tendenz", "Tendenz", (x) => pct(x, 1)], ["differenz", "Tordiff.", (x) => pct(x, 1)],
    ["exakt", "Exakt", (x) => pct(x, 1)], ["punkte_schnitt", "Ø Punkte*", (x) => zahl(x, 2)], ["brier", "Brier**", (x) => zahl(x, 3)]];
  return `<div class="card tabelle-wrap"><table class="t"><thead><tr><th class="l">Variante</th>${spalten.map((s) => `<th>${s[1]}</th>`).join("")}</tr></thead>
    <tbody>${zeilen.filter(([, k]) => k && k.n).map(([name, k]) => `<tr><td class="l">${name}</td>${spalten.map(([f, , fn]) => `<td>${k[f] == null ? "–" : fn(k[f])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function zeichneTreffer() {
  const d = daten();
  const tq = ZUSTAND.treffer, bt = ZUSTAND.backtest;
  const NAMEN = { endprognose: "Angezeigte Prognose", modell: "Rechenmodell allein", markt: "Buchmacher", ki: "KI-Tipp", grundregel: "Immer „Heimsieg 2:1“" };
  let live = "";
  const tl = tq?.je_liga?.[d.liga] || {};
  if (tl.endprognose?.n) {
    live = `<div class="kpis">
      <div class="card kpi"><div class="wert">${pct(tl.endprognose.tendenz)}</div><div class="label">Tendenz richtig</div><div class="vergleich">Grundregel: ${pct(tl.grundregel?.tendenz)}</div></div>
      <div class="card kpi"><div class="wert">${pct(tl.endprognose.exakt)}</div><div class="label">Ergebnis exakt</div><div class="vergleich">${tl.endprognose.n} Spiele</div></div>
      ${tl.ki?.n ? `<div class="card kpi"><div class="wert">${pct(tl.ki.tendenz)}</div><div class="label">KI-Tipp Tendenz</div><div class="vergleich">Modell: ${pct(tl.modell?.tendenz)} · ${tl.ki.n} Spiele</div></div>` : ""}
    </div><div style="margin-top:10px">${kennzahlTabelle(Object.entries(NAMEN).map(([k, n]) => [n, tl[k]]))}</div>`;
  } else {
    live = `<div class="card leer">Noch keine eingefrorenen Prognosen mit Ergebnis. Ab jetzt wird vor jedem Anpfiff gespeichert, was das Tool tippt – die Statistik wächst mit jedem Spieltag.</div>`;
  }
  let rueck = "";
  const bl = bt?.je_liga?.[d.liga];
  if (bl) {
    const e = bt.einfluesse;
    const NAMEN_E = { ruhetage: "Ruhetage", nichts_eigen: "„nichts mehr zu spielen“ (eigene Tore)", nichts_gegner: "„nichts mehr zu spielen“ (Gegentore)" };
    const getestet = Object.entries(e.getestet).sort((a, b) => a[1] - b[1]).map(([k, v]) => `<div class="grund small"><span>${k === "ohne" ? "ohne Zusatz-Einflüsse" : k.split("+").map((x) => NAMEN_E[x] || x).join(" + ")}</span><span class="wert">${zahl(v, 4)}</span></div>`).join("");
    const gew = Object.entries(e.quoten_gewichte).map(([k, v]) => `<div class="grund small"><span>${Math.round(k * 100)} % Quoten</span><span class="wert">${zahl(v, 4)}</span></div>`).join("");
    rueck = `
      <p class="hinweis">Das Modell wurde für die Spielzeiten ${bt.saisons.map((s) => `${s}/${String((s + 1) % 100).padStart(2, "0")}`).join(", ")} Woche für Woche nachgerechnet – jeweils nur mit dem Wissen bis vor dem Spiel.</p>
      ${kennzahlTabelle([[NAMEN.endprognose, bl.modell], ["Modell ohne Einflüsse/Quoten", bl.nur_modell_ohne_einfluesse], ["Nur Buchmacher", bl.nur_markt], [NAMEN.grundregel, bl.grundregel]])}
      <div class="abschnitt"><h3>Welche Einflüsse helfen?</h3><span class="small muted">Log-Loss, kleiner = besser · Stand ${esc(datum(bt.stand))}</span></div>
      <div class="kpis">
        <div class="card kpi"><div class="label" style="margin-bottom:6px">Spielplan-Einflüsse (alle Ligen)</div>${getestet}
          <div class="small" style="margin-top:6px">Aktiv: <b>${e.aktiv.length ? e.aktiv.map((x) => NAMEN_E[x] || x).join(", ") : "keine"}</b> (nur bei Verbesserung ≥ 0,001)</div></div>
        <div class="card kpi"><div class="label" style="margin-bottom:6px">Mischung mit Buchmacherquoten (${e.spiele_mit_quoten} Spiele, 1./2. BL)</div>${gew}
          <div class="small" style="margin-top:6px">Gewählt: <b>${Math.round(e.quoten_gewicht * 100)} %</b> Quoten</div></div>
      </div>`;
  }
  inhalt.innerHTML = `
    <div class="abschnitt"><h3>Echte Prognosen (vor Anpfiff gespeichert)</h3><span class="small muted">${esc(d.name)}</span></div>
    ${live}
    <div class="abschnitt"><h3>Rückwärtstest</h3><span class="small muted">${esc(d.name)}</span></div>
    ${rueck || '<div class="card leer">Noch kein Rückwärtstest vorhanden.</div>'}
    <p class="small muted" style="margin-top:12px">* Kicktipp-Punkte: 4 exakt, 3 richtige Tordifferenz, 2 richtige Tendenz. ** Brier-Score: Güte der Prozentangaben, 0 = perfekt, kleiner = besser.</p>`;
}

function zeichne() {
  if (!daten()) return;
  document.querySelectorAll("#ligen button").forEach((b) => b.setAttribute("aria-selected", b.dataset.l === ZUSTAND.liga));
  document.querySelectorAll("#ansichten button").forEach((b) => b.setAttribute("aria-selected", b.dataset.a === ZUSTAND.ansicht));
  ({ spieltag: zeichneSpieltag, tabelle: zeichneTabelle, teams: zeichneTeams, treffer: zeichneTreffer })[ZUSTAND.ansicht]();
}

/* Tooltips für Elemente mit data-tip */
function bindeTooltips(root) {
  const tip = $("#tooltip");
  root.querySelectorAll("[data-tip]").forEach((el) => {
    el.addEventListener("mouseenter", () => { tip.textContent = el.dataset.tip; tip.style.display = "block"; });
    el.addEventListener("mousemove", (e) => { tip.style.left = e.pageX + "px"; tip.style.top = e.pageY - 8 + "px"; });
    el.addEventListener("mouseleave", () => { tip.style.display = "none"; });
  });
}

/* ---------- Start ---------- */
function speichereWahl() {
  try { localStorage.setItem("fp-wahl", JSON.stringify({ liga: ZUSTAND.liga, ansicht: ZUSTAND.ansicht })); } catch { /* egal */ }
}
async function wechsleLiga(liga) {
  ZUSTAND.liga = liga; ZUSTAND.team = null; speichereWahl();
  if (!ZUSTAND.daten[liga]) {
    inhalt.innerHTML = '<div class="leer">lädt …</div>';
    ZUSTAND.daten[liga] = await ladeJson(`data/${liga}.json`);
  }
  zeichne();
  liveAktualisieren();
}

async function start() {
  // Hell/Dunkel
  try { const th = localStorage.getItem("fp-theme"); if (th) document.documentElement.dataset.theme = th; } catch { /* egal */ }
  $("#theme").addEventListener("click", () => {
    const dunkel = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = dunkel ? "light" : "dark";
    try { localStorage.setItem("fp-theme", document.documentElement.dataset.theme); } catch { /* egal */ }
  });
  try {
    ZUSTAND.index = await ladeJson("data/index.json");
  } catch (e) {
    inhalt.innerHTML = `<div class="card leer">Keine Daten gefunden. Bitte zuerst <code>python run.py</code> ausführen.</div>`;
    return;
  }
  ladeJson("data/trefferquote.json").then((x) => { ZUSTAND.treffer = x; if (ZUSTAND.ansicht === "treffer") zeichne(); }).catch(() => {});
  ladeJson("data/backtest.json").then((x) => { ZUSTAND.backtest = x; if (ZUSTAND.ansicht === "treffer") zeichne(); }).catch(() => {});
  $("#stand").textContent = `Stand ${datum(ZUSTAND.index.stand)} · Saison ${ZUSTAND.index.saison}/${String((ZUSTAND.index.saison + 1) % 100).padStart(2, "0")}`;
  $("#ligen").innerHTML = ZUSTAND.index.ligen.map((l) => `<button data-l="${l.id}" role="tab">${esc(l.name)}</button>`).join("");
  $("#ligen").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) wechsleLiga(b.dataset.l); });
  $("#ansichten").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    ZUSTAND.ansicht = b.dataset.a; speichereWahl(); zeichne();
  });
  let wahl = {};
  try { wahl = JSON.parse(localStorage.getItem("fp-wahl") || "{}"); } catch { /* egal */ }
  if (wahl.ansicht) ZUSTAND.ansicht = wahl.ansicht;
  const liga = ZUSTAND.index.ligen.some((l) => l.id === wahl.liga) ? wahl.liga : ZUSTAND.index.ligen[0].id;
  await wechsleLiga(liga);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) liveAktualisieren(); });
}
start();
