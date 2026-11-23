import copy

from fastapi.openapi.utils import get_openapi


def schema_complet(app):
    return get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)


def garder_operations(schema, identifiants):
    schema = copy.deepcopy(schema)
    chemins = {}
    for chemin, item in schema["paths"].items():
        gardees = {m: op for m, op in item.items() if op.get("operationId") in identifiants}
        if gardees:
            chemins[chemin] = gardees
    schema["paths"] = chemins

    composants = schema.get("components", {}).get("schemas", {})
    utiles = set()

    def parcourir(noeud):
        if isinstance(noeud, dict):
            ref = noeud.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                nom = ref.rsplit("/", 1)[1]
                if nom not in utiles:
                    utiles.add(nom)
                    parcourir(composants.get(nom, {}))
            for valeur in noeud.values():
                parcourir(valeur)
        elif isinstance(noeud, list):
            for valeur in noeud:
                parcourir(valeur)

    parcourir(schema["paths"])
    schema.setdefault("components", {})["schemas"] = {k: v for k, v in composants.items() if k in utiles}
    return schema


def convertir(noeud):
    if isinstance(noeud, list):
        return [convertir(x) for x in noeud]
    if not isinstance(noeud, dict):
        return noeud
    noeud = {k: convertir(v) for k, v in noeud.items()}

    for cle in ("anyOf", "oneOf"):
        options = noeud.get(cle)
        if isinstance(options, list) and any(isinstance(o, dict) and o.get("type") == "null" for o in options):
            reste = [o for o in options if not (isinstance(o, dict) and o.get("type") == "null")]
            noeud.pop(cle)
            noeud["nullable"] = True
            if len(reste) == 1:
                if "$ref" in reste[0]:
                    noeud["allOf"] = [reste[0]]
                else:
                    noeud.update(reste[0])
            elif reste:
                noeud[cle] = reste

    if isinstance(noeud.get("examples"), list):
        exemples = noeud.pop("examples")
        if exemples:
            noeud["example"] = exemples[0]
    if "const" in noeud:
        noeud["enum"] = [noeud.pop("const")]
    if noeud.get("contentMediaType") == "application/octet-stream":
        noeud.pop("contentMediaType")
        noeud["format"] = "binary"
    noeud.pop("contentEncoding", None)
    return noeud


def vers_30(schema, serveur=None):
    sortie = convertir(copy.deepcopy(schema))
    sortie["openapi"] = "3.0.3"
    if serveur:
        sortie["servers"] = [{"url": serveur}]
    return sortie
