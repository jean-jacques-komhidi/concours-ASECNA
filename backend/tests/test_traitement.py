import io
from pathlib import Path

import pandas as pd

from app import explication, fichiers, traitement

DOSSIER = Path(__file__).resolve().parent.parent / "data"
DONNEES = traitement.lire_trafic(DOSSIER / "trafic_journalier_brut.csv")
AGREGATS = traitement.preparer_agregats(traitement.lire_csv(DOSSIER / "agregats_mensuels_bruts.csv"))
REFERENTIEL = traitement.preparer_referentiel(traitement.lire_csv(DOSSIER / "referentiel_aerodromes.csv"))
ANOMALIES = traitement.detecter_anomalies(DONNEES)
PIC = next(a for a in ANOMALIES if a["type"] == "pic_atypique")


def types(anomalies):
    return sorted(a["type"] for a in anomalies)


def test_anomalies_du_jeu_fourni():
    assert types(ANOMALIES) == [
        "doublon",
        "doublon",
        "mouvements_manquants",
        "mouvements_negatifs",
        "pic_atypique",
        "retard_manquant",
    ]


def test_pic_du_14_aout():
    assert (PIC["aerodrome"], PIC["date"], PIC["nombre_mouvements"], PIC["valeur_reference"]) == (
        "DAK-SIM",
        "2025-08-14",
        290,
        35,
    )


def test_totaux_nets():
    net = traitement.construire_net(DONNEES, ANOMALIES, {})
    assert int(DONNEES["nombre_mouvements"].sum()) == 110266
    assert int(net["nombre_mouvements"].sum()) == 110229


def test_decision_corriger_remplace_le_pic():
    for decisions, attendu in [({}, 2331), ({PIC["id"]: {"decision": "corriger"}}, 2076), ({PIC["id"]: {"decision": "conserver"}}, 2331)]:
        net = traitement.construire_net(DONNEES, ANOMALIES, decisions)
        stats = traitement.statistiques(net, REFERENTIEL, "DAK-SIM", "2025-08")
        assert stats["total_mouvements"] == attendu


def test_rapprochement_lome_avril():
    lignes = traitement.rapprochement(DONNEES, AGREGATS, ANOMALIES, {})
    lome = next(l for l in lignes if l["mois"] == "2025-04" and l["aerodrome"] == "LME-SIM")
    assert (lome["agregat_brut"], lome["recalcul_brut"], lome["net"], lome["statut"]) == (965, 961, 965, "ecart_explique")


def test_rapprochement_juillet_2026_sans_agregat():
    lignes = traitement.rapprochement(DONNEES, AGREGATS, ANOMALIES, {})
    juillet = [l for l in lignes if l["mois"] == "2026-07"]
    assert len(juillet) == 4
    assert {l["statut"] for l in juillet} == {"absent_des_agregats"}


def test_agregat_sans_donnees_ne_plante_pas():
    sans_mars = DONNEES[DONNEES["mois"] != "2025-03"]
    lignes = traitement.rapprochement(sans_mars, AGREGATS, traitement.detecter_anomalies(sans_mars), {})
    mars = [l for l in lignes if l["mois"] == "2025-03"]
    assert {l["statut"] for l in mars} == {"agregat_sans_donnees"}


def test_jour_manquant_detecte():
    trou = DONNEES[~((DONNEES["aerodrome"] == "NIM-SIM") & DONNEES["date"].between("2025-06-10", "2025-06-12"))]
    manquants = [a for a in traitement.detecter_anomalies(trou) if a["type"] == "jour_manquant"]
    assert [(a["aerodrome"], a["date"]) for a in manquants] == [("NIM-SIM", "2025-06-10")]
    assert "3 jour(s)" in manquants[0]["detail"]


def test_retard_aberrant_detecte():
    modifie = DONNEES.copy()
    modifie.loc[10, "retard_moyen_min"] = 400
    aberrants = [a for a in traitement.detecter_anomalies(modifie) if a["type"] == "retard_aberrant"]
    assert [a["index_ligne"] for a in aberrants] == [10]


def test_tendances_variations():
    net = traitement.construire_net(DONNEES, ANOMALIES, {})
    mois = {p["mois"]: p for p in traitement.tendances(net, "DAK-SIM")}
    assert mois["2026-07"]["mois_complet"] is False
    assert mois["2026-07"]["variation_mois_precedent_pct"] is None
    assert mois["2025-01"]["variation_annee_precedente_pct"] is None
    assert mois["2026-02"]["variation_annee_precedente_pct"] == -4.6


def test_classement_des_aerodromes():
    net = traitement.construire_net(DONNEES, ANOMALIES, {})
    classement = traitement.classement_aerodromes(net, REFERENTIEL)
    assert [c["aerodrome"] for c in classement][0] == "DAK-SIM"
    assert round(sum(c["part_pct"] for c in classement)) == 100


def test_explication_du_pic():
    anomalies = traitement.ajouter_decisions(ANOMALIES, {})
    net = traitement.construire_net(DONNEES, anomalies, {})
    texte = explication.explication_jour(DONNEES, net, anomalies, REFERENTIEL, "DAK-SIM", "2025-08-14")
    assert "290" in texte["explication"]
    assert "ne permettent pas d'en établir la cause" in texte["explication"]
    assert texte["chiffres"]["effet_sur_le_mois"]["part_du_pic_pct"] == 12.4
    assert any("decision" in action for action in texte["actions_conseillees"])


def test_explication_apres_decision():
    decisions = {PIC["id"]: {"decision": "corriger"}}
    anomalies = traitement.ajouter_decisions(ANOMALIES, decisions)
    net = traitement.construire_net(DONNEES, anomalies, decisions)
    texte = explication.explication_jour(DONNEES, net, anomalies, REFERENTIEL, "DAK-SIM", "2025-08-14")
    assert "valeur corrigée" in texte["explication"]
    assert texte["chiffres"]["effet_sur_le_mois"]["part_du_pic_pct"] == 12.4


def test_explication_du_mois_lome():
    net = traitement.construire_net(DONNEES, ANOMALIES, {})
    lignes = traitement.rapprochement(DONNEES, AGREGATS, ANOMALIES, {})
    texte = explication.explication_mois(ANOMALIES, lignes, traitement.tendances(net, "LME-SIM"), "LME-SIM", "2025-04")
    assert "965" in texte["explication"] and "961" in texte["explication"]
    assert "négative" in texte["explication"]


def test_import_excel_trois_feuilles():
    sortie = io.BytesIO()
    with pd.ExcelWriter(sortie) as classeur:
        for nom, fichier in [("trafic", "trafic_journalier_brut"), ("agregats", "agregats_mensuels_bruts"), ("ref", "referentiel_aerodromes")]:
            traitement.lire_csv(DOSSIER / f"{fichier}.csv").to_excel(classeur, sheet_name=nom, index=False)
    tables = fichiers.classer(sortie.getvalue(), "donnees.xlsx")
    assert {nom: len(t) for nom, t in tables.items()} == {"trafic": 4378, "agregats": 72, "referentiel": 4}
    assert types(traitement.detecter_anomalies(tables["trafic"])) == types(ANOMALIES)


def test_import_refuse_ce_qui_n_est_pas_reconnu():
    for contenu, nom in [(b"a,b\n1,2\n", "x.csv"), (b"%PDF", "x.pdf")]:
        try:
            fichiers.classer(contenu, nom)
        except ValueError:
            continue
        raise AssertionError(nom)
