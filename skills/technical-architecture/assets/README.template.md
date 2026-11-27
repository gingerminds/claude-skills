# Architecture technique

Documentation de référence de l'architecture technique de <la plateforme ...> : hébergement, réseau, flux et accès, en <production et préproduction>. Elle s'adresse à <l'équipe de l'agence (développement, exploitation) et au client, qui y trouve la référence de son infrastructure>.

Chaque vue répond à une question et porte sa date (« État au ») : elle ne montre que ce qui a été constaté, décidé ou indiqué, et marque le reste « à confirmer ». Le détail exhaustif des flux est dans la [matrice des flux](#matrice-des-flux), dont les identifiants (`P01`, `Q02`...) figurent sur les flèches.

## Vues

| Vue | Question à laquelle elle répond |
|-----|---------------------------------|
| [01 · <Titre de la vue>](views/01-<slug>.drawio.svg) | <Question à laquelle la vue répond ?> |

### 01 · <Titre de la vue>

<Ce que la vue montre, pour qui, et ce qu'elle laisse de côté.>

![<Titre de la vue> : <ce qu'elle montre>](views/01-<slug>.drawio.svg)

## Légende

Chaque vue porte sa propre légende ; elle est reprise ici.

- **Couleur d'un bloc = qui le gère** : bleu pour <l'hébergeur> (réseau, pare-feu, VM), vert pour <l'agence> (conteneurs, configuration, déploiement), orange pour <le client> (système d'information, utilisateurs), violet pour un service tiers.
- **Gris en pointillés** : élément ou flux à confirmer.
- **Cadres** : zones réseau et machines (VM), imbriquées ; les blocs à l'intérieur d'une VM sont ses conteneurs ou services.
- **Pictogrammes** : utilisateurs, Internet, réseau, VM, conteneur Docker, accès SSH (`sshd :22`, avec le port public qui y mène). Les autres pictogrammes illustrent le bloc qu'ils accompagnent.
- **Couleur d'une flèche = nature du flux** : noir pour le trafic des visiteurs, bleu pour les flux de données, rouge pour l'administration, le déploiement et la supervision, gris pour un flux à confirmer.
- **Trait plein** : flux chiffré (TLS, SSH). **Pointillés et mention « (clair) »** : flux non chiffré.
- **Étiquette d'une flèche** : identifiant de la matrice des flux, port et protocole (« P02 · 3306/tcp (clair) »). Les flèches sans identifiant sont des échanges internes à une VM, sur le réseau Docker.
- **Arc sur une flèche** : deux flux qui se croisent sans se rejoindre.
- **Cartouche** : titre, environnement, date de l'état décrit (« État au »), sources.

## Matrice des flux

Une ligne par flux qui traverse une frontière de machine (les échanges internes à une VM, sur le réseau Docker, sont dessinés sans identifiant). Identifiants stables : <`P` pour la production, `Q` pour la préproduction, `X` pour ce qui ne dépend pas d'un environnement>. Un numéro n'est jamais réattribué : un flux supprimé laisse son numéro libre.

Statuts : `constaté (JJ/MM/AAAA)`, `décidé`, `indiqué par <qui> (JJ/MM/AAAA)`, `à confirmer`.

### <Production>

| # | Source | Destination | Port / protocole | Chiffré | Authentification | Finalité | Géré par | Statut |
|---|--------|-------------|------------------|---------|------------------|----------|----------|--------|
| <P01> | <source, machine> | <destination, machine, adresse> | <`443/tcp` HTTPS> | <oui (TLS) / non> | <méthode, sans secret> | <à quoi sert le flux> | <qui le gère> | <statut> |

## Points d'attention connus

État au <JJ/MM/AAAA>. Une ligne par point : ce qui est constaté (ou indiqué), son effet, et les flux concernés.

| # | Constat | Effet | Détail |
|---|---------|-------|--------|
| <A01> | <ce qui est constaté ou indiqué> | <effet sur la sécurité, la disponibilité ou l'exploitation> | <`P02`, `Q03`> |

## Modifier un schéma

Les vues sont des fichiers `.drawio.svg` : des images SVG qui embarquent leur diagramme draw.io. Les forges (GitLab, GitHub) les affichent comme des images, et draw.io les ouvre et les enregistre telles quelles.

- **À la souris** : ouvrir le fichier dans draw.io (application de bureau, ou [diagrams.net](https://app.diagrams.net) via *Fichier > Ouvrir depuis > Appareil*), modifier, puis enregistrer au même format, en gardant une copie du diagramme et en incorporant les images.
- **Texte simple** : les libellés sont en texte simple (option *Texte formaté* décochée, onglet *Texte* du panneau *Format*). Un libellé en texte formaté fait embarquer par draw.io une image de secours dans le SVG et rend le texte moins fiable à l'impression.
- **Pictogrammes** : ils viennent des bibliothèques intégrées de draw.io et font partie de leur bloc. Pour un nouveau bloc avec pictogramme, copier un bloc existant puis changer son texte.
- **En éditant le XML** (cas de Claude) : `python3 docs/architecture/tools/architecture.py extract <vue> <source.drawio>`, modifier la source, puis `python3 docs/architecture/tools/architecture.py render <source.drawio> <vue>`.
- **Ensuite, toujours** : `make diagrams`. La cible régénère les images (draw.io dans Docker, rien à installer) et vérifie que chaque vue a son cartouche et sa légende, que chaque identifiant de flux d'une vue figure dans la matrice et inversement, que les statuts sont valides, et qu'aucun secret ni image externe ne s'est glissé dans les vues.

Règles :

- Une vue et la matrice se mettent à jour **dans le même changement** (commit, MR) que l'infrastructure, le déploiement ou le flux qu'elles décrivent, et la date du cartouche avec elles.
- Aucun secret (mot de passe, jeton, clé) : les adresses, ports, noms d'hôtes et comptes de service sont acceptés.
- Ce qui n'est pas confirmé reste marqué « à confirmer », jamais deviné.

Dossier PDF : `make architecture-pdf` le génère à la demande dans `docs/architecture/build/`, qui n'est pas versionné. Il réunit une page de titre, une vue par page, puis la légende, la matrice des flux (sans la colonne statut) et les points d'attention connus. `make architecture-pdf SANS_POINTS_ATTENTION=1` produit la variante sans les points d'attention, à transmettre hors de l'équipe.

Outillage : `docs/architecture/tools/architecture.py` (Python 3.9 ou plus, bibliothèque standard), réglé par `docs/architecture/tools/architecture.json` (titre, nom du PDF, préfixes des flux, noms des acteurs).
