# Drupal — security

Drupal-specific dependency/advisory audit. Layered on top of the generic `gm:security` methodology (lockfile + audit, runner → host → offline escalation, prioritisation). Assumes `core.md` is loaded. Consumed by `/gm:security`.

## Extract installed versions from the lockfile

Works with zero network, host or runner:

```bash
# Drupal core version
php -r '$l=json_decode(file_get_contents("composer.lock"),true);
foreach(array_merge($l["packages"]??[],$l["packages-dev"]??[]) as $p)
  if($p["name"]==="drupal/core") echo $p["version"],"\n";'

# All drupal contrib + versions + abandoned/pre-release flags
php -r '$l=json_decode(file_get_contents("composer.lock"),true);
foreach(($l["packages"]??[]) as $p){
  if(preg_match("#^drupal/(?!core)#",$p["name"])){
    $ab=!empty($p["abandoned"])?" [ABANDONED]":"";
    $pre=preg_match("#(alpha|beta|-rc|dev-)#i",$p["version"])?" [pre-release]":"";
    echo str_pad($p["name"],45)." ".$p["version"].$ab.$pre."\n";
  }
}'
```

## Drupal tooling (core + contrib)

`composer audit` covers Drupal core and contrib that publish to the advisory DB, but confirm with Drupal's own tooling when a bootstrapped site is available:

```bash
drush pm:security                 # core + contrib modules with an open security advisory (SA-*)
drush pm:security-php             # PHP-library advisories affecting the codebase
drush status --field=drupal-version   # is core itself within a supported / patched release?
```

- Check **drupal/core** version against the latest security release — an EOL minor (e.g. a 9.x past EOL) is itself a finding regardless of named CVEs.
- For contrib, map each advisory to its **SA-CONTRIB-YYYY-NNN** and the fixed version.
- Flag any module that is **unsupported / abandoned** (no security coverage) — a standing risk, not a one-off CVE.
- If drush isn't available (no bootstrapped site), say so and rely on `composer audit` + the core version check; don't claim the contrib advisory pass ran.

## Important — Drupal advisories are NOT on packagist.org

`drupal/*` modules are hosted on `packages.drupal.org`, and their SAs live on Drupal.org, not in packagist's security API. A packagist-only query (or a generic tool that only knows packagist) returns **empty** for Drupal contrib and gives a false "all clear". `composer audit` knows the Drupal endpoint; a manual cross-check must use the **Drupal.org advisory feeds**.

## Offline fallback — cross-reference via the Drupal.org advisory feeds (WebFetch)

When `composer audit` / `drush pm:security` can't run (no shell network, no bootstrap), fetch the **official Drupal advisory feeds** with `WebFetch` and cross-reference each against the versions extracted from `composer.lock`:

```
WebFetch https://www.drupal.org/security/rss.xml          → core SAs (SA-CORE-YYYY-NNN) + fixed versions
WebFetch https://www.drupal.org/security/contrib/rss.xml  → contrib SAs (SA-CONTRIB-YYYY-NNN) + fixed versions
WebFetch https://www.drupal.org/security/psa/rss.xml      → public service announcements
```

For each installed module, compare `installed version` vs the advisory's `Fixed in` version: installed `<` fixed ⇒ **vulnerable**. Drupal.org may print legacy tags like `8.x-3.15` — that maps to composer version `3.15`. The RSS feeds only carry the **most recent ~25 advisories per feed**, so older SAs affecting your modules may not appear — say so and recommend a live `composer audit` to be exhaustive. Don't claim full contrib coverage from RSS alone; do claim the matches you actually found (they're from the authoritative source).

## Drupal-specific remediation

```bash
composer update drupal/core-recommended --with-dependencies   # to the patched release
composer require drupal/<module>:^X.Y                          # bump contrib to the fixed version
```

- A Drupal core update that needs `update.php` / config sync → give the ordered steps, with the DB-backup-first reminder.
- An abandoned/unsupported module → replace, fork-and-patch, or remove — with the trade-offs.
- For a Drupal theme/asset pipeline (gulp/webpack/postcss/sass compiled to static CSS/JS), the *entire* npm tree is effectively build-time — flag those findings as Low real risk and say why, but still run the audit.
