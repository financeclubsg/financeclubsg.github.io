"""Tests du calcul, sans appel réseau : python -m unittest discover tests"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import update  # noqa: E402


def pitch(entree, serie, position="BUY", fin="2099-01-01", **extra):
    return {"date_entree": entree, "fin_horizon": fin, "position": position,
            "serie": [{"d": d, "r": r, "b": b} for d, r, b in serie], **extra}


def cours(jours, closes, tz="America/New_York"):
    index = pd.DatetimeIndex(pd.to_datetime(jours)).tz_localize(tz)
    return pd.DataFrame({"Close": closes, "Adj Close": closes, "High": closes, "Low": closes}, index=index)


class Portefeuille(unittest.TestCase):
    def test_un_seul_pitch_suit_son_rendement(self):
        p = pitch("2026-01-01", [("2026-01-01", 0, 0), ("2026-01-02", 10, 2), ("2026-01-03", 21, 3)])
        courbe = update.portefeuille([p])
        self.assertEqual([c["club"] for c in courbe], [0, 10, 21])
        self.assertEqual([c["indice"] for c in courbe], [0, 2, 3])

    def test_un_nouveau_pitch_ne_dilue_pas_la_courbe(self):
        a = pitch("2026-01-01", [("2026-01-01", 0, 0), ("2026-01-02", 10, 0), ("2026-01-03", 10, 0)])
        b = pitch("2026-01-02", [("2026-01-02", 0, 0), ("2026-01-03", 0, 0)])
        courbe = update.portefeuille([a, b])
        # Le jour où B arrive à 0 %, le portefeuille reste à +10 % (l'ancien calcul affichait +5 %).
        self.assertEqual(courbe[-1]["club"], 10)

    def test_rendements_enchaines(self):
        a = pitch("2026-01-01", [("2026-01-01", 0, 0), ("2026-01-02", 10, 0), ("2026-01-03", 21, 0)])
        b = pitch("2026-01-02", [("2026-01-02", 0, 0), ("2026-01-03", -10, 0)])
        # Jour 3 : A fait +10 %, B −10 %, moyenne 0 % : on reste à +10 %.
        self.assertEqual(update.portefeuille([a, b])[-1]["club"], 10)

    def test_vente_a_decouvert_contre_indice(self):
        p = pitch("2026-01-01", [("2026-01-01", 0, 0), ("2026-01-02", 5, -5)], position="SELL")
        courbe = update.portefeuille([p])
        self.assertEqual(courbe[-1]["club"], 5)
        self.assertEqual(courbe[-1]["indice"], 5)  # vendre l'indice quand il baisse de 5 % rapporte 5 %

    def test_ecart_au_prix_du_pitch_compte_le_premier_jour(self):
        # Clôture de référence 2 % au-dessus du prix du pitch : r vaut déjà 2 le jour d'entrée.
        p = pitch("2026-01-01", [("2026-01-01", 2, 0), ("2026-01-02", 2, 0)])
        self.assertEqual(update.portefeuille([p])[-1]["club"], 2)

    def test_pitch_termine_sort_du_portefeuille(self):
        a = pitch("2026-01-01", [("2026-01-01", 0, 0), ("2026-01-02", 10, 0)], fin="2026-01-02")
        b = pitch("2026-01-01", [("2026-01-01", 0, 0), ("2026-01-02", 0, 0), ("2026-01-03", 20, 0)])
        courbe = update.portefeuille([a, b])
        self.assertEqual(courbe[1]["club"], 5)
        self.assertEqual(courbe[2]["club"], 26)  # 1.05 × 1.20 : seul B compte le jour 3

    def test_cours_manquant_reste_fige(self):
        a = pitch("2026-01-01", [("2026-01-01", 0, 0), ("2026-01-03", 10, 0)])  # pas de cours le 2
        b = pitch("2026-01-01", [("2026-01-01", 0, 0), ("2026-01-02", 0, 0), ("2026-01-03", 0, 0)])
        self.assertEqual([c["club"] for c in update.portefeuille([a, b])], [0, 0, 5])


class Dates(unittest.TestCase):
    def test_jours_sans_prix(self):
        maintenant = pd.Timestamp("2026-10-28T02:00:00Z")  # mercredi
        self.assertEqual(update.jours_sans_prix(pd.Timestamp("2026-10-27").date(), maintenant), 0)
        self.assertEqual(update.jours_sans_prix(pd.Timestamp("2026-10-26").date(), maintenant), 1)
        lundi = pd.Timestamp("2026-10-26T02:00:00Z")
        self.assertEqual(update.jours_sans_prix(pd.Timestamp("2026-10-23").date(), lundi), 0)  # week-end

    def test_seance_en_cours(self):
        h = cours(["2026-10-27", "2026-10-28"], [10, 11])
        self.assertTrue(update.seance_en_cours(h, pd.Timestamp("2026-10-28T17:00:00Z")))   # 13:00 à New York
        self.assertFalse(update.seance_en_cours(h, pd.Timestamp("2026-10-28T21:30:00Z")))  # après 16:00
        self.assertFalse(update.seance_en_cours(h, pd.Timestamp("2026-10-29T15:00:00Z")))  # séance du 28 finie

    def test_derniere_cloture_avant(self):
        h = cours(["2026-10-26", "2026-10-27"], [10, 11])
        avant = update.derniere_cloture_avant(h, pd.Timestamp("2026-10-27T10:00:00Z"))
        apres = update.derniere_cloture_avant(h, pd.Timestamp("2026-10-27T21:00:00Z"))
        self.assertEqual(avant.date().isoformat(), "2026-10-26")
        self.assertEqual(apres.date().isoformat(), "2026-10-27")


class Telechargement(unittest.TestCase):
    def setUp(self):
        update._cache.clear()

    def test_un_ticker_telecharge_une_seule_fois(self):
        h = cours(["2026-10-20", "2026-10-21", "2026-10-22"], [1, 2, 3])
        with mock.patch.object(update.yf, "Ticker") as ticker:
            ticker.return_value.history.return_value = h
            update.historique("^SP500TR", "2026-10-20")
            plus_tard = update.historique("^SP500TR", "2026-10-21")
            self.assertEqual(ticker.call_count, 1)
            self.assertEqual(len(plus_tard), 2)
            update.historique("^SP500TR", "2026-10-01")  # plus ancien que le cache : on retélécharge
            self.assertEqual(ticker.call_count, 2)


class Main(unittest.TestCase):
    LIGNES = ("publie_le,entreprise,ticker,bourse,position,prix_pitch,prix_cible,devise,horizon_mois,indice,lien\n"
              "2026-10-20T10:00:00Z,Bonne,OK,X,BUY,10,12,USD,12,^GSPC,l\n"
              "2026-10-21T10:00:00Z,Panne,KO,X,BUY,10,12,USD,12,^GSPC,l\n")

    def lancer(self, precedent):
        dossier = Path(tempfile.mkdtemp())
        (dossier / "pitches.csv").write_text(self.LIGNES)

        def analyser(p, maintenant):
            if p["ticker"] == "KO":
                raise RuntimeError("no price data found for KO")
            return {"ticker": "OK", "publie_le": "2026-10-20", "position": "BUY", "perf": 5, "ecart": 1,
                    "cible_atteinte_le": None, "date_entree": "2026-10-19", "fin_horizon": "2027-10-20",
                    "serie": [{"d": "2026-10-19", "r": 0, "b": 0}, {"d": "2026-10-20", "r": 5, "b": 4}]}

        with mock.patch.multiple(update, PITCHES=dossier / "pitches.csv", SORTIE=dossier / "data.json",
                                 analyser=analyser, charger_precedent=lambda: precedent):
            update.main()
        return json.loads((dossier / "data.json").read_text())

    def test_un_ticker_en_panne_garde_ses_derniers_chiffres(self):
        ancien = {"ticker": "KO", "publie_le": "2026-10-21", "position": "BUY", "perf": -3, "ecart": -2,
                  "cible_atteinte_le": None, "date_entree": "2026-10-20", "fin_horizon": "2027-10-21",
                  "serie": [{"d": "2026-10-20", "r": 0, "b": 0}]}
        data = self.lancer({"pitchs": [ancien]})
        self.assertEqual([p["ticker"] for p in data["pitchs"]], ["KO", "OK"])
        self.assertTrue(data["pitchs"][0]["donnees_anciennes"])
        self.assertEqual(len(data["erreurs"]), 1)
        self.assertEqual(data["resume"]["perf_moyenne"], 1)

    def test_sans_version_precedente_le_pitch_manque(self):
        data = self.lancer({})
        self.assertEqual([p["ticker"] for p in data["pitchs"]], ["OK"])


class Cotation(unittest.TestCase):
    def test_complete_une_cloture_manquante(self):
        h = cours(["2026-10-26"], [100.0], tz="Europe/Berlin")
        h["Adj Close"] = 50.0  # facteur d'ajustement 0,5
        quand = pd.Timestamp("2026-10-27T15:30:00Z").timestamp()
        complet = update.completer(h, {"regularMarketPrice": 110.0, "regularMarketTime": quand})
        self.assertEqual([d.date().isoformat() for d in complet.index], ["2026-10-26", "2026-10-27"])
        self.assertEqual(complet["Close"].iloc[-1], 110.0)
        self.assertEqual(complet["Adj Close"].iloc[-1], 55.0)
        self.assertEqual(complet["High"].iloc[-1], 110.0)

    def test_ne_double_pas_une_seance_connue(self):
        h = cours(["2026-10-27"], [100.0])
        quand = pd.Timestamp("2026-10-27T20:00:00Z").timestamp()
        self.assertEqual(len(update.completer(h, {"regularMarketPrice": 101.0, "regularMarketTime": quand})), 1)
        self.assertEqual(len(update.completer(h, {})), 1)

    def test_apres_bourse(self):
        cote = {"regularMarketTime": 1000, "postMarketTime": 2000, "postMarketPrice": 110.0, "preMarketTime": None}
        hs = update.hors_seance(cote, 1, 10.0, 100.0)  # +10 % au cours officiel, puis +10 % après-bourse
        self.assertEqual((hs["type"], hs["prix"], hs["variation"], hs["perf"]), ("post", 110.0, 10.0, 21.0))

    def test_apres_bourse_vente_a_decouvert(self):
        cote = {"regularMarketTime": 1000, "postMarketTime": 2000, "postMarketPrice": 90.0}
        hs = update.hors_seance(cote, -1, 10.0, 100.0)  # position à 1,10 ; le cours baisse encore de 10 %
        self.assertEqual(hs["perf"], 19.0)

    def test_cours_hors_seance_plus_ancien_ignore(self):
        cote = {"regularMarketTime": 3000, "postMarketTime": 2000, "postMarketPrice": 110.0}
        self.assertIsNone(update.hors_seance(cote, 1, 0.0, 100.0))
        self.assertIsNone(update.hors_seance({}, 1, 0.0, 100.0))

    def test_avant_bourse_plus_recent_que_l_apres_bourse(self):
        cote = {"regularMarketTime": 1000, "postMarketTime": 2000, "postMarketPrice": 110.0, "preMarketTime": 3000, "preMarketPrice": 105.0}
        self.assertEqual(update.hors_seance(cote, 1, 0.0, 100.0)["type"], "pre")


class PortefeuilleHorsSeance(unittest.TestCase):
    COURBE = [{"d": "2026-01-01", "club": 0, "indice": 0}, {"d": "2026-01-02", "club": 10, "indice": 0}]
    SERIE = [("2026-01-01", 0, 0), ("2026-01-02", 0, 0)]

    def test_moyenne_sur_toutes_les_positions_ouvertes(self):
        a = pitch("2026-01-01", self.SERIE, perf=10, hors_seance={"perf": 21, "heure": "2026-01-02T23:00:00Z"})  # +10 % hors séance
        b = pitch("2026-01-01", self.SERIE, perf=0)  # pas de cours hors séance : reste à sa clôture
        hs = update.portefeuille_hors_seance([a, b], self.COURBE)
        self.assertEqual((hs["variation"], hs["club"], hs["positions"], hs["ouvertes"]), (5, 15.5, 1, 2))

    def test_vente_a_decouvert(self):
        p = pitch("2026-01-01", self.SERIE, position="SELL", perf=10, hors_seance={"perf": -1, "heure": "x"})
        self.assertEqual(update.portefeuille_hors_seance([p], self.COURBE)["variation"], -10)

    def test_positions_closes_ou_trop_recentes_ignorees(self):
        close = pitch("2025-01-01", [], fin="2025-12-31", perf=0, hors_seance={"perf": 50, "heure": "x"})
        nouvelle = pitch("2026-01-02", [], perf=0, hors_seance={"perf": 50, "heure": "x"})  # entrée à la dernière clôture
        for p in (close, nouvelle):
            p["serie"] = [{"d": "2026-01-01", "r": 0, "b": 0}]
        self.assertIsNone(update.portefeuille_hors_seance([close, nouvelle], self.COURBE))

    def test_sans_cours_hors_seance(self):
        self.assertIsNone(update.portefeuille_hors_seance([pitch("2026-01-01", self.SERIE, perf=0)], self.COURBE))
        self.assertIsNone(update.portefeuille_hors_seance([], []))


if __name__ == "__main__":
    unittest.main()
