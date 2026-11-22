# Backend

API FastAPI de l'agent. Tous les chiffres sont calculés ici, l'agent les explique.

## Installation

    cd backend
    python3 -m venv venv
    source venv/bin/activate        # Windows : venv\Scripts\activate
    pip install -r requirements.txt

## Lancement

    uvicorn app.main:app --reload

Documentation et tests des endpoints : http://localhost:8000/docs
Description pour l'agent : http://localhost:8000/openapi.json

## Tests

    pip install -r requirements-dev.txt
    pytest -q

## Endpoints

    GET   /health
    GET   /resume                          à appeler en premier
    GET   /aerodromes
    POST  /donnees                         remplace le CSV de trafic
    GET   /controle                        filtres : type, aerodrome
    GET   /rapprochement                   filtres : uniquement_ecarts, aerodrome
    GET   /stats                           filtres : aerodrome, mois (AAAA-MM)
    GET   /tendances                       filtre : aerodrome
    POST  /anomalies/{anomalie_id}/decision    corps : {"decision": "corriger" | "conserver"}
    GET   /decisions

## Nettoyage

Lignes retirées du net : doublons, mouvements manquants, mouvements négatifs.
Retard manquant : ligne gardée, ignorée dans la moyenne des retards.
Pic atypique : gardé et signalé tant qu'une personne n'a pas décidé. Avec "corriger", il est remplacé par la médiane de l'aérodrome.

## À savoir

- Données et décisions sont en mémoire, perdues au redémarrage.
- Pas d'authentification, CORS ouvert à tous.
- Copilot Studio ne voit pas localhost : il faut une adresse HTTPS (hébergement ou ngrok).
