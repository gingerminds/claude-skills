# Checklist Qualité GM — snapshot

Source of truth: Notion « Checklist Qualité GM [Modèle à dupliquer] » (data source `collection://198af1a3-d8e4-81fb-a01c-000bb7f846b7`). This file is a **versioned snapshot** — regenerate it with `/gm:quality sync`, never edit rules by hand without syncing Notion too.

**Snapshot du : 2026-10-16** — 139 règles.

## Légende

- **ID** — stable identifier, cite it in every report (`SEC-11`, `A11Y-03`…). Never renumber: new rules get the next free number in their category.
- **Sect.** — `F` front, `B` back, `FB` both.
- **Technos** — `*` = toutes (Laravel, Drupal, Nuxt, WordPress) ; sinon `L` Laravel, `D` Drupal, `N` Nuxt, `W` WordPress.
- **MEL** — `🔴` bloquant pour la mise en ligne, `·` non bloquant.
- **Vérif** — how the rule can be checked:
  - `code` — verifiable by reading the source / the diff.
  - `config` — verifiable in repo config files (env, server conf, robots, framework config) — sometimes only partially.
  - `recette` — needs a running environment, a tool, or a human (manual QA, infra, process). Never claim it as verified from code.

### Correspondance stack → techno

Stack names from `shared/stack-detect.md`: `laravel` → `L`, `drupal` → `D`, `vue` (Vue/Nuxt) → `N`, `wordpress` → `W`. For a stack absent from the checklist (`django`, `python`, generic `php`/`js`), apply only the `*` rules.

## Besoins projet

Not every rule fits every project: an extranet has no SEO, a site without upload doesn't need SEC-01. The project's needs are recorded once in its **profil qualité** (`.gm/quality-profile.md`, see `/gm:quality profile`) and the table below turns them into rules. A rule is **applicable** when its techno and section match **and** its condition below holds. A rule listed nowhere here has no condition: it always applies.

Conditions: `a | b` = a or b, `a & b` = a and b.

### Besoins (clés du profil)

| Clé | Signification |
|---|---|
| `seo` | Le site doit être indexé par les moteurs de recherche |
| `public` | Le front est accessible sans authentification (vitrine, éditorial, e-commerce) — `non` pour un extranet / intranet / API seule |
| `multilingue` | Plusieurs langues de contenu |
| `multisite` | Plusieurs sites sur une même base de code / instance |
| `accessibilite` | Le front doit respecter les guidelines d'accessibilité |
| `formulaires` | Formulaires de saisie côté front (contact, candidature, inscription…) |
| `comptes` | Comptes utilisateurs / authentification côté front |
| `uploads` | Upload de fichiers par les utilisateurs ou le BO |
| `api` | L'application expose une API |
| `backoffice` | Back-office d'administration |
| `ecommerce` | Tunnel d'achat / paiement |
| `tracking` | Outils de tracking / cookies tiers (analytics, GTM, pixels…) |
| `listings` | Pages de listing avec recherche et/ou pagination |
| `medias` | Médias audio / vidéo |
| `starter` | Projet initialisé depuis un starter GM |

Derived needs (never asked, computed): `formulaires_publics` = `formulaires & public` ; `donnees_perso` = `formulaires | comptes | ecommerce`.

### Conditions par règle

| Condition | Règles |
|---|---|
| `seo` | SEO-06 to SEO-10, SEO-12, SEO-13, SEO-15, SEO-17, SEO-19 to SEO-24 |
| `seo \| accessibilite` | SEO-01, SEO-05, SEO-14, SEO-16 |
| `multilingue` | SEO-02 |
| `seo & multilingue` | SEO-03, SEO-04 |
| `seo & listings` | SEO-06, SEO-07, SEO-12 (in addition to `seo`) |
| `public` | SEO-25, QUAL-01 |
| `accessibilite` | A11Y-01 to A11Y-32 |
| `accessibilite & medias` | A11Y-01, A11Y-10, A11Y-17 (in addition to `accessibilite`) |
| `accessibilite & formulaires` | A11Y-08, A11Y-22, A11Y-24, A11Y-29 (in addition to `accessibilite`) |
| `multisite` | QUAL-04 |
| `ecommerce` | QUAL-07 |
| `api` | QUAL-03, QUAL-06, SEC-21 |
| `api & comptes` | SEC-18 |
| `comptes` | SEC-07, SEC-08, QUAL-28 |
| `comptes \| formulaires_publics` | SEC-09 |
| `formulaires_publics` | SEC-15 |
| `formulaires` | QUAL-14, QUAL-29 |
| `donnees_perso` | RGPD-01, RGPD-02, RGPD-04, RGPD-06 |
| `formulaires & donnees_perso` | RGPD-05 |
| `tracking` | RGPD-03 |
| `uploads` | SEC-01, SEC-24, ECO-07 |
| `uploads & backoffice` | QUAL-12 |
| `backoffice` | SEC-22 |
| `listings` | ECO-15 |
| `starter` | SEC-17 |

When a rule appears on several lines, **all** its conditions must hold (e.g. SEO-06 needs `seo` and `listings`). SEO-11 (préprod non indexable) has no condition on purpose: a preprod must never be indexed, whatever the project. For a non-indexed project (`seo: non`), the **prod** must be non-indexable too — flag it if it isn't.

## SÉCURITÉ

| ID | Règle | Sect. | Technos | MEL | Vérif |
|---|---|---|---|---|---|
| SEC-01 | Ne pas rendre les uploads de fichiers client publics (ex. CV) : les stocker dans un dossier privé non accessible | B | L D W | 🔴 | code |
| SEC-02 | Scanner le site avec WPScan | B | W | 🔴 | recette |
| SEC-03 | Les clefs de sécurité (APP_KEY / JWT_SECRET / AUTH_KEY…) sont uniques au projet et à l'environnement | FB | * | 🔴 | config |
| SEC-04 | Les dépendances (npm & composer) sont sur les versions les plus récentes disponibles et peuvent être mises à jour (mineures) automatiquement | FB | * | 🔴 | config |
| SEC-05 | Les fichiers type `INSTALL.mysql.txt`, `license.txt`, `install.php`… ne sont pas accessibles publiquement | FB | * | · | config |
| SEC-06 | Contrôleurs : valider les paramètres entrants de la requête | B | L D W | · | code |
| SEC-07 | Avoir une politique de mot de passe sécurisée | FB | * | 🔴 | code |
| SEC-08 | Les URLs et fonctionnalités limitées à certains rôles ne sont pas accessibles depuis un compte aux droits restreints (élévation de privilèges) | FB | * | 🔴 | code |
| SEC-09 | Un mécanisme anti-brute force est en place sur les formulaires publics | FB | * | 🔴 | code |
| SEC-10 | Réduire la verbosité des messages d'erreur en production (pas de requête SQL, chemins serveur…) | B | L D W | 🔴 | config |
| SEC-11 | XSS : ne jamais insérer de donnée non vérifiée dans le HTML | FB | * | 🔴 | code |
| SEC-12 | Un htpasswd (log/pass unique) ou un filtre IP applicatif protège la préprod et, en prod, les accès non publics (BO…) | FB | * | 🔴 | recette |
| SEC-13 | Si présente, la librairie datatable affiche les données brutes et n'évalue pas les valeurs affichées (injection XSS) | B | L D W | 🔴 | code |
| SEC-14 | Les en-têtes HTTP de sécurité sont bien configurés | FB | * | 🔴 | config |
| SEC-15 | Un captcha est présent sur les formulaires publics | FB | * | 🔴 | code |
| SEC-16 | Éviter les requêtes brutes en BDD | B | L D W | 🔴 | code |
| SEC-17 | En cas d'utilisation d'un starter, supprimer toute URL / formulaire / etc. hérité et inutile au projet | FB | * | 🔴 | code |
| SEC-18 | Supprimer tout retour API permettant l'énumération de comptes utilisateurs | B | L | 🔴 | code |
| SEC-19 | Aucune clef, credential, webhook ou donnée sensible en clair dans le code | FB | * | 🔴 | code |
| SEC-20 | Pas d'algorithme obsolète (MD5/SHA1) : utiliser un algorithme fort (ex. Argon2) | B | L D W | 🔴 | code |
| SEC-21 | La config CORS des API ne laisse passer que les domaines et méthodes HTTP nécessaires | B | L D W | 🔴 | config |
| SEC-22 | Les identifiants BO sont uniques au projet et à l'environnement | B | L D W | 🔴 | recette |
| SEC-23 | Les cookies déposés par l'application sont sécurisés (Secure, HttpOnly, SameSite) | FB | * | 🔴 | code |
| SEC-24 | Configurer Glide pour n'autoriser que les dimensions et fonctions utiles au projet | B | L | 🔴 | config |

## RGPD

| ID | Règle | Sect. | Technos | MEL | Vérif |
|---|---|---|---|---|---|
| RGPD-01 | Mettre en place un email pour gérer les demandes de droits d'accès / modification des données des internautes | F | N D W | 🔴 | recette |
| RGPD-02 | Mettre en place une conservation des données limitée dans le temps | B | L D W | · | code |
| RGPD-03 | Mettre en place un outil de CMP | F | N D W | 🔴 | config |
| RGPD-04 | Ne collecter que les données vraiment nécessaires | FB | * | 🔴 | code |
| RGPD-05 | Case à cocher sur les formulaires dont les données sont stockées + enregistrement en BDD (conditions acceptées + date) | FB | * | 🔴 | code |
| RGPD-06 | Mettre en place une page « Données personnelles » | F | N D W | 🔴 | recette |

## QUALITÉ

| ID | Règle | Sect. | Technos | MEL | Vérif |
|---|---|---|---|---|---|
| QUAL-01 | Ajouter une bannière informant de la compatibilité navigateurs | F | N D W | · | code |
| QUAL-02 | Le projet est configuré en SSR ou SPA selon le contexte projet | F | N | 🔴 | config |
| QUAL-03 | Ajuster le middleware de rate limit (throttle) sur l'API | B | L | · | config |
| QUAL-04 | Projet multisite : recetter chaque fonctionnalité dans un contexte multisite | FB | * | 🔴 | recette |
| QUAL-05 | Compléter le README du projet | FB | * | · | code |
| QUAL-06 | La doc API (Swagger) est à jour, 100 % fonctionnelle et iso avec le comportement réel (params, réponses…) | B | * | · | code |
| QUAL-07 | Le tunnel d'achat a été testé jusqu'au paiement | FB | L N | 🔴 | recette |
| QUAL-08 | Pas de `<meta http-equiv="...">` dans la page (utiliser `<meta charset>` en HTML5) | FB | * | 🔴 | code |
| QUAL-09 | Les CSS sont appelées via `<link>` plutôt que `@import` dans le HTML et le CSS (fonts…) | F | * | · | code |
| QUAL-10 | Les pictos SVG non administrables sont gérés en typo (icomoon) ou en sprite | F | * | · | code |
| QUAL-11 | Les classes CSS sont en anglais et en kebab-case | F | * | · | code |
| QUAL-12 | Visuels dans le BO : indiquer dimensions min/max, poids max et formats autorisés | B | L D W | · | code |
| QUAL-13 | Les styles d'un composant sont scopés à ce seul composant | F | N | 🔴 | code |
| QUAL-14 | Les éléments de formulaire sont du bon type HTML5, en accord avec leur contenu | FB | * | · | code |
| QUAL-15 | Pas d'erreur JS ; les `console.log` / `dump` ont été supprimés | FB | * | · | code |
| QUAL-16 | Les événements JS se basent sur un id ou sur des classes CSS préfixées `js-` | FB | * | · | code |
| QUAL-17 | Mode debug désactivé en prod | B | L D W | 🔴 | config |
| QUAL-18 | Le favicon est en place | FB | * | 🔴 | recette |
| QUAL-19 | Les pictos et logos de la charte sont en SVG | F | * | · | code |
| QUAL-20 | Une CSS dédiée à l'impression est présente | F | N D W | · | code |
| QUAL-21 | Utilisation de la balise `<picture>` sur les images | F | N D W | · | code |
| QUAL-22 | Les CSS sont appelées en haut de page et le JS en bas de page | F | * | · | code |
| QUAL-23 | Les images ont la bonne extension selon leur contenu | F | * | · | code |
| QUAL-24 | Le `.env.example` est à jour avec toutes les variables d'env nécessaires | FB | * | 🔴 | code |
| QUAL-25 | Les pages sont testées sur l'ensemble des résolutions | F | * | 🔴 | recette |
| QUAL-26 | En cas d'import de font-face, `font-display: swap;` est présent | F | * | · | code |
| QUAL-27 | Les textes fixes traduisibles sont encapsulés dans `t()`, `trans()`, `__()`… | FB | * | 🔴 | code |
| QUAL-28 | Tester les pages en mode connecté et non connecté | F | N D W | 🔴 | recette |
| QUAL-29 | Tous les formulaires ont été testés et les emails de confirmation sont reçus (idem à chaque MAJ CMS) | FB | * | 🔴 | recette |
| QUAL-30 | La page a une note acceptable sur Yellow Lab Tools | FB | * | · | recette |
| QUAL-31 | Les pages importantes sont valides W3C dans la mesure du possible | F | * | · | recette |

## SEO

| ID | Règle | Sect. | Technos | MEL | Vérif |
|---|---|---|---|---|---|
| SEO-01 | Structure de balisage Hn cohérente et correcte | F | * | 🔴 | code |
| SEO-02 | Les traductions des pages s'affichent dans la langue voulue | FB | * | 🔴 | recette |
| SEO-03 | Le sitemap.xml contient les contenus de toutes les langues | FB | * | 🔴 | recette |
| SEO-04 | Balises hreflang dans le `<head>` des sites multilingues | FB | * | 🔴 | code |
| SEO-05 | Attribut `lang` renseigné sur `<html>` | F | N D W | 🔴 | code |
| SEO-06 | Comportement correct des query strings et du meta title sur les pages paginées | FB | * | · | code |
| SEO-07 | Le moteur de recherche du site expose le paramètre de recherche en query string | FB | N D W | · | code |
| SEO-08 | Le robots.txt disallow les URLs de test et toute URL inutile à l'indexation | F | * | 🔴 | config |
| SEO-09 | Le robots.txt contient le chemin vers le sitemap.xml | F | * | 🔴 | config |
| SEO-10 | Plusieurs sitemaps par typologie (articles, produits, images, vidéos, PDF…) regroupés dans un index | FB | * | 🔴 | config |
| SEO-11 | La préprod est rendue non indexable par le robots.txt | FB | * | 🔴 | config |
| SEO-12 | Balises `rel prev` / `rel next` dans le `<head>` des pages paginées | FB | N D W | · | code |
| SEO-13 | Données structurées selon le type de contenu | FB | * | · | code |
| SEO-14 | Les liens du menu restent visibles et cliquables avec JavaScript ET CSS désactivés | F | N D W | 🔴 | code |
| SEO-15 | `<link rel="canonical">` présent dans le `<head>` (même page unique) | FB | * | 🔴 | code |
| SEO-16 | Les liens sont de vrais liens HTML (`<a href="">`) | F | N D W | 🔴 | code |
| SEO-17 | Pas de duplication d'URL ni de domaine | B | * | 🔴 | config |
| SEO-18 | La page 404 répond en code 404 sans redirection | FB | * | 🔴 | code |
| SEO-19 | Pas d'URL en `/node/{id}` | FB | D | 🔴 | config |
| SEO-20 | Les URLs sont propres | FB | * | 🔴 | code |
| SEO-21 | Il n'y a pas de pages orphelines | FB | * | 🔴 | recette |
| SEO-22 | Contrôle du bon fonctionnement des redirections 301 | F | N D W | 🔴 | recette |
| SEO-23 | Les metas OpenGraph sont présentes et bien utilisées | F | * | · | code |
| SEO-24 | Installer la Google Search Console (via balise HTML) | F | N D W | · | recette |
| SEO-25 | Crédits Gingerminds dans les mentions légales / page crédits / bas de page | F | * | · | recette |

## ACCESSIBILITÉ

All rules: Sect. `F`, Technos `N D W`, MEL `·`.

| ID | Règle | Vérif |
|---|---|---|
| A11Y-01 | Les sons déclenchés automatiquement sont contrôlables par l'utilisateur | code |
| A11Y-02 | Un contrôle sur les délais et mises à jour automatiques est mis à disposition | code |
| A11Y-03 | Chaque image / picto porteur de sens a une alternative textuelle (`alt`) | code |
| A11Y-04 | Les fonctionnalités à geste complexe ont une alternative à geste simple | code |
| A11Y-05 | Chaque lien a un style qui permet de l'identifier comme lien | code |
| A11Y-06 | L'ordre de tabulation dans le contenu est cohérent | code |
| A11Y-07 | Les messages de statut (toast, alert, dialog…) sont compatibles avec les technologies d'assistance | code |
| A11Y-08 | Chaque élément de formulaire a un label associé explicitement (masqué si besoin) | code |
| A11Y-09 | Les `line-height` n'ont pas d'unité | code |
| A11Y-10 | Les médias temporels sont clairement identifiables | code |
| A11Y-11 | Les scripts qui initient un changement de contexte avertissent l'utilisateur | code |
| A11Y-12 | Chaque ID est unique dans la page | code |
| A11Y-13 | Les tableaux (`<table>`) complexes ont un résumé | code |
| A11Y-14 | Chaque graphique / canvas a une alternative textuelle | code |
| A11Y-15 | Les images / pictos décoratifs et le contenu caché sont masqués aux technologies d'assistance | code |
| A11Y-16 | Chaque élément porteur d'un événement JS (onclick, focus, blur…) est accessible au clavier et à tout dispositif de pointage | code |
| A11Y-17 | Les médias temporels (vidéo, podcast) ont des sous-titres synchronisés | recette |
| A11Y-18 | Une page « plan du site » existe et est pertinente | recette |
| A11Y-19 | La prise de focus est visible sur chaque élément focusable | code |
| A11Y-20 | Les cadres (`frame`, `iframe`) ont un titre | code |
| A11Y-21 | Les contenus additionnels au survol / focus peuvent être rendus visibles au clavier et à tout dispositif de pointage | code |
| A11Y-22 | Le contrôle de saisie des formulaires est correctement configuré et structuré | code |
| A11Y-23 | Le texte reste lisible avec une taille de caractères à 200 % | recette |
| A11Y-24 | L'auto-complétion des champs de formulaire (`autocomplete`) est correctement configurée | code |
| A11Y-25 | Les actions critiques déclenchées par un seul clic peuvent être annulées | code |
| A11Y-26 | Structure HTML5 cohérente | code |
| A11Y-27 | Les balises ne sont pas utilisées uniquement à des fins de présentation | code |
| A11Y-28 | Chaque lien a un texte explicite sur sa cible | code |
| A11Y-29 | Les champs de formulaire de même nature sont regroupés (`fieldset`/`legend`) | code |
| A11Y-30 | Des liens d'évitement sont présents et focusables immédiatement au clavier | code |
| A11Y-31 | Chaque élément cliquable est utilisé à bon escient (`<button>` pour une action, `<a>` pour une navigation) | code |
| A11Y-32 | La page tend à être valide AXE (hors contraste, qui dépend surtout de la créa) | recette |

## ÉCO-CONCEPTION / WEBPERFORMANCE

| ID | Règle | Sect. | Technos | MEL | Vérif |
|---|---|---|---|---|---|
| ECO-01 | Adapter la mise en cache navigateur selon la ressource | FB | * | 🔴 | config |
| ECO-02 | Le cache applicatif backend est configuré et activé | B | L D W | 🔴 | config |
| ECO-03 | Compression Brotli (en priorité) / deflate / gzip activée | FB | * | 🔴 | config |
| ECO-04 | Format WebP pour les visuels | FB | * | · | code |
| ECO-05 | Concaténer et minifier CSS et JS | F | N D W | · | config |
| ECO-06 | Si possible, preload les scripts JS | F | N D W | · | code |
| ECO-07 | Les visuels sont limités à l'upload (~4 Mo) et compressés automatiquement à l'enregistrement | B | L D W | · | code |
| ECO-08 | Lazy load sur visuels / vidéos / iframes | F | N D W | · | code |
| ECO-09 | Les dimensions du visuel chargé correspondent au format d'affichage | F | N D W | · | code |
| ECO-10 | Optimiser le chargement des webfonts | F | N D W | · | code |
| ECO-11 | Limiter les ressources JS et CSS ; préférer les ressources locales aux externes | FB | * | · | code |
| ECO-12 | Attributs `width` et `height` sur les `<img>` | F | N D W | · | code |
| ECO-13 | Différer le parsing du JS (`async` / `defer`), le plus bas possible dans la page | F | N D W | · | code |
| ECO-14 | Supprimer ou déplacer les redirections du `.htaccess` | FB | * | · | config |
| ECO-15 | Index custom de recherche présents et bien configurés en BDD | B | L D W | · | code |
| ECO-16 | Le CSS au-dessus de la ligne de flottaison peut être inliné dans le HTML | FB | * | · | code |
| ECO-17 | Ne pas dépasser 3 niveaux d'imbrication en SCSS (si possible) | F | * | · | code |
| ECO-18 | La page a une note acceptable sur PageSpeed / Lighthouse | FB | * | · | recette |

## PROCESS INTERNE

| ID | Règle | Sect. | Technos | MEL | Vérif |
|---|---|---|---|---|---|
| PROC-01 | Ajouter le site sur UptimeKuma | FB | * | 🔴 | recette |
| PROC-02 | Ajouter la ou les applications sur Wasabi (au niveau du projet) | FB | * | 🔴 | recette |
| PROC-03 | À la MEL, désactiver (avec l'hébergeur) le domaine temporaire (ex. `xxx.prod.hebergement-gm.fr`) | FB | * | 🔴 | recette |
