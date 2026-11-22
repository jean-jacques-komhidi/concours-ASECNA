import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture(autouse=True)
def etat_neuf():
    main.etat = main.Etat()


client = TestClient(main.app)


def test_sante():
    assert client.get("/health").json() == {"statut": "ok"}


def test_aerodromes():
    codes = [a["code"] for a in client.get("/aerodromes").json()]
    assert codes == ["DAK-SIM", "ABJ-SIM", "NIM-SIM", "LME-SIM"]


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


def test_rapprochement_lome_avril():
    lignes = client.get("/rapprochement").json()["lignes"]
    lome = next(l for l in lignes if l["mois"] == "2025-04" and l["aerodrome"] == "LME-SIM")
    assert (lome["agregat_brut"], lome["recalcul_brut"], lome["statut"]) == (965, 961, "ecart_explique")


def test_decision_sur_le_pic():
    params = {"aerodrome": "DAK-SIM", "mois": "2025-08"}
    avant = client.get("/stats", params=params).json()["total_mouvements"]
    reponse = client.post("/anomalies/pic_atypique-1800/decision", json={"decision": "corriger"})
    assert reponse.status_code == 200
    apres = client.get("/stats", params=params).json()["total_mouvements"]
    assert apres < avant


def test_erreurs():
    assert client.get("/stats", params={"aerodrome": "XXX"}).status_code == 404
    assert client.get("/stats", params={"mois": "2025-13"}).status_code == 422
    assert client.post("/anomalies/inconnue/decision", json={"decision": "corriger"}).status_code == 404
    assert client.post("/donnees", files={"fichier": ("x.csv", b"a,b\n1,2\n", "text/csv")}).status_code == 422
