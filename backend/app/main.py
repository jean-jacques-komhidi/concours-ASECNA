from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import traitement

DOSSIER_DONNEES = Path(__file__).resolve().parent.parent / "data"
FORMAT_MOIS = r"^\d{4}-(0[1-9]|1[0-2])$"

TypeAnomalie = Literal["doublon", "mouvements_manquants", "retard_manquant", "mouvements_negatifs", "pic_atypique"]


class Etat:
    def __init__(self):
        self.fichier = "trafic_journalier_brut.csv"
        self.donnees = traitement.lire_trafic(DOSSIER_DONNEES / self.fichier)
        self.agregats = traitement.lire_csv(DOSSIER_DONNEES / "agregats_mensuels_bruts.csv")
        self.referentiel = traitement.lire_csv(DOSSIER_DONNEES / "referentiel_aerodromes.csv")
        self.decisions = {}

    def anomalies(self):
        return traitement.detecter_anomalies(self.donnees)

    def net(self, anomalies=None):
        anomalies = self.anomalies() if anomalies is None else anomalies
        return traitement.construire_net(self.donnees, anomalies, self.decisions)


etat = Etat()

app = FastAPI(
    title="API Trafic Aérien",
    version="1.0.0",
    description="Contrôle, analyse et explication des données de trafic aérien (données fictives).",
)

# ouvert à tous pour le prototype, à restreindre ensuite
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def verifier_aerodrome(code):
    codes = list(etat.referentiel["code"])
    if code and code not in codes:
        raise HTTPException(404, f"Aérodrome inconnu : {code}. Valeurs possibles : {', '.join(codes)}")


@app.get("/health", operation_id="sante", summary="Vérifier que l'API répond", tags=["Général"])
def sante():
    return {"statut": "ok"}


@app.get("/aerodromes", operation_id="lister_aerodromes", summary="Liste des aérodromes", tags=["Général"])
def aerodromes():
    return etat.referentiel.to_dict(orient="records")


@app.get("/resume", operation_id="resume_donnees", summary="Vue d'ensemble des données", tags=["Analyse"])
def resume():
    """Période, volumes, anomalies par type et décisions en attente. À appeler en premier."""
    anomalies = etat.anomalies()
    par_type = {t: 0 for t in traitement.TYPES_ANOMALIES}
    for a in anomalies:
        par_type[a["type"]] += 1
    en_attente = [a["id"] for a in anomalies if a["type"] == "pic_atypique" and a["id"] not in etat.decisions]
    donnees = etat.donnees
    return {
        "fichier": etat.fichier,
        "nombre_lignes": int(len(donnees)),
        "periode": {"debut": donnees["date"].min(), "fin": donnees["date"].max()},
        "aerodromes": sorted(donnees["aerodrome"].unique().tolist()),
        "total_mouvements_brut": int(donnees["nombre_mouvements"].sum()),
        "total_mouvements_net": int(etat.net(anomalies)["nombre_mouvements"].sum()),
        "anomalies_par_type": par_type,
        "decisions_humaines_en_attente": en_attente,
    }


@app.post("/donnees", operation_id="envoyer_donnees", summary="Remplacer le fichier de trafic", tags=["Données"])
def envoyer_donnees(fichier: UploadFile = File(...)):
    """Charge un nouveau CSV de trafic. Les décisions déjà prises sont effacées."""
    try:
        nouvelles = traitement.lire_trafic(fichier.file)
    except Exception as erreur:
        raise HTTPException(422, f"Fichier invalide : {erreur}") from erreur
    etat.donnees = nouvelles
    etat.fichier = fichier.filename or "fichier_envoye.csv"
    etat.decisions.clear()
    return {"statut": "charge", "fichier": etat.fichier, "nombre_lignes": int(len(nouvelles))}


@app.get("/controle", operation_id="controle_qualite", summary="Anomalies des données brutes", tags=["Analyse"])
def controle(
    type_anomalie: TypeAnomalie | None = Query(None, alias="type", description="Filtrer sur un type"),
    aerodrome: str | None = Query(None, description="Ex. DAK-SIM"),
):
    """Doublons, valeurs manquantes, mouvements négatifs et pics, avec le numéro de ligne du CSV."""
    verifier_aerodrome(aerodrome)
    anomalies = etat.anomalies()
    if type_anomalie:
        anomalies = [a for a in anomalies if a["type"] == type_anomalie]
    if aerodrome:
        anomalies = [a for a in anomalies if a["aerodrome"] == aerodrome]
    for a in anomalies:
        decision = etat.decisions.get(a["id"])
        a["decision"] = decision["decision"] if decision else None
    return {"nombre_anomalies": len(anomalies), "anomalies": anomalies}


@app.get("/rapprochement", operation_id="rapprochement_agregats", summary="Agrégats bruts, recalcul et net", tags=["Analyse"])
def rapprochement(
    uniquement_ecarts: bool = Query(False, description="Garder seulement les mois à expliquer"),
    aerodrome: str | None = Query(None, description="Ex. DAK-SIM"),
):
    """Pour chaque mois et aérodrome : agrégat brut, recalcul, net, écart et explication."""
    verifier_aerodrome(aerodrome)
    lignes = traitement.rapprochement(etat.donnees, etat.agregats, etat.anomalies(), etat.decisions)
    if aerodrome:
        lignes = [l for l in lignes if l["aerodrome"] == aerodrome]
    if uniquement_ecarts:
        lignes = [l for l in lignes if l["statut"] != "ok"]
    return {
        "nombre_lignes": len(lignes),
        "impact_total_nettoyage": sum(l["impact_nettoyage"] for l in lignes),
        "lignes": lignes,
    }


@app.get("/stats", operation_id="statistiques_trafic", summary="Indicateurs de trafic (données nettes)", tags=["Analyse"])
def stats(
    aerodrome: str | None = Query(None, description="Ex. DAK-SIM. Vide : tous les aérodromes"),
    mois: str | None = Query(None, pattern=FORMAT_MOIS, description="AAAA-MM. Vide : toute la période"),
):
    """Total, moyenne par jour, retard moyen, répartitions et écart au volume de référence."""
    verifier_aerodrome(aerodrome)
    try:
        return traitement.statistiques(etat.net(), etat.referentiel, aerodrome, mois)
    except LookupError as erreur:
        raise HTTPException(404, str(erreur)) from erreur


@app.get("/tendances", operation_id="tendances_mensuelles", summary="Évolution mensuelle du trafic", tags=["Analyse"])
def tendances(aerodrome: str | None = Query(None, description="Ex. DAK-SIM. Vide : tous les aérodromes")):
    """Total par mois sur les données nettes. Les mois partiels sont signalés par mois_complet."""
    verifier_aerodrome(aerodrome)
    try:
        return traitement.tendances(etat.net(), aerodrome)
    except LookupError as erreur:
        raise HTTPException(404, str(erreur)) from erreur


class Decision(BaseModel):
    decision: Literal["corriger", "conserver"]
    commentaire: str | None = None


@app.post(
    "/anomalies/{anomalie_id}/decision",
    operation_id="decider_anomalie",
    summary="Décision humaine sur une anomalie",
    tags=["Validation humaine"],
)
def decider(anomalie_id: str, corps: Decision):
    """corriger : le pic est remplacé par la valeur de référence. conserver : le pic est réel."""
    if anomalie_id not in {a["id"] for a in etat.anomalies()}:
        raise HTTPException(404, f"Anomalie inconnue : {anomalie_id}")
    etat.decisions[anomalie_id] = corps.model_dump()
    return {"anomalie_id": anomalie_id, **etat.decisions[anomalie_id]}


@app.get("/decisions", operation_id="lister_decisions", summary="Décisions prises", tags=["Validation humaine"])
def decisions():
    return [{"anomalie_id": cle, **valeur} for cle, valeur in etat.decisions.items()]
