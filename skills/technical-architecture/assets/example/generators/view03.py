"""Example generator: view 03, preproduction infrastructure (one VM), laid out like view 02.

Run: PYTHONPATH=<plugin>/scripts/technical_architecture python3 view03.py <out.drawio>
"""

import sys

from drawio_kit import Diagram

OWNERS = {"hebergeur": "Hébergeur Exemple", "agence": "Gingerminds", "client": "Client Exemple",
          "tiers": "Service tiers"}
STATE = "26/11/2026"


def build() -> Diagram:
    d = Diagram("infra-preprod", "03 · Infra preprod", 1860, 1060)

    internet = d.zone("internet", "Internet", 20, 60, 220, 720, role="internet", icon="internet")
    d.box("recette", "Recette : client\net agence", 15, 130, 190, 60, "internet", parent=internet, icon="users")
    d.box("equipe", "Équipe de l'agence\net CI GitLab", 15, 250, 190, 60, "agence", parent=internet, icon="gitlab")

    host = d.zone("hebergeur", "Hébergeur Exemple : préproduction", 290, 60, 1170, 720, role="hebergeur",
                  icon="building")
    d.box("nat", "Pare-feu et NAT\n203.0.113.20", 40, 130, 150, 180, "hebergeur", parent=host, icon="firewall")
    lan = d.zone("lan", "Réseau privé 198.51.100.0/24", 250, 40, 900, 660, role="hebergeur", parent=host,
                 icon="network", dashed=True)

    vm = d.zone("vm", "VM préproduction · 198.51.100.20", 20, 40, 860, 600, role="hebergeur", parent=lan, icon="vm")
    d.box("proxy", "Proxy nginx\n:443 (TLS)", 20, 50, 150, 60, "agence", parent=vm, icon="docker")
    d.box("app", "Application PHP-FPM : site et API", 230, 50, 460, 60, "agence", parent=vm, icon="docker")
    d.box("ssh", "sshd :22", 20, 170, 150, 60, "hebergeur", parent=vm, icon="ssh")
    d.box("telemetrie", "Agent de\ntélémétrie", 710, 170, 130, 60, "agence", parent=vm, icon="docker")
    d.box("mariadb", "MariaDB\n:3306", 230, 360, 140, 60, "agence", parent=vm, icon="docker")
    d.box("redis", "Redis\n:6379", 390, 360, 140, 60, "agence", parent=vm, icon="docker")
    d.box("postgresql", "PostgreSQL\n:5432", 550, 360, 140, 60, "agence", parent=vm, icon="docker")
    d.box("worker", "Worker\n(tâches)", 550, 480, 140, 60, "agence", parent=vm, icon="docker")
    d.text("note", "Une seule VM porte toute la pile : les échanges avec les bases restent internes "
           "(réseau Docker), d'où des flèches sans identifiant.", 20, 360, 180, 120, parent=vm)

    tiers = d.zone("tiers", "Services tiers", 1500, 60, 340, 720, role="tiers")
    d.box("supervision", "Supervision (SaaS) :\nlogs et métriques", 30, 250, 280, 60, "tiers", parent=tiers,
          icon="monitoring")
    d.box("api-partenaire", "API partenaire\n(bac à sable)", 30, 560, 280, 60, "tiers", parent=tiers, icon="api")

    d.edge("q01a", "Q01 · 443/tcp", "recette", "nat", "visiteur", (1, 0.5), (0, 0.167))
    d.edge("q01b", "Q01 · 443/tcp", "nat", "proxy", "visiteur", (1, 0.167), (0, 0.5))
    d.edge("proxy-app", "", "proxy", "app", "visiteur", (1, 0.5), (0, 0.5))
    d.edge("q02a", "Q02 · 22024/tcp", "equipe", "nat", "admin", (1, 0.5), (0, 0.833))
    d.edge("q02b", "Q02 · 22/tcp", "nat", "ssh", "admin", (1, 0.833), (0, 0.5))
    d.edge("app-mariadb", "", "app", "mariadb", "donnees", (0.152, 1), (0.5, 0), clear=True)
    d.edge("app-redis", "", "app", "redis", "donnees", (0.5, 1), (0.5, 0), clear=True)
    d.edge("app-postgresql", "", "app", "postgresql", "donnees", (0.848, 1), (0.5, 0))
    d.edge("worker-postgresql", "", "worker", "postgresql", "donnees", (0.5, 0), (0.5, 1))
    d.edge("q03", "Q03 · 443/tcp", "worker", "api-partenaire", "donnees", (1, 0.5), (0, 0.5))
    d.edge("q04", "Q04 · 443/tcp", "telemetrie", "supervision", "admin", (1, 0.5), (0, 0.5))

    d.legend(20, 830, OWNERS)
    d.cartouche(1340, 830, 500, 190, "03 · Infrastructure de préproduction", "Préproduction", STATE,
                "Sources : dépôt (compose, CI), relevé SSH en lecture seule du 26/11/2026, "
                "indications de l'hébergeur (NAT, pare-feu).")
    return d


def main() -> None:
    build().write(sys.argv[1])


if __name__ == "__main__":
    main()
