# Backend

API FastAPI de l'agent. Tous les chiffres sont calculés ici, l'agent les explique.

## Installation

    cd backend
    python3 -m venv venv
    source venv/bin/activate        # Windows : venv\Scripts\activate
    pip install -r requirements.txt

## Lancement

    uvicorn app.main:app --reload

Test des endpoints : http://localhost:8000/docs
Description complète : http://localhost:8000/openapi.json
Description pour Copilot Studio (9 outils) : http://localhost:8000/copilot/openapi.json

## Tests

    pip install -r requirements-dev.txt
    pytest -q

## Fichiers

    app/main.py          routes et format des erreurs
    app/traitement.py    anomalies, net, rapprochement, statistiques, tendances
    app/explication.py   explications en français (jour, mois)
    app/fichiers.py      lecture des CSV et Excel
    app/openapi_copilot.py   OpenAPI 3.0 pour Copilot Studio
    data/                données chargées au démarrage
    tests/

## Endpoints

    GET   /health
    GET   /resume                              à appeler en premier
    GET   /tableau-de-bord                     écran d'accueil en un appel
    GET   /aerodromes
    POST  /donnees                             CSV ou Excel, voir plus bas
    GET   /lignes                              lignes du CSV avec leurs anomalies, paginées
    GET   /controle                            filtres : type, aerodrome
    GET   /rapprochement                       filtres : uniquement_ecarts, aerodrome
    GET   /stats                               filtres : aerodrome, mois (AAAA-MM)
    GET   /tendances                           filtre : aerodrome
    GET   /explication/jour/{aerodrome}/{jour}
    GET   /explication/mois/{aerodrome}/{mois}
    POST  /anomalies/{id}/decision             corps : {"decision": "corriger" | "conserver"}
    GET   /decisions
    GET   /copilot/openapi.json

Toutes les erreurs ont la forme {"detail": "texte"}.

## Anomalies détectées

    doublon                 ligne exclue du net
    mouvements_manquants    ligne exclue du net
    mouvements_negatifs     ligne exclue du net
    retard_manquant         ligne gardée, ignorée dans la moyenne des retards
    retard_aberrant         retard hors de 0 à 180 min, signalé
    pic_atypique            gardé et signalé tant qu'une personne n'a pas décidé
    creux_atypique          signalé
    jour_manquant           signalé

Un pic est comparé à la médiane des 31 jours autour de lui, par aérodrome et par sens.
Avec "corriger", il est remplacé par cette médiane. Avec "conserver", il reste.

## Envoi de données

POST /donnees accepte un CSV ou un Excel (.xlsx). Le trafic, le référentiel et les agrégats sont reconnus
d'après leurs colonnes, un Excel peut contenir les trois feuilles. Seules les tables trouvées sont remplacées.
Les décisions humaines sont effacées quand le trafic change.

## Copilot Studio

Importer http://adresse-publique/copilot/openapi.json. La variable URL_PUBLIQUE fixe l'adresse écrite dans la spec,
sinon elle est déduite de la requête. La décision humaine n'est pas dans les outils de l'agent, elle passe par l'interface.

## À savoir

- Données et décisions sont en mémoire, perdues au redémarrage.
- Pas d'authentification. CORS ouvert à tous, à restreindre avec la variable CORS_ORIGINES (adresses séparées par des virgules).
- Copilot Studio ne voit pas localhost : il faut une adresse HTTPS (hébergement ou ngrok).
