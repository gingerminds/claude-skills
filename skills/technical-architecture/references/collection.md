# Collecting the facts

The views and the matrix only show what is known, and say how it is known. Collect from the repositories first, then from the servers, and log every fact.

## 1. Repositories first

Read, in the current repository and in the other repositories of question 2 (read-only):

- compose files and Dockerfiles: services, images, published ports, networks, volumes, which service runs where;
- the CI file and deploy scripts: build, registry, deploy targets, the account and the method used to connect;
- proxy and web server configs (nginx, Apache, Traefik, Varnish, HAProxy...): domains, TLS, upstreams;
- crontabs and scheduled jobs versioned in the repository, workers, queues;
- `.env.example` and settings files: **key names only**, they reveal databases, caches, SMTP, external APIs, SSO;
- the existing docs.

What the repositories say is « décidé » (it is the intended state) until a server confirms it.

## 2. Then each server, read-only over SSH

Connect only through the aliases the user chose at question 3, one command per call: `ssh <alias> '<command>'`. Never try another host, user or key.

**Read commands you run without asking:**

- identity and network: `hostname`, `hostnamectl`, `ip -brief addr`, `ip route`;
- listening ports: `ss -tlnp` (and `ss -ulnp` when a UDP service is expected);
- containers: `docker ps --format '{{.Names}}\t{{.Image}}\t{{.Ports}}'`, `docker network ls`, `docker network inspect --format '{{.Name}} {{range .Containers}}{{.Name}} {{end}}' <network>`;
- container details, only through a `--format` limited to non-environment fields, for example `docker inspect --format '{{.Name}} {{.Config.Image}} {{json .NetworkSettings.Ports}} {{.HostConfig.RestartPolicy.Name}}' <container>`;
- SSH daemon: `sshd -T | grep -Ei '^(port|passwordauthentication|permitrootlogin|pubkeyauthentication)'` (if it needs root, see `sudo` below);
- scheduled jobs and services: `crontab -l`, `cat /etc/crontab`, `ls /etc/cron.d`, `systemctl list-units --type=service --state=running --no-pager`;
- `cat` of a deployed compose file, a proxy config, or the config of a log or monitoring agent (it names the destinations), as long as it is not a secret file.

Pipe through the local mask every output that may carry a credential (crontabs, configs, compose files, unit files), so a secret never reaches the conversation. It hides the value after a key such as `password=`, `token:` or `Authorization:`; it is a safety net, not a licence to read secret files:

```bash
ssh <alias> 'crontab -l' | perl -CSD -Mutf8 -pe 's/((?:pass(?:word|wd)?|pwd|secret|token|api[_-]?key|authorization)[^=:\s]*[=:\s]+)(?:(?:Bearer|Basic)\s+)?\S+/${1}[masqué]/gi'
```

**Forbidden:**

- `docker inspect` without such a `--format` (it prints the environment variables, so the secrets);
- `docker compose config` (it resolves and prints the `.env` values);
- reading a `.env` file, a key, a certificate's private part or any secret file; of a `.env`, only the key names, through `cut -d= -f1`;
- `sudo`, except a closed list of read commands shown to the user first and run after their yes: `sudo iptables -S`, `sudo nft list ruleset`, `sudo sshd -T` (with the grep above), `sudo ss -tlnp`;
- any connection outside the configured aliases.

**Anything else, or a command whose impact you are unsure of**, is shown to the user with its impact and run only after their agreement. A command that writes, restarts or deletes is never part of the collection. When in doubt, treat it as a write: the agency rule applies even when the user asks to go faster.

**A secret seen in an output** (a password in a crontab, a token in a config) is never copied: not in the facts log, not in a view, not in the README, not in your messages. Note only that a credential sits there, if it matters (« mot de passe en clair dans la crontab de root » is a known issue, not the password).

## 3. The facts log

Write every fact in `docs/architecture/build/faits.md` (git-ignored), grouped by machine or source, one line per fact:

```markdown
## VM web (alias exemple-prod-web)

- `nginx` publie `443/tcp` sur toutes les interfaces ; source : `docker ps` ; 26/11/2026
- sshd accepte les mots de passe ; source : `sshd -T` ; 26/11/2026
- sauvegardes faites par l'hébergeur ; source : indiqué par l'équipe ; 26/11/2026
```

The log gives each flow of the matrix its status:

| How the fact is known | Status in the matrix |
|-----------------------|----------------------|
| seen on the server, or in a deployed config, on that date | `constaté (JJ/MM/AAAA)` |
| in the repositories only (intended state), or agreed in a decision | `décidé` |
| told by someone (the client, the host, a teammate) | `indiqué par <qui> (JJ/MM/AAAA)` |
| neither seen nor told | `à confirmer` |

Keep the log after the create mode: the update mode compares the new facts with it.
