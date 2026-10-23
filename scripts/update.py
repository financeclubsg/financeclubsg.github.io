"""Calcule la performance hypothétique des stock pitchs du club et écrit site/data.json.

Hypothèse : à chaque pitch, on investit MISE au prix annoncé dans le pitch (« share price »),
dans le sens recommandé (BUY = achat, SELL = vente à découvert), et on garde la position
jusqu'à la fin de l'horizon annoncé. Le jour de référence est la dernière clôture avant le
post : c'est à partir de ce jour qu'on suit le cours, et l'indice de référence du pitch,
pour comparer.
"""
import csv
import json
import math
import sys
from datetime import datetime, time, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
PITCHES = ROOT / "data" / "pitches.csv"
SORTIE = ROOT / "site" / "data.json"
MISE = 1000.0

# Heure de clôture locale de chaque bourse, pour savoir si un post est sorti avant ou après la clôture.
CLOTURES = {
    "Europe/Berlin": time(17, 30),
    "Europe/Paris": time(17, 30),
    "Europe/Amsterdam": time(17, 30),
    "Europe/Zurich": time(17, 30),
    "Europe/London": time(16, 30),
    "America/New_York": time(16, 0),
    "Asia/Singapore": time(17, 0),
    "Asia/Hong_Kong": time(16, 0),
    "Asia/Tokyo": time(15, 30),
}

NOMS_INDICES = {
    "^GDAXI": "DAX",
    "^STI": "STI",
    "^GSPC": "S&P 500",
    "^NDX": "Nasdaq 100",
    "^FTSE": "FTSE 100",
    "^FCHI": "CAC 40",
    "^SSMI": "SMI",
    "^HSI": "Hang Seng",
    "^N225": "Nikkei 225",
    "^AEX": "AEX",
}


def pct(x):
    return None if x is None or not math.isfinite(x) else round(x * 100, 2)


def prix(x):
    return None if x is None or not math.isfinite(x) else round(float(x), 2)


def historique(ticker, debut):
    h = yf.Ticker(ticker).history(start=debut, auto_adjust=False, actions=False)
    h = h.dropna(subset=["Close"])
    if h.empty:
        raise RuntimeError(f"no price data found for {ticker}")
    return h


def derniere_cloture_avant(h, publie_le):
    """Dernière séance clôturée avant la publication du post : le jour de référence du pitch."""
    heure = CLOTURES.get(str(h.index.tz), time(16, 0))
    reference = None
    for ts in h.index:
        cloture = ts.normalize().replace(hour=heure.hour, minute=heure.minute)
        if cloture >= publie_le:
            break
        reference = ts
    return reference


def analyser(p, maintenant):
    publie_le = pd.Timestamp(p["publie_le"])
    sens = 1 if p["position"].upper() == "BUY" else -1
    cible = float(p["prix_cible"])
    resultat = {
        "entreprise": p["entreprise"],
        "ticker": p["ticker"],
        "bourse": p["bourse"],
        "position": p["position"].upper(),
        "publie_le": publie_le.strftime("%Y-%m-%d"),
        "prix_pitch": float(p["prix_pitch"]),
        "prix_cible": cible,
        "devise": p["devise"],
        "horizon_mois": int(p["horizon_mois"]),
        "indice": NOMS_INDICES.get(p["indice"], p["indice"]),
        "lien": p["lien"],
    }

    debut = (publie_le - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    h = historique(p["ticker"], debut)
    reference = derniere_cloture_avant(h, publie_le)
    if reference is None:
        raise RuntimeError(f"no close found before the post for {p['ticker']}")

    fin_horizon = publie_le.tz_convert(h.index.tz) + pd.DateOffset(months=resultat["horizon_mois"])
    h = h[(h.index >= reference) & (h.index <= fin_horizon)]

    # L'indice peut avoir des jours fériés différents : on l'aligne jour par jour sur l'action.
    idx = historique(p["indice"], debut)["Adj Close"]
    idx.index = idx.index.date
    jours = [ts.date() for ts in h.index]
    idx = idx.reindex(sorted(set(idx.index) | set(jours))).ffill().reindex(jours)

    # On part du prix du pitch, puis on suit le cours ajusté (dividendes compris).
    valeur = h["Close"].iloc[0] / resultat["prix_pitch"] * h["Adj Close"].values / h["Adj Close"].iloc[0]
    r = sens * (valeur - 1)
    b = idx.values / idx.iloc[0] - 1

    apres = h.iloc[1:]  # les séances qui suivent le post
    atteinte = apres[apres["High"] >= cible] if sens == 1 else apres[apres["Low"] <= cible]
    termine = maintenant >= fin_horizon

    return {
        **resultat,
        "statut": "closed" if termine else "open",
        "date_entree": jours[0].isoformat(),
        "prix_entree": resultat["prix_pitch"],
        "fin_horizon": fin_horizon.strftime("%Y-%m-%d"),
        "dernier_jour": jours[-1].isoformat(),
        "dernier_prix": prix(h["Close"].iloc[-1]),
        "perf": pct(r[-1]),
        "perf_indice": pct(b[-1]),
        "ecart": pct(r[-1] - sens * b[-1]),
        "cible_atteinte_le": atteinte.index[0].strftime("%Y-%m-%d") if len(atteinte) else None,
        "serie": [
            {"d": j.isoformat(), "prix": prix(c), "r": pct(ri), "b": pct(bi)}
            for j, c, ri, bi in zip(jours, h["Close"].values, r, b)
        ],
    }


def portefeuille(pitches):
    """MISE investie dans chaque pitch à sa date d'entrée, et la même chose dans son indice."""
    actifs = [p for p in pitches if p["serie"]]
    dates = sorted({pt["d"] for p in actifs for pt in p["serie"]})
    points = []
    for d in dates:
        investi = club = indice = 0.0
        for p in actifs:
            passes = [pt for pt in p["serie"] if pt["d"] <= d]
            if not passes:
                continue
            dernier = passes[-1]  # après la fin de l'horizon, la valeur reste figée
            sens = 1 if p["position"] == "BUY" else -1
            investi += MISE
            club += MISE * (1 + dernier["r"] / 100)
            indice += MISE * (1 + sens * dernier["b"] / 100)
        points.append({"d": d, "club": pct(club / investi - 1), "indice": pct(indice / investi - 1)})
    return points


def main():
    maintenant = pd.Timestamp(datetime.now(timezone.utc))
    with open(PITCHES, newline="", encoding="utf-8") as f:
        lignes = list(csv.DictReader(f))

    pitches, erreurs = [], []
    for p in lignes:
        try:
            pitches.append(analyser(p, maintenant))
        except Exception as e:  # un ticker en panne ne doit pas bloquer tout le site
            erreurs.append(f"{p['entreprise']} ({p['ticker']}): {e}")
            print("ERROR", erreurs[-1], file=sys.stderr)

    if lignes and not pitches:
        sys.exit("No prices fetched: data.json left unchanged.")

    pitches.sort(key=lambda p: p["publie_le"], reverse=True)
    actifs = [p for p in pitches if p["serie"]]
    courbe = portefeuille(pitches)
    resume = {
        "nb_pitchs": len(actifs),
        "perf_club": courbe[-1]["club"] if courbe else None,
        "perf_indice": courbe[-1]["indice"] if courbe else None,
        "gagnants": sum(1 for p in actifs if p["perf"] > 0),
        "battent_indice": sum(1 for p in actifs if p["ecart"] > 0),
        "cibles_atteintes": sum(1 for p in actifs if p["cible_atteinte_le"]),
    }

    SORTIE.parent.mkdir(exist_ok=True)
    SORTIE.write_text(
        json.dumps(
            {
                "mis_a_jour": maintenant.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "mise": MISE,
                "resume": resume,
                "portefeuille": courbe,
                "pitchs": pitches,
                "erreurs": erreurs,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"{len(actifs)} pitch(es) tracked, {len(erreurs)} error(s) -> {SORTIE}")


if __name__ == "__main__":
    main()
