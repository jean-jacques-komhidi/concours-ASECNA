import os
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Path as ParamChemin, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as ErreurHTTP

from . import explication, fichiers, openapi_copilot, traitement

DOSSIER_DONNEES = Path(__file__).resolve().parent.parent / "data"
FORMAT_MOIS = r"^\d{4}-(0[1-9]|1[0-2])$"

TypeAnomalie = Literal[
    "doublon", "mouvements_manquants", "retard_manquant", "mouvements_negatifs",
    "pic_atypique", "creux_atypique", "retard_aberrant", "jour_manquant",
]

# outils exposés à Copilot Studio (la décision humaine reste côté interface)
OUTILS_COPILOT = [
    "resume_donnees",
    "tableau_de_bord",
    "lister_aerodromes",
    "controle_qualite",
    "rapprochement_agregats",
    "statistiques_trafic",
    "tendances_mensuelles",
    "explication_jour",
    "explication_mois",
]


class Etat:
    def __init__(self):
        self.fichier = "trafic_journalier_brut.csv"
        self.donnees = traitement.lire_trafic(DOSSIER_DONNEES / self.fichier)
        self.agregats = traitement.preparer_agregats(traitement.lire_csv(DOSSIER_DONNEES / "agregats_mensuels_bruts.csv"))
        self.referentiel = traitement.preparer_referentiel(traitement.lire_csv(DOSSIER_DONNEES / "referentiel_aerodromes.csv"))
        self.decisions = {}

    def anomalies(self):
        return traitement.detecter_anomalies(self.donnees)

    def net(self, anomalies=None):
        anomalies = self.anomalies() if anomalies is None else anomalies
        return traitement.construire_net(self.donnees, anomalies, self.decisions)


etat = Etat()

app = FastAPI(
    title="API Trafic Aérien",
    version="2.0.0",
    description="Contrôle, analyse et explication des données de trafic aérien (données fictives).",
)

# ouvert à tous pour le prototype, à restreindre ensuite (variable CORS_ORIGINES)
origines = [o.strip() for o in os.getenv("CORS_ORIGINES", "*").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origines, allow_methods=["*"], allow_headers=["*"])


# toutes les erreurs ont la forme {"detail": "texte"}
@app.exception_handler(ErreurHTTP)
async def erreur_http(requete: Request, erreur: ErreurHTTP):
    return JSONResponse({"detail": str(erreur.detail)}, status_code=erreur.status_code)


@app.exception_handler(LookupError)
async def erreur_introuvable(requete: Request, erreur: LookupError):
    return JSONResponse({"detail": str(erreur.args[0]) if erreur.args else "Introuvable"}, status_code=404)


@app.exception_handler(RequestValidationError)
async def erreur_validation(requete: Request, erreur: RequestValidationError):
    champs = []
    for e in erreur.errors():
        lieu = [str(p) for p in e["loc"] if p not in ("body", "query", "path")]
        champs.append({"champ": ".".join(lieu) or "requête", "message": str(e["msg"])})
    texte = "; ".join(f"{c['champ']} : {c['message']}" for c in champs)
    return JSONResponse({"detail": f"Paramètres invalides : {texte}", "erreurs": champs}, status_code=422)


def verifier_aerodrome(code):
    codes = list(etat.referentiel["code"])
    if code and code not in codes:
        raise HTTPException(404, f"Aérodrome inconnu : {code}. Valeurs possibles : {', '.join(codes)}")


def resume_donnees(anomalies):
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


@app.get("/health", operation_id="sante", summary="Vérifier que l'API répond", tags=["Général"])
def sante():
    return {"statut": "ok"}


@app.get("/aerodromes", operation_id="lister_aerodromes", summary="Liste des aérodromes", tags=["Général"])
def aerodromes():
    return traitement.vers_json(etat.referentiel)


@app.get("/resume", operation_id="resume_donnees", summary="Vue d'ensemble des données", tags=["Analyse"])
def resume():
    """Période, volumes, anomalies par type et décisions en attente. À appeler en premier."""
    return resume_donnees(etat.anomalies())


@app.get("/tableau-de-bord", operation_id="tableau_de_bord", summary="Tout l'écran d'accueil en un appel", tags=["Analyse"])
def tableau_de_bord():
    """Résumé, classement des aérodromes, totaux mensuels du réseau et anomalies de gravité haute."""
    anomalies = traitement.ajouter_decisions(etat.anomalies(), etat.decisions)
    net = etat.net(anomalies)
    return {
        "resume": resume_donnees(anomalies),
        "classement_aerodromes": traitement.classement_aerodromes(net, etat.referentiel),
        "totaux_mensuels": traitement.totaux_mensuels(net),
        "anomalies_hautes": [a for a in anomalies if a["gravite"] == "haute"],
    }


@app.post("/donnees", operation_id="envoyer_donnees", summary="Remplacer les données (CSV ou Excel)", tags=["Données"])
def envoyer_donnees(fichier: UploadFile = File(...)):
    """Accepte un CSV ou un Excel. Le trafic, le référentiel et les agrégats sont reconnus d'après leurs colonnes.
    Seules les tables trouvées sont remplacées. Si le trafic change, les décisions déjà prises sont effacées."""
    try:
        tables = fichiers.classer(fichier.file.read(), fichier.filename)
    except ValueError as erreur:
        raise HTTPException(422, f"Fichier invalide : {erreur}") from erreur
    if "trafic" in tables:
        etat.donnees = tables["trafic"]
        etat.decisions.clear()
        etat.fichier = fichier.filename
    if "agregats" in tables:
        etat.agregats = tables["agregats"]
    if "referentiel" in tables:
        etat.referentiel = tables["referentiel"]
    return {"statut": "charge", "fichier": fichier.filename, "tables": {nom: int(len(t)) for nom, t in tables.items()}}


@app.get("/lignes", operation_id="lister_lignes", summary="Lignes brutes avec leurs anomalies", tags=["Données"])
def lignes(
    aerodrome: str | None = Query(None, description="Ex. DAK-SIM"),
    du: date | None = Query(None, description="Date minimale AAAA-MM-JJ"),
    au: date | None = Query(None, description="Date maximale AAAA-MM-JJ"),
    uniquement_anomalies: bool = Query(False, description="Garder les lignes qui ont une anomalie"),
    limite: int = Query(100, ge=1, le=1000),
    decalage: int = Query(0, ge=0),
):
    """Lignes du CSV, paginées. Chaque ligne porte la liste de ses anomalies."""
    verifier_aerodrome(aerodrome)
    par_ligne = {}
    for a in etat.anomalies():
        if a["index_ligne"] is not None:
            par_ligne.setdefault(a["index_ligne"], []).append(a["type"])
    d = etat.donnees
    if aerodrome:
        d = d[d["aerodrome"] == aerodrome]
    if du:
        d = d[d["date"] >= du.isoformat()]
    if au:
        d = d[d["date"] <= au.isoformat()]
    if uniquement_anomalies:
        d = d[d.index.isin(list(par_ligne))]
    page = d.iloc[decalage:decalage + limite].drop(columns=["mois"])
    resultat = traitement.vers_json(page)
    for ligne, index in zip(resultat, page.index):
        ligne["ligne_csv"] = int(index) + 2
        ligne["anomalies"] = par_ligne.get(index, [])
    return {"total": int(len(d)), "limite": limite, "decalage": decalage, "lignes": resultat}


@app.get("/controle", operation_id="controle_qualite", summary="Anomalies des données brutes", tags=["Analyse"])
def controle(
    type_anomalie: TypeAnomalie | None = Query(None, alias="type", description="Filtrer sur un type"),
    aerodrome: str | None = Query(None, description="Ex. DAK-SIM"),
):
    """Doublons, manquants, négatifs, pics, creux, retards aberrants et jours sans données."""
    verifier_aerodrome(aerodrome)
    anomalies = traitement.ajouter_decisions(etat.anomalies(), etat.decisions)
    if type_anomalie:
        anomalies = [a for a in anomalies if a["type"] == type_anomalie]
    if aerodrome:
        anomalies = [a for a in anomalies if a["aerodrome"] == aerodrome]
    return {"nombre_anomalies": len(anomalies), "anomalies": anomalies}


@app.get("/rapprochement", operation_id="rapprochement_agregats", summary="Agrégats bruts, recalcul et net", tags=["Analyse"])
def rapprochement(
    uniquement_ecarts: bool = Query(False, description="Garder seulement les mois à expliquer"),
    aerodrome: str | None = Query(None, description="Ex. DAK-SIM"),
):
    """Pour chaque mois et aérodrome : agrégat brut, recalcul, net, écart et explication."""
    verifier_aerodrome(aerodrome)
    resultat = traitement.rapprochement(etat.donnees, etat.agregats, etat.anomalies(), etat.decisions)
    if aerodrome:
        resultat = [l for l in resultat if l["aerodrome"] == aerodrome]
    if uniquement_ecarts:
        resultat = [l for l in resultat if l["statut"] != "ok"]
    return {
        "nombre_lignes": len(resultat),
        "impact_total_nettoyage": sum(l["impact_nettoyage"] for l in resultat),
        "lignes": resultat,
    }


@app.get("/stats", operation_id="statistiques_trafic", summary="Indicateurs de trafic (données nettes)", tags=["Analyse"])
def stats(
    aerodrome: str | None = Query(None, description="Ex. DAK-SIM. Vide : tous les aérodromes"),
    mois: str | None = Query(None, pattern=FORMAT_MOIS, description="AAAA-MM. Vide : toute la période"),
):
    """Total, moyenne par jour, retard moyen pondéré, répartitions et écart au volume de référence."""
    verifier_aerodrome(aerodrome)
    return traitement.statistiques(etat.net(), etat.referentiel, aerodrome, mois)


@app.get("/tendances", operation_id="tendances_mensuelles", summary="Évolution mensuelle du trafic", tags=["Analyse"])
def tendances(aerodrome: str | None = Query(None, description="Ex. DAK-SIM. Vide : tous les aérodromes")):
    """Total par mois, variation sur le mois précédent et sur l'an dernier (mois complets seulement)."""
    verifier_aerodrome(aerodrome)
    return traitement.tendances(etat.net(), aerodrome)


@app.get(
    "/explication/jour/{aerodrome}/{jour}",
    operation_id="explication_jour",
    summary="Explication en français d'une journée",
    tags=["Explication"],
)
def explication_du_jour(
    aerodrome: str = ParamChemin(..., description="Ex. DAK-SIM"),
    jour: date = ParamChemin(..., description="AAAA-MM-JJ, ex. 2025-08-14"),
):
    """Trafic du jour comparé au niveau habituel, retard, anomalies, décision humaine et actions conseillées."""
    verifier_aerodrome(aerodrome)
    anomalies = traitement.ajouter_decisions(etat.anomalies(), etat.decisions)
    return explication.explication_jour(
        etat.donnees, etat.net(anomalies), anomalies, etat.referentiel, aerodrome, jour.isoformat()
    )


@app.get(
    "/explication/mois/{aerodrome}/{mois}",
    operation_id="explication_mois",
    summary="Explication en français d'un mois",
    tags=["Explication"],
)
def explication_du_mois(
    aerodrome: str = ParamChemin(..., description="Ex. DAK-SIM"),
    mois: str = ParamChemin(..., pattern=FORMAT_MOIS, description="AAAA-MM, ex. 2025-04"),
):
    """Trafic du mois, variations, rapprochement avec l'agrégat brut et anomalies du mois."""
    verifier_aerodrome(aerodrome)
    anomalies = traitement.ajouter_decisions(etat.anomalies(), etat.decisions)
    net = etat.net(anomalies)
    lignes_rapprochement = traitement.rapprochement(etat.donnees, etat.agregats, anomalies, etat.decisions)
    return explication.explication_mois(
        anomalies, lignes_rapprochement, traitement.tendances(net, aerodrome), aerodrome, mois
    )


class Decision(BaseModel):
    decision: Literal["corriger", "conserver"]
    commentaire: str | None = None


@app.post(
    "/anomalies/{anomalie_id}/decision",
    operation_id="decider_anomalie",
    summary="Décision humaine sur un pic",
    tags=["Validation humaine"],
)
def decider(anomalie_id: str, corps: Decision):
    """corriger : le pic est remplacé par la valeur habituelle. conserver : le pic est réel."""
    anomalie = next((a for a in etat.anomalies() if a["id"] == anomalie_id), None)
    if anomalie is None:
        raise HTTPException(404, f"Anomalie inconnue : {anomalie_id}")
    if anomalie["type"] != "pic_atypique":
        raise HTTPException(422, "Seuls les pics atypiques demandent une décision humaine")
    etat.decisions[anomalie_id] = corps.model_dump()
    return {"anomalie_id": anomalie_id, **etat.decisions[anomalie_id]}


@app.get("/decisions", operation_id="lister_decisions", summary="Décisions prises", tags=["Validation humaine"])
def decisions():
    return [{"anomalie_id": cle, **valeur} for cle, valeur in etat.decisions.items()]


# FastAPI produit de l'OpenAPI 3.1, Copilot Studio attend la 3.0
def adresse_publique(requete=None):
    adresse = os.getenv("URL_PUBLIQUE")
    if adresse:
        return adresse.rstrip("/")
    return str(requete.base_url).rstrip("/") if requete else None


def openapi_personnalise():
    if not app.openapi_schema:
        app.openapi_schema = openapi_copilot.vers_30(openapi_copilot.schema_complet(app), adresse_publique())
    return app.openapi_schema


app.openapi = openapi_personnalise


@app.get("/copilot/openapi.json", include_in_schema=False)
def openapi_pour_copilot(requete: Request):
    reduit = openapi_copilot.garder_operations(openapi_copilot.schema_complet(app), OUTILS_COPILOT)
    return openapi_copilot.vers_30(reduit, adresse_publique(requete))
