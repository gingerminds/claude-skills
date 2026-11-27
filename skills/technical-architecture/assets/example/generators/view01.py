"""Example generator: view 01, overview (who uses the platform, which systems it talks to).

Run: PYTHONPATH=<plugin>/scripts/technical_architecture python3 view01.py <out.drawio>
"""

import sys

from drawio_kit import Diagram

OWNERS = {"hebergeur": "Hébergeur Exemple", "agence": "Gingerminds", "client": "Client Exemple",
          "tiers": "Service tiers"}
STATE = "26/11/2026"


def build() -> Diagram:
    d = Diagram("vue-ensemble", "01 · Vue d'ensemble", 1860, 1060)

    users = d.zone("utilisateurs", "Utilisateurs", 20, 60, 300, 520, role="internet", icon="internet")
    d.box("visiteurs", "Visiteurs du site", 20, 60, 260, 60, "internet", parent=users, icon="users")
    d.box("contributeurs", "Contributeurs\ndu client", 20, 190, 260, 60, "client", parent=users, icon="users")

    platform = d.zone("plateforme", "Plateforme Exemple chez Hébergeur Exemple", 480, 60, 560, 400,
                      role="hebergeur", icon="building")
    d.box("production", "Production\n\n2 VM : web et données\nsite, API, bases, worker", 30, 50, 240, 280,
          "hebergeur", parent=platform, icon="servers")
    d.box("preproduction", "Préproduction\n\n1 VM : même pile", 310, 50, 220, 280, "hebergeur", parent=platform,
          icon="vm")

    client = d.zone("si-client", "Système d'information du client", 20, 620, 300, 160, role="client",
                    icon="building")
    d.box("sso", "Fournisseur d'identité\n(SSO)", 20, 60, 260, 60, "client", parent=client, icon="identity")

    agency = d.zone("agence", "Outillage de l'agence", 480, 620, 870, 160, role="agence", icon="workflow")
    d.box("equipe", "Équipe de l'agence et CI GitLab", 120, 60, 320, 60, "agence", parent=agency, icon="gitlab")
    d.box("registre", "Registre d'images", 580, 60, 240, 60, "agence", parent=agency, icon="registry")

    tiers = d.zone("tiers", "Services tiers", 1160, 60, 400, 400, role="tiers")
    d.box("audience", "Mesure d'audience", 40, 50, 320, 60, "tiers", parent=tiers, icon="dashboard")
    d.box("supervision", "Supervision (SaaS)", 40, 170, 320, 60, "tiers", parent=tiers, icon="monitoring")
    d.box("api-partenaire", "API partenaire", 40, 290, 320, 60, "tiers", parent=tiers, icon="api")

    d.edge("consultation", "consultation (P01)", "visiteurs", "production", "visiteur", (1, 0.5), (0, 0.143))
    d.edge("back-office", "back-office (P01)", "contributeurs", "production", "visiteur", (1, 0.3), (0, 0.564))
    d.edge("recette", "recette (Q01)", "contributeurs", "preproduction", "visiteur", (1, 0.7), (0.5, 1),
           points=((420, 292), (420, 425), (900, 425)), label_seg=2, label_frac=0.2)
    d.edge("connexion", "connexion (X03)", "contributeurs", "sso", "confirm", (0.2, 1), (0.2, 0))
    d.edge("admin-prod", "administration,\ndéploiement (P06, P07)", "equipe", "production", "admin",
           (0.2, 0), (0.642, 1))
    d.edge("admin-preprod", "administration,\ndéploiement (Q02)", "equipe", "preproduction", "admin",
           (0.8, 0), (0.3, 1))
    d.edge("images", "images (X02)", "equipe", "registre", "donnees", (1, 0.5), (0, 0.5))
    d.edge("statistiques", "statistiques de visite (X01), chargées par le navigateur", "visiteurs", "audience",
           "visiteur", (0.5, 0), (0.5, 0), points=((170, 36), (1360, 36)), label_seg=1)
    d.edge("supervision-flux", "logs, métriques\n(P08, Q04)", "plateforme", "supervision", "admin",
           (1, 0.5), (0, 0.5))
    d.edge("commandes", "commandes\n(P05, Q03)", "plateforme", "api-partenaire", "donnees", (1, 0.8), (0, 0.5))

    d.legend(20, 830, OWNERS)
    d.cartouche(1340, 830, 500, 190, "01 · Vue d'ensemble", "Production et préproduction", STATE,
                "Sources : dépôt (compose, CI), relevé SSH en lecture seule du 26/11/2026, "
                "indications du client (SSO, mesure d'audience).")
    return d


def main() -> None:
    build().write(sys.argv[1])


if __name__ == "__main__":
    main()
