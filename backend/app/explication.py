import pandas as pd

from .traitement import construire_net, date_fr, mois_fr, nombre, retard_pondere

ACTIONS = {
    "pic_atypique": [
        "Vérifier la valeur à la source (erreur de saisie ou événement réel).",
        "Décider avec POST /anomalies/{id}/decision : corriger ou conserver.",
    ],
    "creux_atypique": ["Vérifier la valeur à la source (panne, fermeture ou erreur de saisie)."],
    "doublon": ["Supprimer la ligne en double à la source (déjà exclue du net)."],
    "mouvements_manquants": ["Compléter la valeur à la source."],
    "mouvements_negatifs": ["Corriger la valeur à la source, un nombre de mouvements ne peut pas être négatif."],
    "retard_manquant": ["Compléter le retard moyen à la source."],
    "retard_aberrant": ["Vérifier l'unité (minutes) et la valeur du retard."],
    "jour_manquant": ["Récupérer les données des jours manquants à la source."],
}


def phrase(texte):
    return texte if texte.endswith(".") else texte + "."


def actions(anomalies):
    liste = []
    for a in anomalies:
        for action in ACTIONS.get(a["type"], []):
            if action not in liste:
                liste.append(action)
    return liste


def nom_aerodrome(referentiel, code):
    noms = referentiel.loc[referentiel["code"] == code, "nom_fictif"]
    return f"{code} ({noms.iloc[0]})" if not noms.empty else code


def phrase_decision(anomalie):
    decision = anomalie.get("decision")
    if decision == "corriger":
        return "Décision humaine : valeur corrigée dans le net."
    if decision == "conserver":
        return "Décision humaine : valeur conservée."
    return "Aucune décision humaine pour l'instant, la valeur reste dans les totaux."


def explication_jour(donnees, net, anomalies, referentiel, aerodrome, jour):
    brut = donnees[(donnees["aerodrome"] == aerodrome) & (donnees["date"] == jour)]
    if brut.empty:
        raise LookupError(f"Aucune donnée pour {aerodrome} le {date_fr(jour)}.")
    du_jour = net[(net["aerodrome"] == aerodrome) & (net["date"] == jour)]
    total = int(du_jour["nombre_mouvements"].sum())
    arrivees = int(du_jour.loc[du_jour["type_mouvement"] == "ARRIVEE", "nombre_mouvements"].sum())
    departs = int(du_jour.loc[du_jour["type_mouvement"] == "DEPART", "nombre_mouvements"].sum())
    trouvees = [a for a in anomalies if a["aerodrome"] == aerodrome and a["date"] == jour]

    par_jour = net[net["aerodrome"] == aerodrome].groupby("date")["nombre_mouvements"].sum()
    par_jour.index = pd.to_datetime(par_jour.index)
    instant = pd.Timestamp(jour)
    autour = par_jour[(par_jour.index >= instant - pd.Timedelta(days=15))
                      & (par_jour.index <= instant + pd.Timedelta(days=15))
                      & (par_jour.index != instant)]
    habituel = float(autour.median()) if len(autour) >= 5 else None
    base = referentiel.loc[referentiel["code"] == aerodrome, "base_mouvements_jour"]
    base_jour = None if base.empty or pd.isna(base.iloc[0]) else int(base.iloc[0])

    phrases = [
        f"Le {date_fr(jour)}, {nom_aerodrome(referentiel, aerodrome)} compte {total} mouvements "
        f"({arrivees} arrivées, {departs} départs)."
    ]
    rapport = None
    if habituel:
        rapport = total / habituel
        texte = f"Le niveau habituel autour de cette date est d'environ {habituel:.0f} mouvements par jour"
        if base_jour:
            texte += f" (référence du référentiel : {base_jour})"
        if abs(rapport - 1) > 0.25:
            texte += f". Ce jour-là fait {nombre(rapport)} fois le niveau habituel."
        else:
            texte += ". Ce jour-là est dans la norme."
        phrases.append(texte)
    retard = retard_pondere(du_jour)
    if retard is not None:
        phrases.append(f"Retard moyen : {nombre(retard)} min.")

    effet_mois = None
    for a in trouvees:
        phrases.append(phrase(a["detail"]))
        if a["type"] == "pic_atypique":
            mois = jour[:7]
            avant_decision = construire_net(donnees, anomalies, {})
            total_mois = int(avant_decision[(avant_decision["aerodrome"] == aerodrome) & (avant_decision["mois"] == mois)]["nombre_mouvements"].sum())
            part = round(100 * a["nombre_mouvements"] / total_mois, 1) if total_mois else 0
            effet_mois = {"mois": mois, "total_mois": total_mois, "part_du_pic_pct": part}
            phrases.append(
                f"Cette ligne représente {nombre(part)} % du total du mois ({mois_fr(mois)}, "
                f"{total_mois} mouvements pour {aerodrome}). Les données ne permettent pas d'en établir la cause."
            )
            phrases.append(phrase_decision(a))
    if not trouvees:
        phrases.append("Aucune anomalie ce jour-là.")

    return {
        "titre": f"{aerodrome}, {date_fr(jour)}",
        "explication": " ".join(phrases),
        "chiffres": {
            "total_mouvements": total,
            "arrivees": arrivees,
            "departs": departs,
            "niveau_habituel": None if habituel is None else round(habituel, 1),
            "rapport_au_niveau_habituel": None if rapport is None else round(rapport, 2),
            "base_referentiel_par_jour": base_jour,
            "retard_moyen_min": retard,
            "effet_sur_le_mois": effet_mois,
        },
        "anomalies": trouvees,
        "actions_conseillees": actions(trouvees),
    }


def explication_mois(anomalies, lignes_rapprochement, points_tendance, aerodrome, mois):
    rapprochement = next((l for l in lignes_rapprochement if l["mois"] == mois and l["aerodrome"] == aerodrome), None)
    point = next((p for p in points_tendance if p["mois"] == mois), None)
    if rapprochement is None or point is None:
        raise LookupError(f"Aucune donnée pour {aerodrome} en {mois}.")

    phrases = [
        f"En {mois_fr(mois)}, {aerodrome} compte {point['total_mouvements']} mouvements "
        f"sur {point['jours_couverts']} jour(s), soit {nombre(point['moyenne_par_jour'])} par jour."
    ]
    if not point["mois_complet"]:
        phrases.append("Mois incomplet : à ne pas comparer aux autres mois.")
    if point["variation_mois_precedent_pct"] is not None:
        phrases.append(f"Variation par rapport au mois précédent : {nombre(point['variation_mois_precedent_pct'])} %.")
    if point["variation_annee_precedente_pct"] is not None:
        phrases.append(f"Variation par rapport à l'an dernier : {nombre(point['variation_annee_precedente_pct'])} %.")

    if rapprochement["agregat_brut"] is None:
        phrases.append("Aucun agrégat fourni pour ce mois.")
    elif rapprochement["ecart_agregat_vs_recalcul"] == 0:
        phrases.append(f"L'agrégat brut ({rapprochement['agregat_brut']}) correspond au recalcul.")
    else:
        phrases.append(
            f"L'agrégat brut ({rapprochement['agregat_brut']}) ne correspond pas au recalcul "
            f"({rapprochement['recalcul_brut']})."
        )
    phrases.append(rapprochement["commentaire"])
    if rapprochement["impact_nettoyage"]:
        phrases.append(f"Le nettoyage change le total de {rapprochement['impact_nettoyage']:+d} mouvements.")

    du_mois = [a for a in anomalies if a["aerodrome"] == aerodrome and a["mois"] == mois]
    phrases += [phrase(a["detail"]) for a in du_mois]

    return {
        "titre": f"{aerodrome}, {mois_fr(mois)}",
        "explication": " ".join(phrases),
        "chiffres": {**point, "rapprochement": rapprochement},
        "anomalies": du_mois,
        "actions_conseillees": actions(du_mois),
    }
