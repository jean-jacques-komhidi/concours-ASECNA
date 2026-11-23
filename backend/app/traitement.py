import pandas as pd

CLE = ["date", "aerodrome", "type_mouvement", "categorie_vol", "origine_destination"]
VALEURS = ["nombre_mouvements", "retard_moyen_min"]
TYPES_ANOMALIES = [
    "doublon",
    "mouvements_manquants",
    "retard_manquant",
    "mouvements_negatifs",
    "pic_atypique",
    "creux_atypique",
    "retard_aberrant",
    "jour_manquant",
]
A_RETIRER = ("doublon", "mouvements_manquants", "mouvements_negatifs")
RETARD_MAX = 180
FENETRE = "31D"
ECART_ROBUSTE = 5
RATIO_PIC = 2
RATIO_CREUX = 0.5
MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
           "septembre", "octobre", "novembre", "décembre"]


def nombre(valeur):
    return f"{valeur:.1f}".replace(".", ",")


def date_fr(jour):
    return pd.Timestamp(jour).strftime("%d/%m/%Y")


def mois_fr(mois):
    annee, numero = mois.split("-")
    return f"{MOIS_FR[int(numero) - 1]} {annee}"


def jours_du_mois(mois):
    return pd.Period(mois, freq="M").days_in_month


def lire_csv(source):
    return pd.read_csv(source, encoding="utf-8-sig")


def verifier_colonnes(donnees, colonnes):
    absentes = [c for c in colonnes if c not in donnees.columns]
    if absentes:
        raise ValueError("colonnes manquantes : " + ", ".join(absentes))


def preparer_trafic(donnees):
    verifier_colonnes(donnees, CLE + VALEURS)
    donnees = donnees.copy()
    if pd.api.types.is_datetime64_any_dtype(donnees["date"]):
        donnees["date"] = donnees["date"].dt.strftime("%Y-%m-%d")
    else:
        donnees["date"] = donnees["date"].astype(str).str[:10]
    donnees["mois"] = donnees["date"].str[:7]
    for colonne in VALEURS:
        donnees[colonne] = pd.to_numeric(donnees[colonne], errors="coerce")
    return donnees


def preparer_agregats(donnees):
    verifier_colonnes(donnees, ["mois", "aerodrome", "mouvements_bruts"])
    donnees = donnees.copy()
    donnees["mois"] = donnees["mois"].astype(str).str[:7]
    for colonne in ["mouvements_bruts", "nombre_lignes"]:
        if colonne in donnees.columns:
            donnees[colonne] = pd.to_numeric(donnees[colonne], errors="coerce")
    return donnees


def preparer_referentiel(donnees):
    verifier_colonnes(donnees, ["code", "base_mouvements_jour"])
    donnees = donnees.copy()
    donnees["base_mouvements_jour"] = pd.to_numeric(donnees["base_mouvements_jour"], errors="coerce")
    return donnees


def lire_trafic(source):
    return preparer_trafic(lire_csv(source))


def vers_json(donnees):
    return donnees.astype(object).where(donnees.notna(), None).to_dict(orient="records")


def plages(jours):
    resultat = []
    for jour in jours:
        if resultat and (jour - resultat[-1][1]).days == 1:
            resultat[-1][1] = jour
        else:
            resultat.append([jour, jour])
    return resultat


def detecter_anomalies(donnees):
    anomalies = []

    def sur_ligne(type_anomalie, i, gravite, detail, **autres):
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
        sur_ligne("doublon", i, "moyenne", detail, ligne_originale_csv=int(origine) + 2, identique=identique)

    for i in donnees.index[donnees["nombre_mouvements"].isna()]:
        sur_ligne("mouvements_manquants", i, "haute", "Nombre de mouvements absent, ligne exclue du net")

    for i in donnees.index[donnees["retard_moyen_min"].isna()]:
        sur_ligne("retard_manquant", i, "basse", "Retard moyen absent, ignoré dans les moyennes")

    for i in donnees.index[donnees["nombre_mouvements"] < 0]:
        sur_ligne("mouvements_negatifs", i, "haute", "Nombre de mouvements négatif, ligne exclue du net")

    retards = donnees["retard_moyen_min"]
    for i in donnees.index[(retards < 0) | (retards > RETARD_MAX)]:
        detail = f"Retard moyen de {retards[i]:g} min, hors de 0 à {RETARD_MAX} min"
        sur_ligne("retard_aberrant", i, "moyenne", detail)

    valides = donnees[(donnees["nombre_mouvements"] >= 0) & ~donnees.duplicated(CLE)]
    for (aerodrome, sens), groupe in valides.groupby(["aerodrome", "type_mouvement"]):
        if len(groupe) < 10:
            continue
        groupe = groupe.assign(jour=pd.to_datetime(groupe["date"])).sort_values("jour")
        serie = pd.Series(groupe["nombre_mouvements"].to_numpy(dtype=float), index=pd.DatetimeIndex(groupe["jour"]))
        habituel = serie.rolling(FENETRE, center=True, min_periods=10).median()
        ecart = serie - habituel
        sigma = max(1.4826 * (ecart - ecart.median()).abs().median(), 1.0)
        rapport = serie / habituel
        pics = (ecart / sigma > ECART_ROBUSTE) & (rapport > RATIO_PIC)
        creux = (ecart / sigma < -ECART_ROBUSTE) & (rapport < RATIO_CREUX)
        for position, i in enumerate(groupe.index):
            normal = round(float(habituel.iloc[position])) if pd.notna(habituel.iloc[position]) else None
            valeur = int(serie.iloc[position])
            if pics.iloc[position]:
                detail = (
                    f"{valeur} mouvements pour environ {normal} d'habitude "
                    f"({nombre(float(rapport.iloc[position]))} fois). "
                    "Erreur de saisie ou vrai événement, à valider par un humain."
                )
                sur_ligne("pic_atypique", i, "haute", detail, valeur_reference=normal)
            elif creux.iloc[position]:
                detail = f"{valeur} mouvements pour environ {normal} d'habitude. Panne, fermeture ou erreur de saisie."
                sur_ligne("creux_atypique", i, "moyenne", detail, valeur_reference=normal)

    for aerodrome, groupe in donnees.groupby("aerodrome"):
        presents = set(pd.to_datetime(groupe["date"]))
        absents = [j for j in pd.date_range(min(presents), max(presents)) if j not in presents]
        for debut, fin in plages(absents):
            duree = (fin - debut).days + 1
            periode = date_fr(debut) if duree == 1 else f"du {date_fr(debut)} au {date_fr(fin)}"
            anomalies.append(
                {
                    "id": f"jour_manquant-{aerodrome}-{debut.strftime('%Y-%m-%d')}",
                    "type": "jour_manquant",
                    "gravite": "moyenne",
                    "index_ligne": None,
                    "ligne_csv": None,
                    "date": debut.strftime("%Y-%m-%d"),
                    "mois": debut.strftime("%Y-%m"),
                    "aerodrome": aerodrome,
                    "nombre_mouvements": None,
                    "detail": f"Aucune ligne {periode} ({duree} jour(s))",
                }
            )

    return anomalies


def ajouter_decisions(anomalies, decisions):
    for a in anomalies:
        decision = decisions.get(a["id"])
        a["decision"] = decision["decision"] if decision else None
    return anomalies


def construire_net(donnees, anomalies, decisions):
    net = donnees.copy()
    a_retirer = set()
    for a in anomalies:
        if a["type"] in A_RETIRER:
            a_retirer.add(a["index_ligne"])
        elif a["type"] == "pic_atypique":
            decision = decisions.get(a["id"])
            if decision and decision["decision"] == "corriger":
                net.loc[a["index_ligne"], "nombre_mouvements"] = a["valeur_reference"]
    return net.drop(index=list(a_retirer))


def rapprochement(donnees, agregats, anomalies, decisions):
    net = construire_net(donnees, anomalies, decisions)
    cle = ["mois", "aerodrome"]
    utilisables = donnees[donnees["nombre_mouvements"] >= 0]
    colonnes = [
        agregats.set_index(cle)["mouvements_bruts"].rename("agregat"),
        donnees.groupby(cle)["nombre_mouvements"].sum().rename("recalcul"),
        net.groupby(cle)["nombre_mouvements"].sum().rename("net"),
        utilisables.groupby(cle).size().rename("lignes_recalcul"),
    ]
    if "nombre_lignes" in agregats.columns:
        colonnes.append(agregats.set_index(cle)["nombre_lignes"].rename("lignes_agregat"))
    tableau = pd.concat(colonnes, axis=1).reset_index().sort_values(cle)

    par_mois = {}
    for a in anomalies:
        par_mois.setdefault((a["mois"], a["aerodrome"]), []).append(a)

    lignes = []
    for _, r in tableau.iterrows():
        du_mois = par_mois.get((r["mois"], r["aerodrome"]), [])
        par_type = {}
        for a in du_mois:
            par_type[a["type"]] = par_type.get(a["type"], 0) + 1

        agregat = None if pd.isna(r["agregat"]) else int(r["agregat"])
        recalcul = 0 if pd.isna(r["recalcul"]) else int(r["recalcul"])
        net_mois = 0 if pd.isna(r["net"]) else int(r["net"])
        ecart = None if agregat is None else recalcul - agregat
        negatifs = sum(a["nombre_mouvements"] for a in du_mois if a["type"] == "mouvements_negatifs")
        lignes_agregat = r.get("lignes_agregat")
        ecart_lignes = None
        if lignes_agregat is not None and not pd.isna(lignes_agregat):
            ecart_lignes = int(0 if pd.isna(r["lignes_recalcul"]) else r["lignes_recalcul"]) - int(lignes_agregat)

        if agregat is None:
            statut = "absent_des_agregats"
            commentaire = "Mois présent dans les données mais sans agrégat (mois probablement incomplet)."
        elif recalcul == 0 and agregat != 0:
            statut = "agregat_sans_donnees"
            commentaire = "Agrégat fourni sans aucune ligne journalière correspondante."
        elif ecart != 0 and negatifs and ecart == negatifs:
            statut = "ecart_explique"
            commentaire = f"L'agrégat exclut la ligne négative ({negatifs}), d'où un écart de {abs(ecart)}."
        elif ecart != 0:
            statut = "ecart_a_expliquer"
            commentaire = "Le recalcul ne correspond pas à l'agrégat."
        elif ecart_lignes:
            statut = "ecart_a_expliquer"
            commentaire = f"Les mouvements correspondent mais pas le nombre de lignes (écart de {abs(ecart_lignes)})."
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
                "ecart_nombre_lignes": ecart_lignes,
                "net": net_mois,
                "impact_nettoyage": net_mois - recalcul,
                "anomalies": par_type,
                "statut": statut,
                "commentaire": commentaire,
            }
        )
    return lignes


def retard_pondere(donnees):
    d = donnees[donnees["retard_moyen_min"].notna() & (donnees["nombre_mouvements"] > 0)]
    poids = d["nombre_mouvements"].sum()
    if not poids:
        return None
    return round(float((d["retard_moyen_min"] * d["nombre_mouvements"]).sum() / poids), 1)


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
    resultat = {
        "perimetre": {"aerodrome": aerodrome or "TOUS", "mois": mois or "TOUS"},
        "total_mouvements": total,
        "jours_couverts": jours,
        "moyenne_mouvements_par_jour": round(total / jours, 1),
        "retard_moyen_min": retard_pondere(d),
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


def decaler_mois(mois, nombre):
    annee, numero = map(int, mois.split("-"))
    n = annee * 12 + numero - 1 + nombre
    return f"{n // 12}-{n % 12 + 1:02d}"


def variation(valeur, ancienne):
    return round(100 * (valeur - ancienne) / ancienne, 1) if ancienne else None


def tendances(net, aerodrome):
    d = net[net["aerodrome"] == aerodrome] if aerodrome else net
    if d.empty:
        raise LookupError("Aucune donnée pour ce périmètre.")
    par_mois = d.groupby("mois").agg(total=("nombre_mouvements", "sum"), jours=("date", "nunique"))
    complets = {m: float(r["total"]) for m, r in par_mois.iterrows() if int(r["jours"]) == jours_du_mois(m)}
    resultat = []
    for mois, r in par_mois.iterrows():
        complet = mois in complets
        resultat.append(
            {
                "mois": mois,
                "total_mouvements": int(r["total"]),
                "jours_couverts": int(r["jours"]),
                "mois_complet": complet,
                "moyenne_par_jour": round(float(r["total"]) / int(r["jours"]), 1),
                "variation_mois_precedent_pct": variation(complets[mois], complets.get(decaler_mois(mois, -1))) if complet else None,
                "variation_annee_precedente_pct": variation(complets[mois], complets.get(decaler_mois(mois, -12))) if complet else None,
            }
        )
    return resultat


def classement_aerodromes(net, referentiel):
    total_reseau = float(net["nombre_mouvements"].sum())
    classement = []
    for _, ref in referentiel.iterrows():
        d = net[net["aerodrome"] == ref["code"]]
        total = int(d["nombre_mouvements"].sum())
        moyenne = round(total / d["date"].nunique(), 1) if len(d) else 0.0
        base = None if pd.isna(ref["base_mouvements_jour"]) else int(ref["base_mouvements_jour"])
        classement.append(
            {
                "aerodrome": ref["code"],
                "nom": None if pd.isna(ref.get("nom_fictif")) else ref.get("nom_fictif"),
                "total_mouvements": total,
                "part_pct": round(100 * total / total_reseau, 1) if total_reseau else 0.0,
                "moyenne_par_jour": moyenne,
                "base_referentiel_par_jour": base,
                "ecart_vs_base_pct": variation(moyenne, base),
                "retard_moyen_min": retard_pondere(d),
            }
        )
    return sorted(classement, key=lambda c: -c["total_mouvements"])


def totaux_mensuels(net):
    par_mois = net.groupby("mois").agg(total=("nombre_mouvements", "sum"), jours=("date", "nunique"))
    return [
        {
            "mois": mois,
            "total_mouvements": int(r["total"]),
            "mois_complet": int(r["jours"]) == jours_du_mois(mois),
        }
        for mois, r in par_mois.iterrows()
    ]
