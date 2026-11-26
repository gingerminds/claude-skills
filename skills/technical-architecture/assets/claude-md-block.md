<!-- gm:technical-architecture -->
**Schémas d'architecture : signaler, puis demander.** Les vues et la matrice des flux de [`docs/architecture/`](docs/architecture/README.md) servent de référence à l'agence et au client. Dès qu'un changement peut les rendre fausses, le signaler explicitement à l'utilisateur, en nommant les vues et les identifiants de flux touchés (`P06`, `Q03`...), et lui demander s'il faut les mettre à jour. Ne pas les modifier sans son accord ; s'il accepte, la mise à jour va dans le même changement (commit, MR), date « État au » du cartouche comprise, puis `make diagrams`. Sont concernés notamment :

- machines, réseau, adresses, ports publiés ou translatés, pare-feu, proxy et WAF ;
- comptes et accès (SSH, SFTP, back-office, interfaces d'administration), clés et méthodes d'authentification ;
- chaîne CI/CD et déploiement (runners, registre, jobs, cibles) ;
- conteneurs et services (ajout, retrait, déplacement d'une machine à l'autre) ;
- services tiers appelés par la plateforme ou qui l'appellent, flux de données (imports, exports, médias, logs, supervision, sauvegardes).

Un constat fait en lecture seule sur un serveur qui contredit une vue suit la même règle. Pour une passe complète (relevé des serveurs, liste des écarts), lancer `/gm:technical-architecture mise-a-jour`. Le dossier PDF n'est pas versionné : il se génère à la demande (`/gm:technical-architecture pdf`).
<!-- /gm:technical-architecture -->
