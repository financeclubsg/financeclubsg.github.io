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
import time as time_mod
import urllib.request
from bisect import bisect_right
from datetime import datetime, time, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
PITCHES = ROOT / "data" / "pitches.csv"
SORTIE = ROOT / "site" / "data.json"
# data.json n'est plus versionné : la version en ligne sert de secours si Yahoo ne répond pas pour un ticker.
DATA_EN_LIGNE = "https://financeclubsg.github.io/data.json"
MISE = 1000.0
# Un seul indice de référence pour tout le club, dividendes réinvestis comme pour les actions ;
# l'indice du marché local reste affiché en complément.
BENCHMARK = "^SP500TR"
# Au-delà de cet écart entre le prix du pitch et la clôture de référence, on le signale.
SEUIL_ECART_PRIX = 0.01

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
    "^SP500TR": "S&P 500",
    "^NDX": "Nasdaq 100",
    "^FTSE": "FTSE 100",
    "^FCHI": "CAC 40",
    "^SSMI": "SMI",
    "^HSI": "Hang Seng",
    "^N225": "Nikkei 225",
    "^AEX": "AEX",
}
# Indices locaux qui n'ont rien à ajouter au benchmark.
COMME_BENCHMARK = {"^GSPC", BENCHMARK}


def pct(x):
    return None if x is None or not math.isfinite(x) else round(x * 100, 2)


def prix(x):
    return None if x is None or not math.isfinite(x) else round(float(x), 2)


def heure_cloture(h):
    return CLOTURES.get(str(h.index.tz), time(16, 0))


_cache = {}


def historique(ticker, debut, essais=4):
    """Cours quotidiens depuis debut. Un même ticker (l'indice surtout) n'est téléchargé qu'une fois par exécution."""
    deja = _cache.get(ticker)
    if deja is not None and deja[0] <= debut:
        h = deja[1]
        return h[h.index >= pd.Timestamp(debut).tz_localize(h.index.tz)]
    # Yahoo renvoie parfois une réponse vide ou une erreur passagère : on réessaie avant d'abandonner.
    for essai in range(essais):
        try:
            h = yf.Ticker(ticker).history(start=debut, auto_adjust=False, actions=False)
            h = h.dropna(subset=["Close"])
            if not h.empty:
                _cache[ticker] = (debut, h)
                return h
        except Exception:
            if essai == essais - 1:
                raise
        time_mod.sleep(5 * (essai + 1))
    raise RuntimeError(f"no price data found for {ticker}")


def derniere_cloture_avant(h, publie_le):
    """Dernière séance clôturée avant la publication du post : le jour de référence du pitch."""
    heure = heure_cloture(h)
    reference = None
    for ts in h.index:
        cloture = ts.normalize().replace(hour=heure.hour, minute=heure.minute)
        if cloture >= publie_le:
            break
        reference = ts
    return reference


def seance_en_cours(h, maintenant):
    """Vrai si la dernière ligne est la séance du jour et que la bourse n'a pas encore fermé (cours intraday)."""
    dernier = h.index[-1]
    heure = heure_cloture(h)
    cloture = dernier.normalize().replace(hour=heure.hour, minute=heure.minute)
    return maintenant.tz_convert(h.index.tz).normalize() == dernier.normalize() and maintenant < cloture


def jours_sans_prix(dernier_jour, maintenant):
    """Jours ouvrés passés sans cours depuis dernier_jour (aujourd'hui exclu : la séance peut ne pas être finie)."""
    return max(0, int(np.busday_count(dernier_jour, maintenant.date())) - 1)


def rendement_indice(ticker, debut, jours):
    """Rendement d'un indice depuis le premier jour, aligné jour par jour sur l'action (jours fériés différents)."""
    idx = historique(ticker, debut)["Adj Close"]
    idx.index = idx.index.date
    idx = idx.reindex(sorted(set(idx.index) | set(jours))).ffill().reindex(jours)
    return idx.values / idx.iloc[0] - 1


def analyser(p, maintenant):
    publie_le = pd.Timestamp(p["publie_le"])
    sens = 1 if p["position"].upper() == "BUY" else -1
    cible = float(p["prix_cible"])
    resultat = {
        "entreprise": p["entreprise"],
        "ticker": p["ticker"],
        "bourse": p["bourse"],
        "analyste": (p.get("analyste") or "").strip() or None,
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

    jours = [ts.date() for ts in h.index]

    # On part du prix du pitch, puis on suit le cours ajusté (dividendes compris).
    valeur = h["Close"].iloc[0] / resultat["prix_pitch"] * h["Adj Close"].values / h["Adj Close"].iloc[0]
    r = sens * (valeur - 1)
    b = rendement_indice(BENCHMARK, debut, jours)
    local = rendement_indice(p["indice"], debut, jours) if p["indice"] not in COMME_BENCHMARK else None

    apres = h.iloc[1:]  # les séances qui suivent le post
    atteinte = apres[apres["High"] >= cible] if sens == 1 else apres[apres["Low"] <= cible]
    termine = maintenant >= fin_horizon

    # Le prix annoncé dans le pitch peut différer de la clôture de référence (cours de la veille, coquille…).
    cloture_reference = float(h["Close"].iloc[0])
    ecart_prix = resultat["prix_pitch"] / cloture_reference - 1
    if abs(ecart_prix) > SEUIL_ECART_PRIX:
        print(f"WARNING {p['ticker']}: pitch price {resultat['prix_pitch']} is {ecart_prix:+.1%} "
              f"from the reference close {cloture_reference:.2f}", file=sys.stderr)

    return {
        **resultat,
        "statut": "closed" if termine else "open",
        "date_entree": jours[0].isoformat(),
        "prix_entree": resultat["prix_pitch"],
        "cloture_reference": prix(cloture_reference),
        "ecart_prix_pitch": pct(ecart_prix),
        "fin_horizon": fin_horizon.strftime("%Y-%m-%d"),
        "dernier_jour": jours[-1].isoformat(),
        "dernier_prix": prix(h["Close"].iloc[-1]),
        "intraday": not termine and seance_en_cours(h, maintenant),
        "jours_sans_prix": 0 if termine else jours_sans_prix(jours[-1], maintenant),
        "perf": pct(r[-1]),
        "perf_indice": pct(b[-1]),
        "ecart": pct(r[-1] - sens * b[-1]),
        "perf_indice_local": pct(local[-1]) if local is not None else None,
        "ecart_local": pct(r[-1] - sens * local[-1]) if local is not None else None,
        "cible_atteinte_le": atteinte.index[0].strftime("%Y-%m-%d") if len(atteinte) else None,
        "serie": [
            {"d": j.isoformat(), "prix": prix(c), "r": pct(ri), "b": pct(bi)}
            for j, c, ri, bi in zip(jours, h["Close"].values, r, b)
        ],
    }


def portefeuille(pitches):
    """Rendement cumulé pondéré par le temps d'un portefeuille équipondéré des pitchs en cours.

    Chaque jour, on fait la moyenne des rendements du jour des positions ouvertes, puis on
    enchaîne ces rendements : l'arrivée d'un nouveau pitch ne dilue plus la courbe. La même chose
    est faite avec l'indice, dans le même sens que chaque pitch, pour comparer.
    """
    actifs = [p for p in pitches if p["serie"]]
    dates = sorted({pt["d"] for p in actifs for pt in p["serie"]})

    jours = {id(p): [pt["d"] for pt in p["serie"]] for p in actifs}

    def valeur(p, d, cle):
        # Valeur de la position (1 = MISE au prix du pitch), figée au dernier cours connu.
        if d <= p["date_entree"]:
            return 1.0
        x = p["serie"][bisect_right(jours[id(p)], d) - 1][cle] / 100
        sens = 1 if cle == "r" or p["position"] == "BUY" else -1
        return 1 + sens * x

    points, club, indice = [], 1.0, 1.0
    for i, d in enumerate(dates):
        if i:
            veille = dates[i - 1]
            # Une position compte de la clôture de son jour d'entrée jusqu'à la fin de son horizon.
            ouvertes = [p for p in actifs if p["date_entree"] < d <= p["fin_horizon"]]
            if ouvertes:
                club *= 1 + sum(valeur(p, d, "r") / valeur(p, veille, "r") - 1 for p in ouvertes) / len(ouvertes)
                indice *= 1 + sum(valeur(p, d, "b") / valeur(p, veille, "b") - 1 for p in ouvertes) / len(ouvertes)
        points.append({"d": d, "club": pct(club - 1), "indice": pct(indice - 1)})
    return points


def charger_precedent():
    """Dernière version publiée de data.json, pour garder un pitch quand Yahoo ne répond pas pour lui."""
    try:
        with urllib.request.urlopen(DATA_EN_LIGNE, timeout=20) as reponse:
            return json.load(reponse)
    except Exception as e:
        print(f"WARNING previous data.json unavailable: {e}", file=sys.stderr)
        return {}


def main():
    maintenant = pd.Timestamp(datetime.now(timezone.utc))
    with open(PITCHES, newline="", encoding="utf-8") as f:
        lignes = list(csv.DictReader(f))

    precedents = None
    pitches, erreurs = [], []
    for p in lignes:
        try:
            pitches.append(analyser(p, maintenant))
        except Exception as e:  # un ticker en panne ne doit pas bloquer tout le site
            erreurs.append(f"{p['entreprise']} ({p['ticker']}): {e}")
            print("ERROR", erreurs[-1], file=sys.stderr)
            if precedents is None:
                precedents = {(q["ticker"], q["publie_le"]): q for q in charger_precedent().get("pitchs", [])}
            ancien = precedents.get((p["ticker"], pd.Timestamp(p["publie_le"]).strftime("%Y-%m-%d")))
            if ancien:
                # On garde les derniers chiffres connus plutôt que de faire disparaître le pitch.
                pitches.append({**ancien, "donnees_anciennes": True})

    if lignes and not pitches:
        sys.exit("No prices fetched: data.json left unchanged.")

    pitches.sort(key=lambda p: p["publie_le"], reverse=True)
    actifs = [p for p in pitches if p["serie"]]
    courbe = portefeuille(pitches)
    resume = {
        "nb_pitchs": len(actifs),
        "perf_club": courbe[-1]["club"] if courbe else None,
        "perf_indice": courbe[-1]["indice"] if courbe else None,
        # Moyenne simple des pitchs, chacun depuis sa propre date d'entrée.
        "perf_moyenne": pct(sum(p["perf"] for p in actifs) / len(actifs) / 100) if actifs else None,
        "ecart_moyen": pct(sum(p["ecart"] for p in actifs) / len(actifs) / 100) if actifs else None,
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
                "benchmark": BENCHMARK,
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
