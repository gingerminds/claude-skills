/*
 * model.example.js — CONTRAT DE DONNÉES du skill gm:archi-c4.
 *
 * Le template (assets/template.html) est FIGÉ. Seul ce bloc de données change.
 * Générer = copier le template puis remplacer LA LIGNE, juste sous le marqueur
 * "@@ARCHI-C4:MODEL@@" :
 *   META = null; CONTEXT = null; CONTAINER = null; COMPONENT = null; CODE = null;
 * par le bloc ci-dessous. Les cinq variables sont pré-déclarées dans le template :
 * un niveau absent peut rester null (ou être omis) => son onglet se masque, sans erreur.
 *
 * Règles : sortie en français.
 * Types de noeud : person | system | external | container | store | component
 * Kinds UML     : interface | abstract | class | trait
 * Kinds d'edge  : flow (C1/C2) | uses (DI, C3) | extends (C3) | extend (C4) | realize | use | assoc
 * COMPONENT.groups : band in single | parsers | domain | domain2 ; grp in one | c2 | c4
 *   (toute autre valeur retombe sur une bande simple colonne).
 * META.footer DOIT porter le stamp de génération "généré le AAAA-MM-JJ · commit <hash-court>"
 *   pour que le flux Maintenir détecte la dérive (voir l'exemple ci-dessous).
 */

var META = {
  eyebrow: "Architecture · Modèle C4",
  title: 'Module <span style="font-family:var(--font-mono);font-weight:640">product_import</span>',
  subtitle: "Exemple de référence : pipeline d'import du catalogue produit depuis le PIM produit.",
  footer: 'Source de vérité : <code>product_import.services.yml</code>, <code>src/Service</code>, <code>product_import.module</code>. Diagramme interactif — survolez ou sélectionnez un élément pour tracer ses relations.<br><small>Généré le 2026-07-10 · commit <code>a1b2c3d</code></small>'
};

var CONTEXT = {
  lead: "<b>Vue de contexte.</b> Le sous-système Product Import relie le PIM produit au site Drupal : il récupère les fichiers déposés sur le bastion SFTP et alimente le catalogue.",
  layout: { type: "grid", cols: 5, rows: 4 },
  nodes: [
    { id: "pim",     type: "external", name: "PIM produit", id2: "système tiers", desc: "Gestion de l'information produit. Publie les fichiers de correspondance FR/EN, images, PDF et DXF.", col: 1, row: 1 },
    { id: "bastion", type: "external", name: "Bastion SFTP", id2: "hébergeur distant", desc: "Hôte distant où PIM dépose les fichiers, dans des dossiers datés AAAAMMJJ.", col: 2, row: 1 },
    { id: "ops",     type: "person",   name: "Développeur / Ops", id2: "acteur", desc: "Déclenche la synchronisation et les imports via Make et Drush.", col: 5, row: 1 },
    { id: "cron",    type: "external", name: "Ordonnanceur (cron)", id2: "planificateur", desc: "Déclenche l'import quotidien et le nettoyage SFTP.", col: 5, row: 2 },
    { id: "sys",     type: "system",   name: "Product Import", id2: "sous-système ", desc: "Synchronise, analyse et importe le catalogue produit dans Drupal.", col: 3, row: 2, hero: true },
    { id: "drupal",  type: "system",   name: "Site Drupal", id2: "CMS hôte", desc: "Reçoit les nœuds produits/familles, médias, liens de menu et termes de taxonomie.", col: 3, row: 3 },
    { id: "es",      type: "external", name: "Elasticsearch", id2: "Search API", desc: "Moteur d'indexation du catalogue et des médias.", col: 3, row: 4 }
  ],
  edges: [
    { from: "pim",     to: "bastion", kind: "flow", label: "Dépose FR/EN, images, PDF, DXF (SFTP)" },
    { from: "ops",     to: "sys",     kind: "flow", label: "make download · drush import" },
    { from: "cron",    to: "sys",     kind: "flow", label: "hook_cron : nettoyage quotidien" },
    { from: "bastion", to: "sys",     kind: "flow", label: "rsync → stockage local" },
    { from: "sys",     to: "drupal",  kind: "flow", label: "crée / met à jour nœuds, médias, menus, termes" },
    { from: "sys",     to: "es",      kind: "flow", label: "suspend / reprend l'indexation directe" },
    { from: "drupal",  to: "es",      kind: "flow", label: "indexe contenus & médias" }
  ]
};

var CONTAINER = {
  lead: "<b>Vue des conteneurs.</b> Trois unités exécutables entourent le module : un script Python de synchronisation, un orchestrateur Bash, et le module Drupal lui-même — reliés par le stockage de fichiers daté.",
  layout: { type: "grid", cols: 5, rows: 5 },
  nodes: [
    { id: "pim",     type: "external",  name: "PIM produit", id2: "système tiers", desc: "Dépose les fichiers sur le SFTP distant.", col: 1, row: 1 },
    { id: "syncpy",  type: "container",  name: "product_sync.py", id2: "CLI Python · rsync", desc: "Synchronise le SFTP distant ↔ stockage local par dossier daté (ENV prod|preprod, DATE).", col: 1, row: 2 },
    { id: "ops",     type: "person",    name: "Développeur / Ops", id2: "acteur", desc: "Lance make download-catalog puis import_catalog.sh.", col: 1, row: 3 },
    { id: "orch",    type: "container",  name: "import_catalog.sh", id2: "orchestrateur Bash", desc: "Enchaîne les commandes Drush d'import (image, pdf, dxf, produits, menus, vignettes).", col: 1, row: 4 },
    { id: "cron",    type: "external",  name: "Ordonnanceur (cron)", id2: "planificateur", desc: "Déclenche hook_cron pour le nettoyage SFTP.", col: 1, row: 5 },
    { id: "storage", type: "store",     name: "Stockage SFTP", id2: "storage/private/default/sftp/AAAAMMJJ", desc: "Dossiers datés contenant fichiers FR/EN, images, PDF, DXF.", col: 3, row: 1, span: 2 },
    { id: "module",  type: "container",  name: "Module product_import", id2: "Drupal 11 · PHP 8.3", desc: "Cœur : commandes Drush, parsers, handlers de contenu/média, services de menu.", col: 3, row: 3, span: 2, hero: true, doc: "units/product-import.html" },
    { id: "db",      type: "store",     name: "MariaDB", id2: "base Drupal", desc: "Nœuds, médias, liens de menu, termes de taxonomie.", col: 5, row: 2 },
    { id: "pdfthumb",type: "external",  name: "media_pdf_thumbnail", id2: "module contrib", desc: "Génère les vignettes des PDF via une file d'attente.", col: 5, row: 3 },
    { id: "es",      type: "external",  name: "Elasticsearch", id2: "Search API", desc: "Indexe contenus & médias.", col: 5, row: 4 }
  ],
  edges: [
    { from: "pim",    to: "storage", kind: "flow", label: "dépôt SFTP (dossier daté)" },
    { from: "ops",    to: "syncpy",  kind: "flow", label: "make download-catalog" },
    { from: "syncpy", to: "storage", kind: "flow", label: "rsync distant → local" },
    { from: "ops",    to: "orch",    kind: "flow", label: "exécute" },
    { from: "cron",   to: "module",  kind: "flow", label: "hook_cron → clean-sftp" },
    { from: "orch",   to: "module",  kind: "flow", label: "drush product:import-*" },
    { from: "module", to: "storage", kind: "flow", label: "lit les fichiers datés" },
    { from: "module", to: "db",      kind: "flow", label: "Entity API : lecture / écriture" },
    { from: "module", to: "pdfthumb",kind: "flow", label: "met les PDF en file" },
    { from: "module", to: "es",      kind: "flow", label: "bascule index_directly" }
  ]
};

var COMPONENT = {
  lead: "<b>Vue des composants.</b> Architecture en couches : commandes Drush → parsers → handlers de contenu/média/menu → traduction, câblés par injection de dépendances. Survolez un composant pour tracer ses liaisons.",
  layout: { type: "comp" },
  nodes: {
    cmd_clean: { type: "component", name: "CleanSftpCommand", id2: "product_import:clean-sftp · nics", desc: "Déplace les fichiers vers le dossier daté et purge les dossiers de plus d'une semaine. Aussi appelé par hook_cron.", file: "src/Drush/Commands/CleanSftpCommand.php" },
    cmd_img:   { type: "component", name: "ImportImageCommand", id2: "product:import-image", desc: "Importe les images. Étend AbstractMediaImportCommand : filtre ElementType 202, suspend l'indexation directe, supprime les médias absents du flux.", file: "src/Drush/Commands/ImportImageCommand.php" },
    cmd_pdf:   { type: "component", name: "ImportPdfCommand", id2: "product:import-pdf", desc: "Importe les PDF (documents téléchargeables). Étend AbstractMediaImportCommand.", file: "src/Drush/Commands/ImportPdfCommand.php" },
    cmd_dxf:   { type: "component", name: "ImportDxfCommand", id2: "product:import-dxf", desc: "Importe les fichiers DXF. Étend AbstractMediaImportCommand.", file: "src/Drush/Commands/ImportDxfCommand.php" },
    cmd_prod:  { type: "component", name: "ImportProductCommand", id2: "product:import-products", desc: "Importe familles, sous-familles et produits. Étend AbstractContentImportCommand.", file: "src/Drush/Commands/ImportProductCommand.php" },
    cmd_menu:  { type: "component", name: "ImportMenuCommand", id2: "product:import-menu --type", desc: "Reconstruit l'arborescence de menu (individual / pro) et y rattache les produits.", file: "src/Drush/Commands/ImportMenuCommand.php" },
    cmd_del:   { type: "component", name: "DeleteImportedItemsCommand", id2: "commande Drush", desc: "Supprime les entités importées depuis le PIM.", file: "src/Drush/Commands/DeleteImportedItemsCommand.php" },
    cmd_thumb: { type: "component", name: "PdfThumbnailQueueCommand", id2: "product:pdf-thumbnail-queue", desc: "Met les médias PDF en file pour la génération de vignettes (media_pdf_thumbnail).", file: "src/Drush/Commands/PdfThumbnailQueueCommand.php" },
    hooks:     { type: "component", name: "Hooks du module", id2: "product_import.module", desc: "hook_cron (nettoyage SFTP), hook_pathauto_alias_alter (+ → -plus), hook_tokens_alter (retire ® des titres).", file: "product_import.module" },
    parser_import:  { type: "component", name: "ImportFileParser", id2: "product_import.file_parser", desc: "Parseur de base des fichiers tabulés FR/EN ; filtrage par ElementType.", file: "src/Service/Parser/ImportFileParser.php" },
    parser_content: { type: "component", name: "ContentImportFileParser", id2: "product_import.content_file_parser", desc: "Étend ImportFileParser : colonnes produit (CodeLang, FMID, FID, PID, RID…), extraction familles / sous-familles.", file: "src/Service/Parser/ContentImportFileParser.php" },
    parser_menu:    { type: "component", name: "MenuImportFileParser", id2: "product_import.menu_file_parser", desc: "Analyse la structure de menu à importer.", file: "src/Service/Parser/MenuImportFileParser.php" },
    util_sftp: { type: "component", name: "SftpPathHelper", id2: "Util (statique)", desc: "Résout le chemin de base SFTP (override par settings) et le chemin daté AAAAMMJJ.", file: "src/Util/SftpPathHelper.php" },
    content_abstract:    { type: "component", name: "AbstractContentHandler", id2: "classe abstraite", desc: "Orchestration commune : traite le FR puis le EN, crée/met à jour, traduit, findOrCreateTerm.", file: "src/Service/Content/AbstractContentHandler.php" },
    content_family:      { type: "component", name: "FamilyProductHandler", id2: "product_import.family_product_handler", desc: "Familles de produits (mode individuel).", file: "src/Service/Content/FamilyProductHandler.php" },
    content_subfamily:   { type: "component", name: "SubfamilyProductHandler", id2: "product_import.subfamily_product_handler", desc: "Sous-familles de produits (individuel).", file: "src/Service/Content/SubfamilyProductHandler.php" },
    content_product:     { type: "component", name: "ProductHandler", id2: "product_import.product_handler", desc: "Produits (individuel).", file: "src/Service/Content/ProductHandler.php" },
    content_family_pro:  { type: "component", name: "FamilyProductProHandler", id2: "product_import.family_product_pro_handler", desc: "Familles de produits (mode Pro).", file: "src/Service/Content/FamilyProductProHandler.php" },
    content_subfam_pro:  { type: "component", name: "SubfamilyProductProHandler", id2: "product_import.subfamily_product_pro_handler", desc: "Sous-familles (Pro).", file: "src/Service/Content/SubfamilyProductProHandler.php" },
    content_product_pro: { type: "component", name: "ProductProHandler", id2: "product_import.product_pro_handler", desc: "Produits (Pro).", file: "src/Service/Content/ProductProHandler.php" },
    media_abstract:  { type: "component", name: "AbstractMediaHandler", id2: "classe abstraite", desc: "Logique commune des médias : extraction ZIP, slugify, mediaExists, create/update, suppression.", file: "src/Service/Media/AbstractMediaHandler.php" },
    media_image:     { type: "component", name: "ImageMediaHandler", id2: "product_import.image_media_handler", desc: "Crée/met à jour les médias image et les traduit.", file: "src/Service/Media/ImageMediaHandler.php" },
    media_pdf:       { type: "component", name: "PdfMediaHandler", id2: "product_import.pdf_media_handler", desc: "Crée/met à jour les médias PDF.", file: "src/Service/Media/PdfMediaHandler.php" },
    media_dxf:       { type: "component", name: "DxfMediaHandler", id2: "product_import.dxf_media_handler", desc: "Crée/met à jour les médias DXF.", file: "src/Service/Media/DxfMediaHandler.php" },
    media_manager:   { type: "component", name: "MediaManager", id2: "product_import.media_manager", desc: "Façade déléguant aux handlers image/PDF et au service de traduction.", file: "src/Service/Media/MediaManager.php" },
    menu_nav:         { type: "component", name: "MenuNavigation", id2: "product_import.menu_navigation", desc: "Lit l'arbre de menu existant via menu.link_tree.", file: "src/Service/Menu/MenuNavigation.php" },
    menu_link:        { type: "component", name: "MenuLinkManager", id2: "product_import.menu_link_manager", desc: "CRUD des entités menu_link_content.", file: "src/Service/Menu/MenuLinkManager.php" },
    menu_productlink: { type: "component", name: "ProductMenuLinker", id2: "product_import.product_menu_linker", desc: "Rattache les produits aux sous-familles dans les menus main / main-pro.", file: "src/Service/Menu/ProductMenuLinker.php" },
    media_translation:{ type: "component", name: "MediaTranslationService", id2: "product_import.media_translation", desc: "Ajoute/synchronise les traductions des entités média.", file: "src/Service/Translation/MediaTranslationService.php" },
    ext_etm:      { type: "external", name: "entity_type.manager", id2: "Drupal core", desc: "Gestionnaire d'entités : nœuds, médias, termes, liens de menu, index Search API.", file: null },
    ext_fs:       { type: "external", name: "file_system", id2: "Drupal core", desc: "Opérations de système de fichiers.", file: null },
    ext_frepo:    { type: "external", name: "file.repository", id2: "Drupal core", desc: "Écriture des fichiers gérés.", file: null },
    ext_menutree: { type: "external", name: "menu.link_tree", id2: "Drupal core", desc: "Construction de l'arbre de menu.", file: null },
    ext_search:   { type: "external", name: "search_api_index", id2: "Search API", desc: "Bascule de l'indexation directe pendant l'import.", file: null },
    ext_pdfthumb: { type: "external", name: "media_pdf_thumbnail", id2: "module contrib", desc: "Managers de file d'attente et d'images pour les vignettes PDF.", file: null }
  },
  edges: [
    { from: "hooks", to: "cmd_clean", kind: "uses" },
    { from: "cmd_img", to: "parser_import", kind: "uses" },
    { from: "cmd_img", to: "media_image", kind: "uses" },
    { from: "cmd_img", to: "ext_etm", kind: "uses" },
    { from: "cmd_img", to: "ext_search", kind: "uses" },
    { from: "cmd_img", to: "util_sftp", kind: "uses" },
    { from: "cmd_pdf", to: "parser_import", kind: "uses" },
    { from: "cmd_pdf", to: "media_pdf", kind: "uses" },
    { from: "cmd_pdf", to: "ext_etm", kind: "uses" },
    { from: "cmd_pdf", to: "ext_search", kind: "uses" },
    { from: "cmd_pdf", to: "util_sftp", kind: "uses" },
    { from: "cmd_dxf", to: "parser_import", kind: "uses" },
    { from: "cmd_dxf", to: "media_dxf", kind: "uses" },
    { from: "cmd_dxf", to: "ext_etm", kind: "uses" },
    { from: "cmd_dxf", to: "ext_search", kind: "uses" },
    { from: "cmd_dxf", to: "util_sftp", kind: "uses" },
    { from: "cmd_prod", to: "parser_content", kind: "uses" },
    { from: "cmd_prod", to: "content_product", kind: "uses" },
    { from: "cmd_menu", to: "parser_menu", kind: "uses" },
    { from: "cmd_menu", to: "menu_nav", kind: "uses" },
    { from: "cmd_menu", to: "menu_link", kind: "uses" },
    { from: "cmd_menu", to: "menu_productlink", kind: "uses" },
    { from: "cmd_menu", to: "util_sftp", kind: "uses" },
    { from: "cmd_del", to: "ext_etm", kind: "uses" },
    { from: "cmd_thumb", to: "ext_etm", kind: "uses" },
    { from: "cmd_thumb", to: "ext_pdfthumb", kind: "uses" },
    { from: "parser_content", to: "parser_import", kind: "extends" },
    { from: "content_family", to: "content_abstract", kind: "extends" },
    { from: "content_subfamily", to: "content_abstract", kind: "extends" },
    { from: "content_product", to: "content_abstract", kind: "extends" },
    { from: "content_family_pro", to: "content_abstract", kind: "extends" },
    { from: "content_subfam_pro", to: "content_abstract", kind: "extends" },
    { from: "content_product_pro", to: "content_abstract", kind: "extends" },
    { from: "content_abstract", to: "ext_etm", kind: "uses" },
    { from: "media_image", to: "media_abstract", kind: "extends" },
    { from: "media_pdf", to: "media_abstract", kind: "extends" },
    { from: "media_dxf", to: "media_abstract", kind: "extends" },
    { from: "media_image", to: "media_translation", kind: "uses" },
    { from: "media_pdf", to: "media_translation", kind: "uses" },
    { from: "media_dxf", to: "media_translation", kind: "uses" },
    { from: "media_image", to: "ext_etm", kind: "uses" },
    { from: "media_image", to: "ext_fs", kind: "uses" },
    { from: "media_image", to: "ext_frepo", kind: "uses" },
    { from: "media_pdf", to: "ext_fs", kind: "uses" },
    { from: "media_pdf", to: "ext_frepo", kind: "uses" },
    { from: "media_dxf", to: "ext_fs", kind: "uses" },
    { from: "media_dxf", to: "ext_frepo", kind: "uses" },
    { from: "media_manager", to: "media_image", kind: "uses" },
    { from: "media_manager", to: "media_pdf", kind: "uses" },
    { from: "media_manager", to: "media_translation", kind: "uses" },
    { from: "menu_nav", to: "ext_menutree", kind: "uses" },
    { from: "menu_link", to: "ext_etm", kind: "uses" },
    { from: "menu_productlink", to: "menu_link", kind: "uses" },
    { from: "menu_productlink", to: "ext_etm", kind: "uses" },
    { from: "media_translation", to: "ext_etm", kind: "uses" }
  ],
  groups: [
    { band: "single", title: "Points d'entrée · <b>Commandes Drush</b>", grp: "c4", ids: ["cmd_img","cmd_pdf","cmd_dxf","cmd_prod","cmd_menu","cmd_thumb","cmd_clean","cmd_del"] },
    { band: "parsers", title: "Analyse · <b>Parsers</b>", grp: "one", ids: ["parser_import","parser_content","parser_menu"] },
    { band: "parsers", title: "<b>Hooks</b>", grp: "one", ids: ["hooks"] },
    { band: "parsers", title: "<b>Utilitaire</b>", grp: "one", ids: ["util_sftp"] },
    { band: "domain", title: "Domaine · <b>Handlers de contenu</b> (nœuds)", grp: "c2", ids: ["content_abstract","content_product","content_family","content_subfamily","content_product_pro","content_family_pro","content_subfam_pro"] },
    { band: "domain", title: "Domaine · <b>Handlers de média</b>", grp: "c2", ids: ["media_abstract","media_manager","media_image","media_pdf","media_dxf"] },
    { band: "domain2", title: "<b>Services de menu</b>", grp: "one", ids: ["menu_nav","menu_link","menu_productlink"] },
    { band: "domain2", title: "<b>Traduction</b>", grp: "one", ids: ["media_translation"] }
  ],
  rail: { title: "Services Drupal <b>(core &amp; contrib)</b>", ids: ["ext_etm","ext_fs","ext_frepo","ext_menutree","ext_search","ext_pdfthumb"] }
};

var CODE = {
  layout: { type: "code" },
  views: {
    content: {
      label: "Handlers de contenu",
      layout: { type: "code" },
      lead: "<b>Niveau code — Handlers de contenu.</b> Le contrat <code>ContentHandlerInterface</code>, le squelette <code>AbstractContentHandler</code>, et la matrice Individuel × Pro (famille, sous-famille, produit) avec les traits partagés.",
      nodes: {
        iface: { kind: "interface", name: "ContentHandlerInterface", col: 5, row: 1, id2: "Service\\Content", file: "src/Service/Content/ContentHandlerInterface.php",
          desc: "Contrat des handlers de contenu : créer/mettre à jour et localiser les nœuds importés.",
          methods: ["+ processContentItems(fr, en)", "+ createOrUpdateContent(data)", "+ getContentBundle()", "+ getContentEntityTypeId()", "+ contentExists(id, lang)", "+ getExternalIdFieldName()"] },
        traitTr: { kind: "trait", name: "ContentTranslationTrait", col: 1, row: 1, id2: "Service\\Content", file: "src/Service/Content/ContentTranslationTrait.php",
          desc: "Traduction EN des nœuds et gestion des champs taxonomie partagés (famille / sous-famille / produit).",
          methods: ["# getOrCreateTranslation()", "+ hasTranslation() / hasFrench…() / hasEnglish…()", "# setTaxonomyField() · setMultipleTaxonomyField()", "# translateCommonProductFields()", "# translateCommonSubfamilyFields()", "# translateCommonFamilyFields()", "… (13 méthodes)"] },
        traitTitle: { kind: "trait", name: "ProductsListingTitleUpdateTrait", col: 9, row: 1, id2: "Service\\Content", file: "src/Service/Content/ProductsListingTitleUpdateTrait.php",
          desc: "Met à jour le titre des pages de listing avec le nombre de produits rattachés.",
          methods: ["+ updateProductsListingTitle()", "# countRelatedProducts()", "# generateProductsListingTitle()", "# isFamily()", "… (9 méthodes)"] },
        abstract: { kind: "abstract", name: "AbstractContentHandler", col: 5, row: 2, id2: "implements ContentHandlerInterface", file: "src/Service/Content/AbstractContentHandler.php",
          desc: "Squelette d'import : traite le FR puis le EN, crée/traduit, et délègue les spécificités aux sous-classes. Utilise StringTranslationTrait.",
          attrs: ["# entityTypeManager", "# loggerFactory"],
          methods: ["+ processContentItems()", "# findCorrespondingItem()", "# findOrCreateTerm()", "# getExternalIdFromItem() {abstract}", "# translateContent() {abstract}", "# translateSpecificFields() {abstract}"] },
        family: { kind: "class", name: "FamilyProductHandler", col: 1, row: 3, id2: "extends AbstractContentHandler", file: "src/Service/Content/FamilyProductHandler.php",
          desc: "Famille de produits (mode individuel). Utilise les deux traits.",
          methods: ["+ getContentBundle() → family_product", "+ createOrUpdateContent()", "# setFamilyProductFields()", "# translateSpecificFields()"] },
        subfamily: { kind: "class", name: "SubfamilyProductHandler", col: 5, row: 3, id2: "extends AbstractContentHandler", file: "src/Service/Content/SubfamilyProductHandler.php",
          desc: "Sous-famille de produits (individuel). Rattache la famille parente.",
          methods: ["+ getContentBundle() → subfamily_product", "+ createOrUpdateContent()", "# setSubfamilyProductFields()", "# findFamilyProductByExternalId()"] },
        product: { kind: "class", name: "ProductHandler", col: 9, row: 3, id2: "extends AbstractContentHandler", file: "src/Service/Content/ProductHandler.php",
          desc: "Produit (individuel). Parse les USP, options, focus techniques et documents.",
          methods: ["+ getContentBundle() → product", "+ createOrUpdateContent()", "# setProductFields()", "# translateSpecificFields()", "# processOptionItems() …"] },
        familyPro: { kind: "class", name: "FamilyProductProHandler", col: 1, row: 4, id2: "extends FamilyProductHandler", file: "src/Service/Content/FamilyProductProHandler.php",
          desc: "Famille (mode Pro) — surcharge les champs et la traduction du mode individuel.",
          methods: ["+ getContentBundle() → family_product_pro", "# setFamilyProductFields()", "# translateSpecificFields()"] },
        subfamilyPro: { kind: "class", name: "SubfamilyProductProHandler", col: 5, row: 4, id2: "extends SubfamilyProductHandler", file: "src/Service/Content/SubfamilyProductProHandler.php",
          desc: "Sous-famille (Pro).",
          methods: ["+ getContentBundle() → subfamily_product_pro", "# setSubfamilyProductFields()", "# findFamilyProductByExternalId()"] },
        productPro: { kind: "class", name: "ProductProHandler", col: 9, row: 4, id2: "extends ProductHandler", file: "src/Service/Content/ProductProHandler.php",
          desc: "Produit (Pro) — spécifications techniques, performances, rapports d'essai.",
          methods: ["+ getContentBundle() → product_pro", "# setProductFields()", "# processPerformanceItems()", "# processTechnicalSpecificationItems()"] }
      },
      edges: [
        { from: "abstract", to: "iface", kind: "realize" },
        { from: "family", to: "abstract", kind: "extend" },
        { from: "subfamily", to: "abstract", kind: "extend" },
        { from: "product", to: "abstract", kind: "extend" },
        { from: "familyPro", to: "family", kind: "extend" },
        { from: "subfamilyPro", to: "subfamily", kind: "extend" },
        { from: "productPro", to: "product", kind: "extend" },
        { from: "family", to: "traitTr", kind: "use" },
        { from: "family", to: "traitTitle", kind: "use" },
        { from: "subfamily", to: "traitTr", kind: "use" },
        { from: "subfamily", to: "traitTitle", kind: "use" },
        { from: "product", to: "traitTr", kind: "use" }
      ]
    },
    media: {
      label: "Handlers de média",
      layout: { type: "code" },
      lead: "<b>Niveau code — Handlers de média.</b> Trois handlers (image, PDF, DXF) dérivent d'<code>AbstractMediaHandler</code> ; <code>MediaManager</code> sert de façade et délègue la traduction.",
      nodes: {
        ifaceMedia: { kind: "interface", name: "MediaHandlerInterface", col: 1, row: 1, id2: "Service\\Media", file: "src/Service/Media/MediaHandlerInterface.php",
          desc: "Contrat commun des handlers de média.",
          methods: ["+ processMediaItems(fr, en)", "+ createOrUpdateMedia(data, src, dst)", "+ getMediaBundle()", "+ getBaseStorageDirectory()", "+ getZipArchivePath(dir)", "+ createDirectory() · slugify()", "+ extractZipArchive() · mediaExists()"] },
        ifaceTr: { kind: "interface", name: "MediaTranslationInterface", col: 5, row: 1, id2: "Service\\Translation", file: "src/Service/Translation/MediaTranslationInterface.php",
          desc: "Contrat de traduction des entités média.",
          methods: ["+ translateMedia(id, data, type)", "+ hasFrenchTranslation(id)", "+ hasEnglishTranslation(id)", "+ hasTranslation(id, lang)"] },
        ifaceMgr: { kind: "interface", name: "MediaManagerInterface", col: 9, row: 1, id2: "Service\\Media", file: "src/Service/Media/MediaManagerInterface.php",
          desc: "Contrat de la façade média.",
          methods: ["+ processMediaImageItems() / …PdfItems()", "+ createOrUpdateMediaImage() / …Pdf()", "+ translateMediaImage() / …Pdf()", "+ extractZipArchive() · mediaExists() …"] },
        absMedia: { kind: "abstract", name: "AbstractMediaHandler", col: 1, row: 2, id2: "implements MediaHandlerInterface", file: "src/Service/Media/AbstractMediaHandler.php",
          desc: "Logique média partagée : extraction ZIP, slug, dédoublonnage, création/màj et suppression des médias absents du flux.",
          attrs: ["# entityTypeManager", "# fileSystem", "# fileRepository", "# loggerFactory", "# mediaTranslation"],
          methods: ["+ processMediaItems()", "+ extractZipArchive() · slugify()", "+ createDirectory() · mediaExists()", "# findOrCreateTerm() · setPriority()", "+ createOrUpdateMedia() {abstract}"] },
        svcTr: { kind: "class", name: "MediaTranslationService", col: 5, row: 2, id2: "implements MediaTranslationInterface", file: "src/Service/Translation/MediaTranslationService.php",
          desc: "Ajoute/synchronise les traductions EN des médias (champs image ou PDF).",
          attrs: ["# entityTypeManager", "# loggerFactory"],
          methods: ["+ translateMedia() · hasTranslation()", "# translateImageFields()", "# translatePdfFields()", "# setTaxonomyField() · setMultiple…()"] },
        mgr: { kind: "class", name: "MediaManager", col: 9, row: 2, id2: "implements MediaManagerInterface", file: "src/Service/Media/MediaManager.php",
          desc: "Façade : délègue aux handlers image et PDF, et au service de traduction.",
          attrs: ["# imageMediaHandler", "# pdfMediaHandler", "# mediaTranslation"],
          methods: ["+ createOrUpdateMediaImage() / …Pdf()", "+ translateMediaImage() / …Pdf()", "+ processMediaImageItems() / …PdfItems()"] },
        image: { kind: "class", name: "ImageMediaHandler", col: 1, row: 3, id2: "extends AbstractMediaHandler", file: "src/Service/Media/ImageMediaHandler.php",
          desc: "Médias image.",
          methods: ["+ createOrUpdateMedia()", "+ getMediaBundle() → image", "+ getBaseStorageDirectory()", "+ getZipArchivePath()"] },
        pdf: { kind: "class", name: "PdfMediaHandler", col: 5, row: 3, id2: "extends AbstractMediaHandler", file: "src/Service/Media/PdfMediaHandler.php",
          desc: "Médias PDF (documents téléchargeables).",
          methods: ["+ createOrUpdateMedia()", "+ getMediaBundle() → pdf", "+ getBaseStorageDirectory()", "+ getZipArchivePath()"] },
        dxf: { kind: "class", name: "DxfMediaHandler", col: 9, row: 3, id2: "extends AbstractMediaHandler", file: "src/Service/Media/DxfMediaHandler.php",
          desc: "Médias DXF.",
          methods: ["+ createOrUpdateMedia()", "+ getMediaBundle() → dxf", "+ getBaseStorageDirectory()", "+ getZipArchivePath()"] }
      },
      edges: [
        { from: "absMedia", to: "ifaceMedia", kind: "realize" },
        { from: "svcTr", to: "ifaceTr", kind: "realize" },
        { from: "mgr", to: "ifaceMgr", kind: "realize" },
        { from: "image", to: "absMedia", kind: "extend" },
        { from: "pdf", to: "absMedia", kind: "extend" },
        { from: "dxf", to: "absMedia", kind: "extend" },
        { from: "absMedia", to: "ifaceTr", kind: "assoc", label: "#mediaTranslation" },
        { from: "mgr", to: "image", kind: "assoc", label: "#imageMediaHandler" },
        { from: "mgr", to: "pdf", kind: "assoc", label: "#pdfMediaHandler" },
        { from: "mgr", to: "ifaceTr", kind: "assoc", label: "#mediaTranslation" }
      ]
    },
    parser: {
      label: "Parsers",
      layout: { type: "code" },
      lead: "<b>Niveau code — Parsers.</b> Analyse des fichiers tabulés FR/EN ; <code>ContentImportFileParser</code> spécialise le parseur de base pour les colonnes produit.",
      nodes: {
        ifaceBase: { kind: "interface", name: "ImportFileParserInterface", col: 1, row: 1, id2: "Service\\Parser", file: "src/Service/Parser/ImportFileParserInterface.php",
          desc: "Contrat de base : lire un fichier tabulé et en extraire des items.",
          methods: ["+ parseFile(path, type)", "+ filterByElementType(items, type)", "+ getIndexes(headers, type)", "+ getHeaderDescription(key)"] },
        ifaceContent: { kind: "interface", name: "ContentImportFileParserInterface", col: 5, row: 1, id2: "extends ImportFileParserInterface", file: "src/Service/Parser/ContentImportFileParserInterface.php",
          desc: "Contrat spécialisé produit : familles, sous-familles, produits.",
          methods: ["+ filterByElementType()", "+ getFamilyProductItems()", "+ getSubfamilyProductItems()", "+ getProductItems()"] },
        ifaceMenu: { kind: "interface", name: "MenuImportFileParserInterface", col: 9, row: 1, id2: "Service\\Parser", file: "src/Service/Parser/MenuImportFileParserInterface.php",
          desc: "Contrat du parseur de menu.",
          methods: ["+ parseFile(path, isFlat)", "+ sortItemsByOrder(&items)"] },
        base: { kind: "class", name: "ImportFileParser", col: 1, row: 2, id2: "implements ImportFileParserInterface", file: "src/Service/Parser/ImportFileParser.php",
          desc: "Parseur de base des fichiers tabulés FR/EN, avec descriptions d'en-têtes.",
          attrs: ["# headerDescriptions", "# loggerFactory"],
          methods: ["+ parseFile() · filterByElementType()", "+ getIndexes()", "+ getHeaderDescription() / …Descriptions()"] },
        content: { kind: "class", name: "ContentImportFileParser", col: 5, row: 2, id2: "extends ImportFileParser", file: "src/Service/Parser/ContentImportFileParser.php",
          desc: "Colonnes produit (CodeLang, FMID, FID, PID, RID…) et extraction famille / sous-famille / produit.",
          methods: ["+ parseFile() · getIndexes()", "+ getFamilyProductItems()", "+ getSubfamilyProductItems()", "+ getProductItems()"] },
        menu: { kind: "class", name: "MenuImportFileParser", col: 9, row: 2, id2: "implements MenuImportFileParserInterface", file: "src/Service/Parser/MenuImportFileParser.php",
          desc: "Analyse la structure de menu (plate ou hiérarchique) et la trie.",
          attrs: ["# loggerFactory"],
          methods: ["+ parseFile()", "+ sortItemsByOrder()"] }
      },
      edges: [
        { from: "base", to: "ifaceBase", kind: "realize" },
        { from: "ifaceContent", to: "ifaceBase", kind: "extend" },
        { from: "content", to: "base", kind: "extend" },
        { from: "content", to: "ifaceContent", kind: "realize" },
        { from: "menu", to: "ifaceMenu", kind: "realize" }
      ]
    }
  }
};
