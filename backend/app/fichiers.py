import io
import re
import unicodedata
from pathlib import Path

import pandas as pd

from . import traitement

EXTENSIONS = {".csv", ".tsv", ".xlsx", ".xlsm"}

TYPES = {
    "trafic": (traitement.CLE + traitement.VALEURS, traitement.preparer_trafic),
    "agregats": (["mois", "aerodrome", "mouvements_bruts"], traitement.preparer_agregats),
    "referentiel": (["code", "base_mouvements_jour"], traitement.preparer_referentiel),
}


def norme(nom):
    texte = unicodedata.normalize("NFKD", str(nom)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", texte).strip("_")


def lire_tables(contenu, nom):
    extension = Path(nom or "").suffix.lower()
    if extension not in EXTENSIONS:
        raise ValueError(f"Format non accepté ({extension or 'inconnu'}). Formats : {', '.join(sorted(EXTENSIONS))}")
    try:
        if extension in (".xlsx", ".xlsm"):
            tables = list(pd.read_excel(io.BytesIO(contenu), sheet_name=None).values())
        else:
            tables = [lire_texte(contenu)]
    except Exception as erreur:
        raise ValueError(f"Fichier illisible : {erreur}") from erreur
    for table in tables:
        table.columns = [norme(c) for c in table.columns]
    return tables


def lire_texte(contenu):
    for encodage in ("utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(io.BytesIO(contenu), sep=None, engine="python", encoding=encodage)
        except UnicodeDecodeError:
            continue
    raise ValueError("encodage illisible")


def classer(contenu, nom):
    trouvees = {}
    for table in lire_tables(contenu, nom):
        for type_table, (colonnes, preparer) in TYPES.items():
            if all(c in table.columns for c in colonnes):
                trouvees[type_table] = preparer(table.dropna(how="all"))
                break
    if not trouvees:
        attendu = " ; ".join(f"{t} : {', '.join(c)}" for t, (c, _) in TYPES.items())
        raise ValueError(f"Aucune table reconnue. Colonnes attendues : {attendu}")
    return trouvees
