import io
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture(autouse=True)
def etat_neuf():
    main.etat = main.Etat()


DONNEES = Path(__file__).resolve().parent.parent / "data"
client = TestClient(main.app)


def test_sante():
    assert client.get("/health").json() == {"statut": "ok"}


def test_aerodromes():
    codes = [a["code"] for a in client.get("/aerodromes").json()]
    assert codes == ["DAK-SIM", "ABJ-SIM", "NIM-SIM", "LME-SIM"]


def test_resume():
    resume = client.get("/resume").json()
    assert resume["total_mouvements_net"] == 110229
    assert resume["decisions_humaines_en_attente"] == ["pic_atypique-1800"]


def test_tableau_de_bord():
    tableau = client.get("/tableau-de-bord").json()
    assert set(tableau) == {"resume", "classement_aerodromes", "totaux_mensuels", "anomalies_hautes"}
    assert tableau["classement_aerodromes"][0]["aerodrome"] == "DAK-SIM"
    assert {a["type"] for a in tableau["anomalies_hautes"]} == {"mouvements_manquants", "mouvements_negatifs", "pic_atypique"}


def test_controle_trouve_les_anomalies():
    types = sorted(a["type"] for a in client.get("/controle").json()["anomalies"])
    assert types == [
        "doublon",
        "doublon",
        "mouvements_manquants",
        "mouvements_negatifs",
        "pic_atypique",
        "retard_manquant",
    ]


def test_pic_du_14_aout():
    pics = client.get("/controle", params={"type": "pic_atypique"}).json()["anomalies"]
    assert len(pics) == 1
    assert (pics[0]["aerodrome"], pics[0]["date"], pics[0]["nombre_mouvements"]) == ("DAK-SIM", "2025-08-14", 290)


def test_lignes_avec_anomalies():
    reponse = client.get("/lignes", params={"uniquement_anomalies": True}).json()
    assert reponse["total"] == 6
    assert all(ligne["anomalies"] for ligne in reponse["lignes"])
    page = client.get("/lignes", params={"aerodrome": "DAK-SIM", "limite": 5, "decalage": 5}).json()
    assert len(page["lignes"]) == 5 and page["lignes"][0]["ligne_csv"] == 19


def test_rapprochement_lome_avril():
    lignes = client.get("/rapprochement").json()["lignes"]
    lome = next(l for l in lignes if l["mois"] == "2025-04" and l["aerodrome"] == "LME-SIM")
    assert (lome["agregat_brut"], lome["recalcul_brut"], lome["statut"]) == (965, 961, "ecart_explique")


def test_decision_sur_le_pic():
    params = {"aerodrome": "DAK-SIM", "mois": "2025-08"}
    assert client.get("/stats", params=params).json()["total_mouvements"] == 2331
    reponse = client.post("/anomalies/pic_atypique-1800/decision", json={"decision": "corriger"})
    assert reponse.status_code == 200
    assert client.get("/stats", params=params).json()["total_mouvements"] == 2076
    assert client.get("/resume").json()["decisions_humaines_en_attente"] == []
    assert "valeur corrigée" in client.get("/explication/jour/DAK-SIM/2025-08-14").json()["explication"]


def test_decision_refusee_hors_pic():
    doublon = client.get("/controle", params={"type": "doublon"}).json()["anomalies"][0]["id"]
    assert client.post(f"/anomalies/{doublon}/decision", json={"decision": "corriger"}).status_code == 422


def test_explication_jour_et_mois():
    jour = client.get("/explication/jour/DAK-SIM/2025-08-14").json()
    assert "290" in jour["explication"] and jour["anomalies"][0]["type"] == "pic_atypique"
    mois = client.get("/explication/mois/LME-SIM/2025-04").json()
    assert "965" in mois["explication"] and "961" in mois["explication"]


def test_envoi_excel():
    sortie = io.BytesIO()
    with pd.ExcelWriter(sortie) as classeur:
        pd.read_csv(DONNEES / "trafic_journalier_brut.csv", encoding="utf-8-sig").to_excel(classeur, sheet_name="trafic", index=False)
        pd.read_csv(DONNEES / "agregats_mensuels_bruts.csv", encoding="utf-8-sig").to_excel(classeur, sheet_name="agregats", index=False)
    fichier = {"fichier": ("donnees.xlsx", sortie.getvalue(), "application/octet-stream")}
    reponse = client.post("/donnees", files=fichier)
    assert reponse.json()["tables"] == {"trafic": 4378, "agregats": 72}
    assert client.get("/resume").json()["total_mouvements_net"] == 110229


def test_spec_copilot():
    spec = client.get("/copilot/openapi.json").json()
    assert spec["openapi"] == "3.0.3"
    identifiants = {op["operationId"] for chemin in spec["paths"].values() for op in chemin.values()}
    assert identifiants == set(main.OUTILS_COPILOT)
    assert client.get("/openapi.json").json()["openapi"] == "3.0.3"


def test_erreurs():
    assert client.get("/stats", params={"aerodrome": "XXX"}).status_code == 404
    mauvais_mois = client.get("/stats", params={"mois": "2025-13"})
    assert mauvais_mois.status_code == 422 and isinstance(mauvais_mois.json()["detail"], str)
    assert client.get("/explication/jour/DAK-SIM/2025-02-30").status_code == 422
    assert client.get("/explication/jour/DAK-SIM/2030-01-01").status_code == 404
    assert client.get("/route-inconnue").json() == {"detail": "Not Found"}
    assert client.post("/anomalies/inconnue/decision", json={"decision": "corriger"}).status_code == 404
    assert client.post("/donnees", files={"fichier": ("x.csv", b"a,b\n1,2\n", "text/csv")}).status_code == 422
