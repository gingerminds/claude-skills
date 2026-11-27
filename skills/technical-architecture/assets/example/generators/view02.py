"""Example generator: view 02, production infrastructure (two VMs).

Run: PYTHONPATH=<plugin>/scripts/technical_architecture python3 view02.py <out.drawio>
"""

import sys

from drawio_kit import Diagram

OWNERS = {"hebergeur": "Hébergeur Exemple", "agence": "Gingerminds", "client": "Client Exemple",
          "tiers": "Service tiers"}
STATE = "26/11/2026"


def build() -> Diagram:
    d = Diagram("infra-prod", "02 · Infra prod", 1860, 1060)

    internet = d.zone("internet", "Internet", 20, 60, 220, 720, role="internet", icon="internet")
    d.box("visiteurs", "Visiteurs et\ncontributeurs", 15, 130, 190, 60, "internet", parent=internet, icon="users")
    d.box("equipe", "Équipe de l'agence\net CI GitLab", 15, 250, 190, 250, "agence", parent=internet, icon="gitlab")

    host = d.zone("hebergeur", "Hébergeur Exemple : production", 290, 60, 1170, 720, role="hebergeur",
                  icon="building")
    d.box("nat", "Pare-feu et NAT\n203.0.113.10", 40, 130, 150, 370, "hebergeur", parent=host, icon="firewall")
    lan = d.zone("lan", "Réseau privé 192.0.2.0/24", 250, 40, 900, 660, role="hebergeur", parent=host,
                 icon="network", dashed=True)

    web = d.zone("vm-web", "VM web · 192.0.2.11", 20, 40, 860, 260, role="hebergeur", parent=lan, icon="vm")
    d.box("proxy", "Proxy nginx\n:443 (TLS)", 20, 50, 150, 60, "agence", parent=web, icon="docker")
    d.box("app", "Application PHP-FPM : site et API", 230, 50, 460, 60, "agence", parent=web, icon="docker")
    d.box("ssh-web", "sshd :22", 20, 170, 150, 60, "hebergeur", parent=web, icon="ssh")
    d.box("telemetrie", "Agent de\ntélémétrie", 710, 170, 130, 60, "agence", parent=web, icon="docker")

    data = d.zone("vm-donnees", "VM données · 192.0.2.12", 20, 340, 860, 300, role="hebergeur", parent=lan,
                  icon="vm")
    d.box("ssh-donnees", "sshd :22", 20, 60, 150, 60, "hebergeur", parent=data, icon="ssh")
    d.box("mariadb", "MariaDB\n:3306", 230, 60, 140, 60, "agence", parent=data, icon="docker")
    d.box("redis", "Redis\n:6379", 390, 60, 140, 60, "agence", parent=data, icon="docker")
    d.box("postgresql", "PostgreSQL\n:5432", 550, 60, 140, 60, "agence", parent=data, icon="docker")
    d.box("worker", "Worker\n(tâches)", 550, 180, 140, 60, "agence", parent=data, icon="docker")

    tiers = d.zone("tiers", "Services tiers", 1500, 60, 340, 720, role="tiers")
    d.box("supervision", "Supervision (SaaS) :\nlogs et métriques", 30, 250, 280, 60, "tiers", parent=tiers,
          icon="monitoring")
    d.box("api-partenaire", "API partenaire\n(commandes)", 30, 560, 280, 60, "tiers", parent=tiers, icon="api")

    d.edge("p01a", "P01 · 443/tcp", "visiteurs", "nat", "visiteur", (1, 0.5), (0, 0.081))
    d.edge("p01b", "P01 · 443/tcp", "nat", "proxy", "visiteur", (1, 0.081), (0, 0.5))
    d.edge("proxy-app", "", "proxy", "app", "visiteur", (1, 0.5), (0, 0.5))
    d.edge("p06a", "P06 · 22022/tcp", "equipe", "nat", "admin", (1, 0.12), (0, 0.405))
    d.edge("p06b", "P06 · 22/tcp", "nat", "ssh-web", "admin", (1, 0.405), (0, 0.5))
    d.edge("p07a", "P07 · 22023/tcp", "equipe", "nat", "admin", (1, 0.88), (0, 0.919))
    d.edge("p07b", "P07 · 22/tcp", "nat", "ssh-donnees", "admin", (1, 0.919), (0, 0.5))
    d.edge("p02", "P02 · 3306/tcp (clair)", "app", "mariadb", "donnees", (0.152, 1), (0.5, 0), clear=True)
    d.edge("p04", "P04 · 6379/tcp (clair)", "app", "redis", "donnees", (0.5, 1), (0.5, 0), clear=True)
    d.edge("p03", "P03 · 5432/tcp", "app", "postgresql", "donnees", (0.848, 1), (0.5, 0))
    d.edge("worker-postgresql", "", "worker", "postgresql", "donnees", (0.5, 0), (0.5, 1))
    d.edge("p05", "P05 · 443/tcp", "worker", "api-partenaire", "confirm", (1, 0.5), (0, 0.5))
    d.edge("p08", "P08 · 443/tcp", "telemetrie", "supervision", "admin", (1, 0.5), (0, 0.5))

    d.legend(20, 830, OWNERS)
    d.cartouche(1340, 830, 500, 190, "02 · Infrastructure de production", "Production", STATE,
                "Sources : dépôt (compose, CI), relevé SSH en lecture seule du 26/11/2026, "
                "indications de l'hébergeur (NAT, pare-feu).")
    return d


def main() -> None:
    build().write(sys.argv[1])


if __name__ == "__main__":
    main()
