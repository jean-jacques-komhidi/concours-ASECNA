import pandas as pd

CLE = ["date", "aerodrome", "type_mouvement", "categorie_vol", "origine_destination"]
VALEURS = ["nombre_mouvements", "retard_moyen_min"]
TYPES_ANOMALIES = ["doublon", "mouvements_manquants", "retard_manquant", "mouvements_negatifs", "pic_atypique"]
FACTEUR_PIC = 3


def lire_csv(source):
    return pd.read_csv(source, encoding="utf-8-sig")


def lire_trafic(source):
    donnees = lire_csv(source)
    absentes = [c for c in CLE + VALEURS if c not in donnees.columns]
    if absentes:
        raise ValueError("colonnes manquantes : " + ", ".join(absentes))
    donnees["date"] = donnees["date"].astype(str)
    donnees["mois"] = donnees["date"].str[:7]
    for colonne in VALEURS:
        donnees[colonne] = pd.to_numeric(donnees[colonne], errors="coerce")
    return donnees


def detecter_anomalies(donnees):
    anomalies = []

    def ajouter(type_anomalie, i, gravite, detail, **autres):
        ligne = donnees.loc[i]
        valeur = ligne["nombre_mouvements"]
        anomalies.append(
            {
                "id": f"{type_anomalie}-{i}",
                "type": type_anomalie,
                "gravite": gravite,
                "index_ligne": int(i),
                "ligne_csv": int(i) + 2,  # l'en-tête compte pour une ligne
                "date": ligne["date"],
                "mois": ligne["mois"],
                "aerodrome": ligne["aerodrome"],
                "type_mouvement": ligne["type_mouvement"],
                "categorie_vol": ligne["categorie_vol"],
                "origine_destination": ligne["origine_destination"],
                "nombre_mouvements": None if pd.isna(valeur) else int(valeur),
                "detail": detail,
                **autres,
            }
        )

    for i in donnees.index[donnees.duplicated(CLE)]:
        memes = donnees[(donnees[CLE] == donnees.loc[i, CLE]).all(axis=1)]
        origine = memes.index[0]
        identique = bool(donnees.loc[i, VALEURS].equals(donnees.loc[origine, VALEURS]))
        if identique:
            detail = f"Identique à la ligne {origine + 2} du CSV"
        else:
            detail = f"Même clé que la ligne {origine + 2} du CSV mais valeurs différentes"
        ajouter("doublon", i, "moyenne", detail, ligne_originale_csv=int(origine) + 2, identique=identique)

    for i in donnees.index[donnees["nombre_mouvements"].isna()]:
        ajouter("mouvements_manquants", i, "haute", "Nombre de mouvements absent, ligne exclue du net")

    for i in donnees.index[donnees["retard_moyen_min"].isna()]:
        ajouter("retard_manquant", i, "basse", "Retard moyen absent, ignoré dans les moyennes")

    for i in donnees.index[donnees["nombre_mouvements"] < 0]:
        ajouter("mouvements_negatifs", i, "haute", "Nombre de mouvements négatif, ligne exclue du net")

    valides = donnees[donnees["nombre_mouvements"] >= 0]
    for aerodrome, groupe in valides.groupby("aerodrome"):
        q1, q3 = groupe["nombre_mouvements"].quantile([0.25, 0.75])
        seuil = q3 + FACTEUR_PIC * (q3 - q1)
        mediane = round(float(groupe["nombre_mouvements"].median()))
        for i in groupe.index[groupe["nombre_mouvements"] > seuil]:
            valeur = int(donnees.loc[i, "nombre_mouvements"])
            detail = (
                f"{valeur} mouvements pour environ {mediane} d'habitude (seuil {round(float(seuil))}). "
                "Erreur de saisie ou vrai événement, à valider par un humain."
            )
            ajouter("pic_atypique", i, "haute", detail, valeur_reference=mediane, seuil=round(float(seuil), 1))

    return anomalies


def construire_net(donnees, anomalies, decisions):
    net = donnees.copy()
    a_retirer = set()
    for a in anomalies:
        if a["type"] in ("doublon", "mouvements_manquants", "mouvements_negatifs"):
            a_retirer.add(a["index_ligne"])
        elif a["type"] == "pic_atypique":
            decision = decisions.get(a["id"])
            if decision and decision["decision"] == "corriger":
                net.loc[a["index_ligne"], "nombre_mouvements"] = a["valeur_reference"]
    return net.drop(index=list(a_retirer))


def rapprochement(donnees, agregats, anomalies, decisions):
    net = construire_net(donnees, anomalies, decisions)
    cle = ["mois", "aerodrome"]
    tableau = (
        pd.concat(
            [
                agregats.set_index(cle)["mouvements_bruts"].rename("agregat"),
                donnees.groupby(cle)["nombre_mouvements"].sum().rename("recalcul"),
                net.groupby(cle)["nombre_mouvements"].sum().rename("net"),
            ],
            axis=1,
        )
        .reset_index()
        .sort_values(cle)
    )

    par_mois = {}
    for a in anomalies:
        par_mois.setdefault((a["mois"], a["aerodrome"]), []).append(a)

    lignes = []
    for _, r in tableau.iterrows():
        du_mois = par_mois.get((r["mois"], r["aerodrome"]), [])
        nombre = {}
        for a in du_mois:
            nombre[a["type"]] = nombre.get(a["type"], 0) + 1

        agregat = None if pd.isna(r["agregat"]) else int(r["agregat"])
        recalcul = int(r["recalcul"])
        net_mois = int(r["net"])
        ecart = None if agregat is None else recalcul - agregat
        negatifs = sum(a["nombre_mouvements"] for a in du_mois if a["type"] == "mouvements_negatifs")

        if agregat is None:
            statut = "absent_des_agregats"
            commentaire = "Mois présent dans les données mais sans agrégat (mois probablement incomplet)."
        elif ecart != 0 and negatifs and ecart == negatifs:
            statut = "ecart_explique"
            commentaire = f"L'agrégat exclut la ligne négative ({negatifs}), d'où un écart de {abs(ecart)}."
        elif ecart != 0:
            statut = "ecart_a_expliquer"
            commentaire = "Le recalcul ne correspond pas à l'agrégat."
        elif net_mois != recalcul:
            statut = "corrige_par_nettoyage"
            commentaire = "L'agrégat correspond aux données brutes, le nettoyage change le total."
        else:
            statut = "ok"
            commentaire = "Aucun écart, aucune correction."

        lignes.append(
            {
                "mois": r["mois"],
                "aerodrome": r["aerodrome"],
                "agregat_brut": agregat,
                "recalcul_brut": recalcul,
                "ecart_agregat_vs_recalcul": ecart,
                "net": net_mois,
                "impact_nettoyage": net_mois - recalcul,
                "anomalies": nombre,
                "statut": statut,
                "commentaire": commentaire,
            }
        )
    return lignes


def jours_du_mois(mois):
    return pd.Period(mois, freq="M").days_in_month


def statistiques(net, referentiel, aerodrome, mois):
    d = net
    if aerodrome:
        d = d[d["aerodrome"] == aerodrome]
    if mois:
        d = d[d["mois"] == mois]
    if d.empty:
        raise LookupError("Aucune donnée pour ce périmètre.")

    total = int(d["nombre_mouvements"].sum())
    jours = int(d["date"].nunique())
    retard = d["retard_moyen_min"].mean()
    resultat = {
        "perimetre": {"aerodrome": aerodrome or "TOUS", "mois": mois or "TOUS"},
        "total_mouvements": total,
        "jours_couverts": jours,
        "moyenne_mouvements_par_jour": round(total / jours, 1),
        "retard_moyen_min": None if pd.isna(retard) else round(float(retard), 1),
    }
    for nom, colonne in [
        ("par_type_mouvement", "type_mouvement"),
        ("par_categorie_vol", "categorie_vol"),
        ("par_origine_destination", "origine_destination"),
    ]:
        somme = d.groupby(colonne)["nombre_mouvements"].sum()
        resultat[nom] = {cle: int(valeur) for cle, valeur in somme.items()}

    if aerodrome:
        base = referentiel.loc[referentiel["code"] == aerodrome, "base_mouvements_jour"]
        if not base.empty:
            base_jour = int(base.iloc[0])
            resultat["base_referentiel_par_jour"] = base_jour
            resultat["ecart_vs_base_pct"] = round((total / jours - base_jour) / base_jour * 100, 1)
    if mois:
        resultat["mois_complet"] = jours == jours_du_mois(mois)
    return resultat


def tendances(net, aerodrome):
    d = net[net["aerodrome"] == aerodrome] if aerodrome else net
    if d.empty:
        raise LookupError("Aucune donnée pour ce périmètre.")
    par_mois = d.groupby("mois").agg(total=("nombre_mouvements", "sum"), jours=("date", "nunique"))
    return [
        {
            "mois": mois,
            "total_mouvements": int(r["total"]),
            "jours_couverts": int(r["jours"]),
            "mois_complet": int(r["jours"]) == jours_du_mois(mois),
            "moyenne_par_jour": round(float(r["total"]) / int(r["jours"]), 1),
        }
        for mois, r in par_mois.iterrows()
    ]
