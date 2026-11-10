import json
import shlex
import tempfile
import unittest
from pathlib import Path

from cqa import fix_commands

AGENCY_IMAGE = "registry.gitlab.gingerminds.fr/gm/docker-images/php-8.2-node-16-composer-2:latest"
PINNED_NODE = "node:18-alpine@sha256:" + "ab12" * 16


def write(root, path, content):
    target = Path(root) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content if isinstance(content, str) else json.dumps(content),
                      encoding="utf-8")


def names(commands):
    return [item["name"] for item in commands]


def kinds(commands):
    return [(item["name"], item["kind"]) for item in commands]


def noted(notes, job):
    return [note for note in notes if f"job « {job} » écarté" in note]


class CiTest(unittest.TestCase):
    def test_ci_build_job_is_split_into_install_and_build(self):
        ci = {
            "stages": ["build"],
            "build:npm": {
                "stage": "build",
                "image": PINNED_NODE,
                "before_script": "cd $NPM_DIRECTORY",
                "script": ["npm ci", "npx gulp production", "cd $CI_PROJECT_DIR"],
                "variables": {"NPM_DIRECTORY": "src/theme/assets"},
            },
        }
        commands, notes = fix_commands.from_ci(ci)
        self.assertEqual(notes, [])
        self.assertEqual(kinds(commands), [("build:npm (install)", "install"),
                                           ("build:npm", "build")])
        self.assertEqual(
            [item["command"] for item in commands],
            ["cd $NPM_DIRECTORY && npm ci",
             "cd $NPM_DIRECTORY && npx gulp production && cd /work"],
        )
        for item in commands:
            self.assertEqual(item["env"], {"NPM_DIRECTORY": "src/theme/assets"})
            self.assertEqual(item["source"], ".gitlab-ci.yml (build:npm)")
            self.assertEqual((item["dir"], item["image"], item["private"]), ("", PINNED_NODE, False))
            self.assertEqual(
                set(item), {"name", "dir", "image", "kind", "command", "env", "source", "private"})

    def test_install_at_the_start_of_a_lint_or_test_job_is_split_too(self):
        ci = {"image": "php:8.2-cli", "lint": {"script": ["composer install", "vendor/bin/phpcs"]},
              "test": {"script": ["php -v", "composer install --no-dev", "vendor/bin/phpunit"]}}
        commands, notes = fix_commands.from_ci(ci)
        self.assertEqual(kinds(commands), [
            ("lint (install)", "install"), ("lint", "lint"),
            ("test (install)", "install"), ("test", "test")])
        self.assertEqual(commands[1]["command"], "vendor/bin/phpcs")
        self.assertEqual(commands[2]["command"], "php -v && composer install --no-dev")
        self.assertEqual(commands[3]["command"], "vendor/bin/phpunit")

    def test_ci_jobs_with_secrets_docker_or_deploy_are_left_out(self):
        ci = {
            "image": "node:18-alpine",
            "stages": ["build", "quality", "deploy"],
            "build:wordpress": {"stage": "build", "script": [
                "npm ci", "npm run build -- --registry-token $ACR_PASSWORD"]},
            "deploy:staging": {"stage": "deploy", "script": ["npm run deploy"]},
            "quality:sonarqube": {"stage": "quality", "script": [
                "sonar-scanner -Dsonar.login=$SONAR_TOKEN"]},
            "push:cache": {"stage": "build", "script": ["docker push registry.example.org/cache"]},
        }
        commands, notes = fix_commands.from_ci(ci)
        self.assertEqual(commands, [])
        self.assertEqual(len(notes), 4, notes)
        for job, why in (("build:wordpress", "ACR_PASSWORD"), ("deploy:staging", "déploi"),
                         ("quality:sonarqube", "SONAR_TOKEN"), ("push:cache", "push")):
            with self.subTest(job=job):
                found = noted(notes, job)
                self.assertEqual(len(found), 1, notes)
                self.assertIn(why, found[0])

    def test_deployment_names_and_stages_are_left_out(self):
        jobs = {}
        for label in ("deploy-api", "release:prod", "push-image", "publish_docs", "pages",
                      "build-pages", "Deploy"):
            jobs[label] = {"stage": "build", "script": ["npm run build"]}
        jobs["compile"] = {"stage": "publish", "script": ["npm run build"]}
        jobs["assets"] = {"stage": "build", "script": ["npm run build"]}
        commands, notes = fix_commands.from_ci({"image": "node:18-alpine", **jobs})
        self.assertEqual(names(commands), ["assets"])
        self.assertEqual(len(notes), len(jobs) - 1, notes)

    def test_non_literal_variables_are_never_retained(self):
        ci = {
            "image": "node:18-alpine",
            "variables": {"LITERAL_FILE": "from-file", "SECRET_FILE": "$VAULT_TOKEN"},
            "ok": {"stage": "build", "variables": {"LITERAL_JOB": "yes", "DIR": "src"},
                   "script": ["cd ${DIR} && npm run build -- $LITERAL_FILE $LITERAL_JOB",
                              "cd $CI_PROJECT_DIR"]},
            "defined-from-secret": {"stage": "build", "script": ["npm run build $SECRET_FILE"]},
            "overridden-by-secret": {"stage": "build", "variables": {"LITERAL_FILE": "$OTHER"},
                                     "script": ["npm run build $LITERAL_FILE"]},
            "predefined": {"stage": "build", "script": ["npm run build $CI_COMMIT_SHA"]},
            "braced": {"stage": "build", "script": ["npm run build ${REGISTRY_USER}"]},
            "undefined": {"stage": "build", "script": ["echo $HOME && npm run build"]},
        }
        commands, notes = fix_commands.from_ci(ci)
        self.assertEqual(names(commands), ["ok"])
        self.assertEqual(commands[0]["env"], {"LITERAL_FILE": "from-file", "LITERAL_JOB": "yes",
                                              "DIR": "src"})
        self.assertEqual(len(notes), 5, notes)
        for job, name in (("defined-from-secret", "SECRET_FILE"),
                          ("overridden-by-secret", "LITERAL_FILE"),
                          ("predefined", "CI_COMMIT_SHA"), ("braced", "REGISTRY_USER"),
                          ("undefined", "HOME")):
            with self.subTest(job=job):
                found = noted(notes, job)
                self.assertEqual(len(found), 1)
                self.assertIn(name, found[0])

    def test_overridden_non_literal_variable_is_not_kept_as_a_literal(self):
        ci = {"image": "node:18-alpine", "variables": {"TOKEN": "literal"},
              "build": {"variables": {"TOKEN": "$SECRET"}, "script": ["npm run build $TOKEN"]}}
        commands, notes = fix_commands.from_ci(ci)
        self.assertEqual(commands, [])
        self.assertEqual(len(notes), 1)

    def test_services_and_trigger_are_never_retained(self):
        ci = {
            "image": "php:8.2-cli",
            "with-services": {"stage": "test", "services": ["mysql:8"], "script": ["vendor/bin/phpunit"]},
            "with-trigger": {"stage": "test", "trigger": {"project": "acme/other"}},
            "plain": {"stage": "test", "script": ["vendor/bin/phpunit"]},
            "no-services": {"stage": "test", "services": [], "script": ["vendor/bin/phpunit"]},
        }
        commands, notes = fix_commands.from_ci(ci)
        self.assertEqual(names(commands), ["plain", "no-services"])
        self.assertEqual(len(notes), 2, notes)
        self.assertIn("services", noted(notes, "with-services")[0])
        self.assertIn("trigger", noted(notes, "with-trigger")[0])

    def test_global_services_leave_every_job_out_unless_overridden(self):
        ci = {"image": "php:8.2-cli", "services": ["mysql:8"],
              "a": {"stage": "test", "script": ["vendor/bin/phpunit"]},
              "b": {"stage": "test", "services": [], "script": ["vendor/bin/phpunit"]}}
        commands, notes = fix_commands.from_ci(ci)
        self.assertEqual(names(commands), ["b"])
        self.assertEqual(len(notes), 1)

    def test_a_job_needing_a_left_out_job_is_left_out(self):
        ci = {
            "image": "node:18-alpine",
            "stages": ["env", "build", "test"],
            "env": {"stage": "env", "script": ["cp $PROD_ENV_FILE .env"]},
            "build": {"stage": "build", "needs": ["env"], "script": ["npm run build"]},
            "test": {"stage": "test", "needs": [{"job": "build", "artifacts": True}],
                     "script": ["npm test"]},
            "lint": {"stage": "test", "dependencies": ["env"], "script": ["npm run lint"]},
            "check": {"stage": "test", "needs": [{"project": "acme/other", "job": "x",
                                                  "ref": "main"}], "script": ["npm run lint"]},
            "assets": {"stage": "build", "needs": ["unknown"], "script": ["npm run build"]},
        }
        commands, notes = fix_commands.from_ci(ci)
        self.assertEqual(names(commands), ["assets"])
        self.assertEqual(len(notes), 5, notes)
        self.assertIn("env", noted(notes, "build")[0])
        self.assertIn("build", noted(notes, "test")[0])
        self.assertIn("env", noted(notes, "lint")[0])

    def test_docker_ssh_rsync_scp_and_sudo_words_are_never_retained(self):
        ci = {"image": "node:18-alpine"}
        for word in ("docker", "ssh", "rsync", "scp", "sudo"):
            ci[f"build-{word}"] = {"stage": "build", "script": [f"{word} -x thing", "npm run build"]}
        ci["build-docker-compose"] = {"stage": "build", "script": ["docker-compose up"]}
        ci["build-quiet"] = {"stage": "build", "script": ["npm run sudoku", "npm run build"]}
        commands, notes = fix_commands.from_ci(ci)
        self.assertEqual(names(commands), ["build-quiet"])
        self.assertEqual(len(notes), 6, notes)

    def test_script_that_cannot_be_read_as_text_leaves_the_job_out_without_crash(self):
        ci = {
            "image": "node:18-alpine",
            "nested": {"stage": "build", "script": [["npm ci", "npm run build"]]},
            "mapping": {"stage": "build", "script": [{"echo": "x"}]},
            "before": {"stage": "build", "before_script": [["a"]], "script": ["npm run build"]},
            "number": {"stage": "build", "script": [1]},
            "fine": {"stage": "build", "script": "npm run build"},
        }
        commands, notes = fix_commands.from_ci(ci)
        self.assertEqual(names(commands), ["fine"])
        self.assertEqual(len(notes), 4)
        for job in ("nested", "mapping", "before", "number"):
            with self.subTest(job=job):
                self.assertIn("script non analysable", noted(notes, job)[0])

    def test_extends_and_global_image_are_resolved(self):
        ci = {
            "image": "php:8.2-cli",
            "stages": ["build", "test"],
            ".node": {"image": {"name": "node:20-alpine"}, "variables": {"NODE_ENV": "production"}},
            ".build": {"extends": ".node", "stage": "build"},
            "assets": {"extends": [".build"], "script": ["npm run build"]},
            "phpunit": {"stage": "test", "script": ["vendor/bin/phpunit"]},
            "orphan": {"extends": ".missing", "stage": "test", "script": ["vendor/bin/phpunit"]},
            "loop-a": {"extends": "loop-b", "script": ["npm run build"]},
            "loop-b": {"extends": "loop-a", "script": ["npm run build"]},
        }
        commands, notes = fix_commands.from_ci(ci)
        self.assertEqual(kinds(commands), [("assets", "build"), ("phpunit", "test")])
        self.assertEqual(commands[0]["image"], "node:20-alpine")
        self.assertEqual(commands[0]["env"], {"NODE_ENV": "production"})
        self.assertEqual(commands[1]["image"], "php:8.2-cli")
        self.assertEqual(sorted(note.split("«")[1].split("»")[0].strip() for note in notes),
                         ["loop-a", "loop-b", "orphan"])
        self.assertIn(".missing", noted(notes, "orphan")[0])

    def test_job_keys_override_the_extended_ones_and_variables_merge(self):
        ci = {".base": {"image": "node:18-alpine", "variables": {"A": "1", "B": "2"},
                        "script": ["npm run lint"]},
              "lint": {"extends": ".base", "image": "node:20-alpine", "variables": {"B": "3"}}}
        commands, notes = fix_commands.from_ci(ci)
        self.assertEqual(notes, [])
        self.assertEqual((commands[0]["image"], commands[0]["env"], commands[0]["command"]),
                         ("node:20-alpine", {"A": "1", "B": "3"}, "npm run lint"))

    def test_default_image_comes_before_the_file_image_and_a_job_image_before_both(self):
        ci = {"image": "php:7.4-cli", "default": {"image": "php:8.2-cli"},
              "a": {"stage": "test", "script": ["vendor/bin/phpunit"]},
              "b": {"stage": "test", "image": "php:8.3-cli", "script": ["vendor/bin/phpunit"]}}
        commands, _ = fix_commands.from_ci(ci)
        self.assertEqual([item["image"] for item in commands], ["php:8.2-cli", "php:8.3-cli"])

    def test_default_before_script_applies_unless_the_job_has_its_own(self):
        ci = {"image": "node:18-alpine", "default": {"before_script": ["cd front"]},
              "a": {"stage": "build", "script": ["npm run build"]},
              "b": {"stage": "build", "before_script": [], "script": ["npm run build"]}}
        commands, _ = fix_commands.from_ci(ci)
        self.assertEqual([item["command"] for item in commands],
                         ["cd front && npm run build", "npm run build"])

    def test_image_variable_resolves_only_through_a_literal_value(self):
        ci = {
            "variables": {"NODE_IMAGE": "node:20-alpine", "SECRET_IMAGE": "$REGISTRY/node"},
            "a": {"stage": "build", "image": "$NODE_IMAGE", "script": ["npm run build"]},
            "b": {"stage": "build", "image": "${NODE_IMAGE}", "script": ["npm run build"]},
            "c": {"stage": "build", "image": "$DOCKER_IMAGE", "script": ["npm run build"]},
            "d": {"stage": "build", "image": "$SECRET_IMAGE", "script": ["npm run build"]},
            "e": {"stage": "build", "script": ["npm run build"]},
            "f": {"stage": "build", "image": "node:18 --privileged", "script": ["npm run build"]},
        }
        commands, notes = fix_commands.from_ci(ci)
        self.assertEqual([(item["name"], item["image"]) for item in commands],
                         [("a", "node:20-alpine"), ("b", "node:20-alpine")])
        self.assertEqual(len(notes), 4, notes)
        self.assertIn("image", noted(notes, "c")[0])
        self.assertIn("image", noted(notes, "e")[0])

    def test_agency_ssh_pipeline_keeps_build_jobs_in_stage_order(self):
        ci = {
            "image": AGENCY_IMAGE,
            "stages": ["init", "vendors", "build", "quality", "test", "deploy"],
            "deploy": {"stage": "deploy", "script": ["dep deploy production"]},
            "phpunit": {"stage": "test", "dependencies": ["composer", "env"],
                        "script": ["sudo cp .env.ci .env", "./vendor/bin/phpunit"]},
            "php-cs-fixer": {"stage": "quality", "script": ["./vendor/bin/pint --dirty"]},
            "build-assets": {"stage": "build", "dependencies": ["composer", "npm"],
                             "script": ["npm run prod"]},
            "env": {"stage": "init", "script": ["cp $PROD_ENV_FILE .env"]},
            "composer": {"stage": "vendors", "script": ["composer install --no-interaction"]},
            "npm": {"stage": "vendors", "script": ["npm ci"]},
            "init": {"stage": "init", "script": ['echo "init"']},
        }
        commands, notes = fix_commands.from_ci(ci)
        self.assertEqual(kinds(commands), [("composer", "install"), ("npm", "install"),
                                           ("build-assets", "build"), ("php-cs-fixer", "lint")])
        self.assertEqual({item["image"] for item in commands}, {AGENCY_IMAGE})
        self.assertEqual(commands[2]["command"], "npm run prod")
        self.assertEqual(len(notes), 4, notes)
        for job, why in (("init", "type"), ("env", "PROD_ENV_FILE"), ("phpunit", "sudo"),
                         ("deploy", "déploi")):
            with self.subTest(job=job):
                found = noted(notes, job)
                self.assertEqual(len(found), 1, notes)
                self.assertIn(why, found[0])

    def test_jobs_follow_the_stages_then_the_file_order(self):
        ci = {"image": "node:18-alpine", "stages": ["a", "b"],
              "build-late": {"stage": "b", "script": ["npm run build"]},
              "build-early": {"stage": "a", "script": ["npm run build"]},
              "build-also-late": {"stage": "b", "script": ["npm run build"]},
              "test-unlisted": {"stage": "zzz", "script": ["npm test"]},
              "lint-after": {"stage": ".post", "script": ["npm run lint"]},
              "build-before": {"stage": ".pre", "script": ["npm run build"]}}
        commands, _ = fix_commands.from_ci(ci)
        self.assertEqual(names(commands), ["build-before", "build-early", "build-late",
                                           "build-also-late", "lint-after", "test-unlisted"])

    def test_kind_comes_from_the_content_first_then_the_name_then_the_stage(self):
        ci = {
            "image": "node:18-alpine",
            "build-vendors": {"stage": "build", "script": ["php -v", "node -v", "npm install"]},
            "lint-install": {"stage": "test", "script": ["yarn install --frozen-lockfile"]},
            "pnpm": {"stage": "test", "script": ["pnpm install --frozen-lockfile"]},
            "eslint": {"stage": "test", "script": ["npx eslint src"]},
            "Check_Syntax": {"stage": "test", "script": ["npx tsc --noEmit"]},
            "unit-tests": {"stage": "validate", "script": ["npm test"]},
            "buildAssets": {"stage": "validate", "script": ["npm run build"]},
            "docs": {"stage": "validate", "script": ["npm run docs"]},
            "css": {"stage": "validate", "script": ["npm run css"]},
            "greet": {"stage": "validate", "script": ["echo hello"]},
            "generate": {"stage": "build", "script": ["npm run generate"]},
            "audit": {"stage": "quality", "script": ["npm audit"]},
            "stage-only": {"stage": "lint", "script": ["npm run x"]},
        }
        commands, notes = fix_commands.from_ci(ci)
        self.assertEqual(dict(kinds(commands)), {
            "build-vendors": "install", "lint-install": "install", "pnpm": "install",
            "eslint": "lint", "Check_Syntax": "lint", "unit-tests": "test",
            "buildAssets": "build", "generate": "build", "audit": "lint", "stage-only": "lint"})
        for job in ("docs", "css", "greet"):
            with self.subTest(job=job):
                self.assertEqual(len(noted(notes, job)), 1)

    def test_install_atoms_must_be_only_an_install_to_make_an_install_job(self):
        ci = {"image": "node:18-alpine",
              "a": {"stage": "build", "script": ["npm ci; npx gulp production"]},
              "b": {"stage": "build", "script": ["npm ci && npm run build"]},
              "c": {"stage": "build", "script": ["npm ci | tee install.log"]},
              "d": {"stage": "build", "script": ["yarn build"]}}
        commands, notes = fix_commands.from_ci(ci)
        self.assertEqual(kinds(commands), [("a", "build"), ("b (install)", "install"),
                                           ("b", "build"), ("c", "build"), ("d", "build")])
        self.assertEqual(commands[2]["command"], "npm run build")

    def test_a_script_comment_cannot_swallow_the_next_command(self):
        ci = {"image": "node:18-alpine",
              "build": {"script": ["npm run build # then more", "echo done"]}}
        commands, _ = fix_commands.from_ci(ci)
        self.assertEqual(commands[0]["command"], "{ npm run build # then more\n} && echo done")

    def test_multi_line_entry_stays_whole(self):
        ci = {"image": "node:18-alpine",
              "build": {"script": ["if [ -f a ]; then\n  npm run build\nfi\n", "echo ok"]}}
        commands, _ = fix_commands.from_ci(ci)
        self.assertEqual(commands[0]["command"],
                         "{ if [ -f a ]; then\n  npm run build\nfi\n} && echo ok")

    def test_hidden_jobs_and_global_keywords_are_not_commands(self):
        ci = {"image": "node:18-alpine", "stages": ["build"], "variables": {"A": "1"},
              "cache": {"paths": ["node_modules"]}, "workflow": {"rules": []},
              "default": {"retry": 1}, ".template": {"script": ["npm run build"]},
              "just-a-mapping": {"stage": "build"}, "a-list": ["x"]}
        self.assertEqual(fix_commands.from_ci(ci), ([], []))

    def test_include_is_ignored_with_a_note(self):
        ci = {"include": [{"local": "ci/build.yml"}], "image": "node:18-alpine",
              "build": {"script": ["npm run build"]}}
        commands, notes = fix_commands.from_ci(ci)
        self.assertEqual(names(commands), ["build"])
        self.assertEqual(len(notes), 1)
        self.assertIn("include", notes[0])

    def test_odd_documents_never_raise(self):
        for ci in ([], "text", None, 3, {"stages": "build", "build": {"script": "npm run build"}},
                   {"variables": ["x"], "image": 3, "build": {"script": ["npm run build"],
                                                              "variables": "x", "needs": "x",
                                                              "extends": {"a": 1}}},
                   {"build": {"script": None}}, {1: {"script": ["x"]}},
                   {"default": "x", "build": {"image": {"entrypoint": [""]}, "script": ["x"]}}):
            with self.subTest(ci=ci):
                commands, notes = fix_commands.from_ci(ci)
                self.assertIsInstance(commands, list)
                self.assertIsInstance(notes, list)


class ManifestTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_manifests_cover_only_folders_the_ci_does_not(self):
        write(self.root, "package.json", {"scripts": {"build": "x", "production": "y",
                                                      "lint": "z", "test": "t", "start": "s"}})
        write(self.root, "package-lock.json", "{}")
        write(self.root, "src/theme/package.json", {"scripts": {"build": "x"}})
        write(self.root, "src/theme/package-lock.json", "{}")
        write(self.root, "tools/package.json", {"scripts": {"lint": "x"}})
        write(self.root, "tools/yarn.lock", "")
        write(self.root, "node_modules/dep/package.json", {"scripts": {"test": "x"}})
        write(self.root, "vendor/pkg/composer.json", {"scripts": {"test": "x"}})
        write(self.root, ".git/package.json", {"scripts": {"test": "x"}})
        write(self.root, "a/b/c/d/package.json", {"scripts": {"test": "x"}})
        write(self.root, "a/b/c/d/package-lock.json", "{}")
        write(self.root, "a/b/c/d/e/package.json", {"scripts": {"test": "x"}})
        write(self.root, "a/b/c/d/e/package-lock.json", "{}")
        commands, notes = fix_commands.from_manifests(self.root, {"src/theme"})
        by_dir = {}
        for item in commands:
            by_dir.setdefault(item["dir"], []).append((item["kind"], item["command"]))
        self.assertEqual(sorted(by_dir), ["", "a/b/c/d", "tools"])
        self.assertEqual(by_dir[""], [
            ("install", "npm ci"), ("build", "npm run build"), ("build", "npm run production"),
            ("lint", "npm run lint"), ("test", "npm run test")])
        self.assertEqual(by_dir["tools"], [("install", "yarn install --frozen-lockfile"),
                                           ("lint", "npm run lint")])
        self.assertEqual(by_dir["a/b/c/d"], [("install", "npm ci"), ("test", "npm run test")])
        for item in commands:
            self.assertEqual(
                (item["env"], item["private"], set(item)),
                ({}, False, {"name", "dir", "image", "kind", "command", "env", "source", "private"}))
        root_install = commands[0]
        self.assertEqual((root_install["name"], root_install["source"]), ("npm ci", "package.json"))
        tools = [item for item in commands if item["dir"] == "tools"]
        self.assertEqual((tools[1]["name"], tools[1]["source"]),
                         ("npm run lint (tools)", "tools/package.json"))
        self.assertEqual(len(names(commands)), len(set(names(commands))))

    def test_third_party_folders_are_skipped(self):
        write(self.root, "wp-content/plugins/acf/package.json", {"scripts": {"test": "x"}})
        write(self.root, "wp-content/plugins/acf/package-lock.json", "{}")
        write(self.root, "theme/package.json", {"scripts": {"build": "x"}})
        write(self.root, "theme/package-lock.json", "{}")
        commands, _ = fix_commands.from_manifests(self.root, set(), skip=("wp-content/plugins",))
        self.assertEqual({item["dir"] for item in commands}, {"theme"})

    def test_package_manager_follows_the_lockfile(self):
        for folder, lock, install in (("npm", "package-lock.json", "npm ci"),
                                      ("yarn", "yarn.lock", "yarn install --frozen-lockfile"),
                                      ("pnpm", "pnpm-lock.yaml", "pnpm install --frozen-lockfile")):
            write(self.root, f"{folder}/package.json", {"scripts": {"build": "x"}})
            write(self.root, f"{folder}/{lock}", "")
        commands, _ = fix_commands.from_manifests(self.root, set())
        self.assertEqual({item["dir"]: item["command"] for item in commands if item["kind"] == "install"},
                         {"npm": "npm ci", "yarn": "yarn install --frozen-lockfile",
                          "pnpm": "pnpm install --frozen-lockfile"})

    def test_package_without_lockfile_gives_a_note_and_no_command(self):
        write(self.root, "web/package.json", {"scripts": {"build": "x"}})
        commands, notes = fix_commands.from_manifests(self.root, set())
        self.assertEqual(commands, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("web/package.json", notes[0])

    def test_node_image_comes_from_nvmrc_then_engines_then_a_default_with_a_note(self):
        for folder, nvmrc, engines in (("nvm", "v18.17.0\n", None), ("eng", None, ">=20.0.0 <23"),
                                       ("both", "16", "22.x"), ("lts", "lts/*\n", None),
                                       ("none", None, None), ("odd", "999", "x")):
            write(self.root, f"{folder}/package.json",
                  {"scripts": {"build": "x"}, **({"engines": {"node": engines}} if engines else {})})
            write(self.root, f"{folder}/package-lock.json", "{}")
            if nvmrc:
                write(self.root, f"{folder}/.nvmrc", nvmrc)
        commands, notes = fix_commands.from_manifests(self.root, set())
        images = {item["dir"]: item["image"] for item in commands}
        self.assertEqual(images, {
            "nvm": "node:18-alpine", "eng": "node:20-alpine", "both": "node:16-alpine",
            "lts": "node:22-alpine", "none": "node:22-alpine", "odd": "node:22-alpine"})
        self.assertEqual(len([note for note in notes if "node:22-alpine" in note]), 3)
        self.assertTrue(all(any(folder in note for note in notes) for folder in ("lts", "none", "odd")))

    def test_root_nvmrc_serves_a_package_in_a_subfolder(self):
        write(self.root, ".nvmrc", "14\n")
        write(self.root, "web/package.json", {"scripts": {"build": "x"}})
        write(self.root, "web/package-lock.json", "{}")
        commands, notes = fix_commands.from_manifests(self.root, set())
        self.assertEqual({item["image"] for item in commands}, {"node:14-alpine"})
        self.assertEqual(notes, [])

    def test_the_default_npm_test_placeholder_is_not_a_test(self):
        write(self.root, "package.json", {"scripts": {
            "test": 'echo "Error: no test specified" && exit 1', "lint": "x"}})
        write(self.root, "package-lock.json", "{}")
        commands, _ = fix_commands.from_manifests(self.root, set())
        self.assertEqual([item["command"] for item in commands], ["npm ci", "npm run lint"])

    def test_composer_commands_and_image(self):
        write(self.root, "composer.json", {"require": {"php": "^8.2"},
                                           "scripts": {"phpstan": "x", "phpcs": "y", "test": "z",
                                                       "post-install-cmd": "w"}})
        write(self.root, "api/composer.json", {"scripts": {"test": "z"}})
        commands, notes = fix_commands.from_manifests(self.root, set())
        self.assertEqual(notes, [])
        root = [item for item in commands if item["dir"] == ""]
        self.assertEqual(
            [(item["kind"], item["command"]) for item in root],
            [("install", "composer install --no-interaction --no-progress --no-scripts"),
             ("lint", "composer phpstan"), ("lint", "composer phpcs"), ("test", "composer test")])
        self.assertEqual({item["image"] for item in commands}, {"composer:2"})
        self.assertEqual({item["private"] for item in commands}, {False})
        self.assertEqual(root[0]["source"], "composer.json")
        self.assertEqual([item["source"] for item in commands if item["dir"] == "api"],
                         ["api/composer.json"] * 2)

    def test_private_composer_repository_is_marked_private(self):
        def composer(folder, repositories):
            write(self.root, f"{folder}/composer.json",
                  {"repositories": repositories, "scripts": {"test": "z", "phpcs": "y"}})

        composer("vcs", [{"type": "vcs", "url": "git@git.example.org:acme/private.git"}])
        composer("https", {"acme": {"type": "composer", "url": "https://satis.example.org"}})
        composer("package", [{"type": "package", "package": {
            "name": "a/b", "version": "1.0.0", "dist": {"url": "https://files.example.org/a.zip",
                                                         "type": "zip"}}}])
        composer("packagist", [{"type": "composer", "url": "https://repo.packagist.org"},
                               {"packagist.org": False}])
        composer("path", [{"type": "path", "url": "../local-package"},
                          {"type": "path", "url": "packages/*"}])
        composer("none", [])
        commands, _ = fix_commands.from_manifests(self.root, set())
        private = {}
        for item in commands:
            private.setdefault(item["dir"], set()).add(item["private"])
        self.assertEqual(private, {
            "vcs": {True}, "https": {True}, "package": {True},
            "packagist": {False}, "path": {False}, "none": {False}})
        self.assertEqual(len([item for item in commands if item["dir"] == "vcs"]), 3)

    def test_ignored_auth_json_at_the_root_marks_composer_private(self):
        write(self.root, "composer.json", {"scripts": {"test": "z"}})
        write(self.root, "api/composer.json", {"scripts": {"test": "z"}})
        write(self.root, ".gitignore", "/vendor\n# comment\n/auth.json\n")
        commands, _ = fix_commands.from_manifests(self.root, set())
        self.assertEqual({item["private"] for item in commands}, {True})
        write(self.root, ".gitignore", "auth.json\n!auth.json\n")
        commands, _ = fix_commands.from_manifests(self.root, set())
        self.assertEqual({item["private"] for item in commands}, {False})
        for pattern in ("*.json", "**/auth.json", "auth.*"):
            write(self.root, ".gitignore", pattern + "\n")
            commands, _ = fix_commands.from_manifests(self.root, set())
            self.assertEqual({item["private"] for item in commands}, {True}, pattern)
        write(self.root, ".gitignore", "config/auth.json\n/vendor/\n")
        commands, _ = fix_commands.from_manifests(self.root, set())
        self.assertEqual({item["private"] for item in commands}, {False})

    def test_unreadable_manifests_give_a_note(self):
        write(self.root, "package.json", "{broken")
        write(self.root, "composer.json", "[]")
        commands, notes = fix_commands.from_manifests(self.root, set())
        self.assertEqual(commands, [])
        self.assertEqual(len(notes), 2, notes)

    def test_a_repo_without_manifests_or_a_missing_one_gives_nothing(self):
        self.assertEqual(fix_commands.from_manifests(self.root, set()), ([], []))
        self.assertEqual(fix_commands.from_manifests(self.root / "absent", set()), ([], []))


class DetectTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        write(self.root, ".nvmrc", "18")
        write(self.root, "package.json", {"scripts": {"build": "x", "lint": "y"}})
        write(self.root, "package-lock.json", "{}")
        write(self.root, "src/theme/package.json", {"scripts": {"build": "x"}})
        write(self.root, "src/theme/package-lock.json", "{}")

    def ci(self, **variables):
        return {"image": "node:18-alpine", "stages": ["build"],
                "build:npm": {"stage": "build", "variables": variables,
                              "before_script": "cd $NPM_DIRECTORY",
                              "script": ["npm ci", "npx gulp production", "cd $CI_PROJECT_DIR"]}}

    def test_without_ci_the_manifests_give_the_commands(self):
        found = fix_commands.detect(self.root, None)
        self.assertEqual(set(found), {"commands", "notes"})
        self.assertEqual({item["dir"] for item in found["commands"]}, {"", "src/theme"})
        self.assertEqual(found["notes"], [])

    def test_a_folder_the_ci_works_in_through_cd_is_not_read_again(self):
        found = fix_commands.detect(self.root, self.ci(NPM_DIRECTORY="src/theme"))
        manifests = [item for item in found["commands"] if item["source"] == "package.json"
                     or item["source"].endswith("/package.json")]
        self.assertEqual({item["dir"] for item in manifests}, {""})
        self.assertEqual(names(found["commands"])[:2], ["build:npm (install)", "build:npm"])

    def test_commands_at_the_root_of_the_ci_cover_the_root(self):
        ci = {"image": "node:18-alpine", "build": {"script": ["npm run build"]}}
        found = fix_commands.detect(self.root, ci)
        self.assertEqual({item["dir"] for item in found["commands"]
                          if item["source"] != ".gitlab-ci.yml (build)"}, {"src/theme"})

    def test_a_ci_left_out_entirely_leaves_the_manifests(self):
        ci = {"image": "node:18-alpine", "deploy": {"script": ["npm run deploy"]}}
        found = fix_commands.detect(self.root, ci)
        self.assertEqual({item["dir"] for item in found["commands"]}, {"", "src/theme"})
        self.assertEqual(len(found["notes"]), 1)

    def test_a_composer_install_of_the_ci_is_private_when_the_project_says_so(self):
        write(self.root, "composer.json", {"repositories": [
            {"type": "vcs", "url": "https://git.example.org/acme/private"}]})
        ci = {"image": "php:8.2-cli", "composer": {"stage": "build",
                                                      "script": ["composer install --no-interaction"]},
              "npm": {"stage": "build", "script": ["npm ci"]}}
        found = fix_commands.detect(self.root, ci)
        self.assertEqual({item["name"]: item["private"] for item in found["commands"]
                          if item["source"].startswith(".gitlab-ci.yml")},
                         {"composer": True, "npm": False})

    def test_covered_dirs_resolve_the_leading_cd_chain(self):
        def item(command, env=None, folder=""):
            return {"source": ".gitlab-ci.yml (x)", "command": command, "env": env or {},
                    "dir": folder}

        self.assertEqual(fix_commands.covered_dirs([item("npm ci")]), {""})
        self.assertEqual(fix_commands.covered_dirs([item("cd a/b/ && npm ci")]), {"a/b"})
        self.assertEqual(fix_commands.covered_dirs([item("cd a && cd ../c && npm ci")]), {"c"})
        self.assertEqual(fix_commands.covered_dirs([item("cd /work/x && npm ci")]), {"x"})
        self.assertEqual(fix_commands.covered_dirs([item("cd $D && npm ci", {"D": "web"})]), {"web"})
        self.assertEqual(fix_commands.covered_dirs([item("cd ${D} && npm ci", {"D": "web"})]), {"web"})
        self.assertEqual(fix_commands.covered_dirs([item("cd $UNKNOWN && npm ci")]), {""})
        self.assertEqual(fix_commands.covered_dirs([item("cd ../out && npm ci")]), {""})
        self.assertEqual(fix_commands.covered_dirs([item("cd /etc && npm ci")]), {""})
        self.assertEqual(fix_commands.covered_dirs([item("npm ci && cd a")]), {""})
        manifest = {"source": "package.json", "command": "cd a", "env": {}, "dir": "x"}
        self.assertEqual(fix_commands.covered_dirs([manifest]), set())


    def test_start_dir_is_the_dir_then_the_leading_cd_whatever_the_source(self):
        manifest = {"source": "package.json", "command": "npm ci", "env": {}, "dir": "web"}
        self.assertEqual(fix_commands.start_dir(manifest), "web")
        ci = {"source": ".gitlab-ci.yml (x)", "command": "cd $D && npm ci", "env": {"D": "a/b"},
              "dir": ""}
        self.assertEqual(fix_commands.start_dir(ci), "a/b")
        self.assertEqual(fix_commands.start_dir({**manifest, "command": "cd ../x"}), "x")
        self.assertEqual(fix_commands.start_dir({**manifest, "command": "cd ../../x"}), "web")


class ShapeTest(unittest.TestCase):
    GOOD = {"name": "npm ci", "dir": "web", "image": "node:18-alpine", "kind": "install",
            "command": "npm ci", "env": {"A": "b"}, "source": "package.json", "private": False}

    def test_valid_command(self):
        self.assertTrue(fix_commands.valid_command(self.GOOD))
        self.assertTrue(fix_commands.valid_command({**self.GOOD, "dir": "", "env": {}}))
        for change in ({"kind": "deploy"}, {"image": "-x"}, {"image": "a b"}, {"image": ""},
                       {"image": "a/../b"}, {"dir": "../x"}, {"dir": "/x"}, {"dir": "a/"},
                       {"dir": "a:b"}, {"command": " "}, {"name": ""}, {"env": {"A B": "c"}},
                       {"env": {"A": 1}}, {"env": []}, {"private": "no"}, {"source": None}):
            with self.subTest(change=change):
                self.assertFalse(fix_commands.valid_command({**self.GOOD, **change}))
        self.assertFalse(fix_commands.valid_command([]))
        self.assertFalse(fix_commands.valid_command({}))

    def test_valid_image_mirrors_the_launcher(self):
        for image in ("node:18-alpine", "node", "docker.io/library/node:18",
                      "node@sha256:" + "a" * 64, AGENCY_IMAGE):
            self.assertTrue(fix_commands.valid_image(image), image)
        for image in ("", "-x", "a b", "a;b", "a/../b", "$X", "node:18\n", None, 3):
            self.assertFalse(fix_commands.valid_image(image), image)

    def test_clean_dir(self):
        self.assertEqual(fix_commands.clean_dir(""), "")
        self.assertEqual(fix_commands.clean_dir("."), "")
        self.assertEqual(fix_commands.clean_dir("./web/"), "web")
        self.assertEqual(fix_commands.clean_dir("web//assets"), "web/assets")
        for bad in ("../x", "a/../../x", "/x", "a:b", "a\\b", "a\nb"):
            self.assertIsNone(fix_commands.clean_dir(bad), bad)


NODE_FILES = {"package.json", "package-lock.json"}
YARN_FILES = {"package.json", "yarn.lock"}
PNPM_FILES = {"package.json", "pnpm-lock.yaml"}
PHP_FILES = {"composer.json", "composer.lock"}
NPM_FILES = ["package.json", "package-lock.json"]


def files_of(command):
    """The files of a folder where the manager of the command works."""
    words = command.split() if isinstance(command, str) else []
    return {"yarn": YARN_FILES, "pnpm": PNPM_FILES, "composer": PHP_FILES}.get(
        words[0] if words else "", NODE_FILES)


# Forms the fixer may not use, grouped by what makes them refused.
REFUSED = {
    "no development flag, or another manager": (
        "npm install lodash", "npm install", "npm install --save-dev", "npm install -D",
        "composer require vendor/p", "composer require --dev", "composer update",
        "composer install", "yarn add eslint", "yarn add --dev", "pnpm add eslint", "pnpm add -D",
        "yarn install", "npm ci", "npm i -D lodash", "npm add -D lodash", "npm uninstall lodash",
        "npm install --dev lodash", "pip install requests", "bun add -d lodash", "npx lodash",
        "composer global require --dev vendor/p", "yarn global add --dev eslint",
        "pnpm install -D eslint", "npm install --save lodash", "npm install -S lodash",
        "npm install --save-prod lodash", "npm install -P lodash", "npm install --save-optional lodash",
        "npm install -O lodash", "npm install --save-peer lodash", "npm install --no-save lodash",
        "npm install --save-bundle lodash", "npm install --dry-run", "npm install --save-dev-exact lodash",
    ),
    "a package outside the public registry": (
        "npm install git+https://example.org/x.git", "npm install -D git+https://example.org/x.git",
        "npm install -D git+ssh://git@example.org/x.git", "npm install -D https://example.org/x.tgz",
        "npm install -D http://example.org/x.tgz", "npm install ../x", "npm install -D ../x",
        "npm install -D ./x", "npm install -D /tmp/x", "npm install -D ~/x", "npm install -D .",
        "npm install -D file:../x", "npm install -D github:user/repo", "npm install -D user/repo",
        "npm install -D lodash@file:../x", "npm install -D lodash@npm:other",
        "npm install -D lodash@git+https://example.org/x.git", "npm install -D lodash@user/repo",
        "npm install -D lodash@https://example.org/x.tgz", "npm install -D lodash@latest@2",
        "composer require --dev ../x", "composer require --dev vendor/p:../x",
        "composer require --dev https://example.org/x.zip", "composer require --dev ./vendor/p",
        "composer require --dev vendor/p:dev-main#abc", "composer require --dev vendor/p@dev",
        "composer require --dev vendor/p:1.0:2", "composer require --dev vendor/p:git://x/y",
        "composer require --dev vendor/p/q", "composer require --dev lodash",
        "composer require --dev vendor/", "composer require --dev /p",
        "yarn add --dev git+https://example.org/x.git", "yarn add --dev ../x",
        "yarn add --dev file:../x", "pnpm add -D ../x", "pnpm add -D link:../x",
        "pnpm add -D https://example.org/x.tgz",
    ),
    "shell metacharacters, quotes, control characters": (
        "npm install -D lodash; rm -rf /", "npm install -D lodash;rm -rf /",
        "npm install -D lodash && rm -rf /", "npm install -D lodash || true",
        "npm install -D lodash | sh", "npm install -D lodash &", "npm install -D $(id)",
        "npm install -D `id`", "npm install -D ${HOME}", "npm install -D $HOME",
        "npm install -D lodash\nrm -rf /", "npm install -D lodash\r", "npm install -D lodash\n",
        "npm install -D lodash\t", "npm install -D\tlodash", "npm install -D lodash > x",
        "npm install -D lodash < x", "npm install -D 'lodash'", 'npm install -D "lodash"',
        "npm install -D lodash # comment", "npm install -D lodash\\", "npm install -D lo*",
        "npm install -D lo?", "npm install -D {a,b}", "npm install -D lodash!", "npm install -D (a)",
        "npm install -D lodash\x00", "npm install -D lodash\x1b[0m", "; npm install -D lodash",
        "npm install -D lodash@1.0 && id", "npm install -D lodash@$(id)",
        "npm install -D lodash@`id`", "composer require --dev vendor/p; id",
        "composer require --dev vendor/p:$(id)", "composer require --dev vendor/p\nid",
        "composer require --dev vendor/p:^1.0|id", "yarn add --dev eslint; id",
        "pnpm add -D eslint && id", "yarn add --dev $(id)",
    ),
    "spaces that are not one between two words": (
        " npm install -D lodash", "npm install -D lodash ", "npm  install -D lodash",
        "npm install  -D lodash", "npm install -D  lodash", "npm install -D lodash@1.0 .0",
        "npm install -D lodash@ 1.0", "composer require --dev vendor/p:1.0 .0",
        "composer require --dev vendor/p: 1.0", "composer require  --dev vendor/p", "", " ", "npm",
        "npm install -D lodash ", "npm install -D lodash",
    ),
    "capitals out of the forms, look-alike characters": (
        "NPM install -D lodash", "npm INSTALL -D lodash", "npm install -d lodash",
        "npm install --SAVE-DEV lodash", "npm install -D Lodash", "npm install -D LODASH",
        "npm install -D @Scope/tool", "npm install -D @scope/Tool",
        "composer require --dev Vendor/p", "composer require --dev vendor/P",
        "composer require --DEV vendor/p", "Composer require --dev vendor/p",
        "yarn add --dev ESLint", "pnpm add -D ESLint", "npm install -D lodаsh",
        "npm install -D lodash​", "npm install -D ｌodash",
    ),
    "an empty or malformed package or version": (
        "npm install -D @", "npm install -D @/x", "npm install -D @scope/", "npm install -D @scope",
        "npm install -D @@scope/x", "npm install -D -lodash", "npm install -D .lodash",
        "npm install -D _lodash", "npm install -D lodash@", "npm install -D @scope/x@",
        "npm install -D lodash@-1", "npm install -D lodash@.1", "npm install -D lodash@^",
        "npm install -D lodash@^^1", "npm install -D lodash@>=1.0.0", "npm install -D lodash@*",
        "npm install -D lodash@1.*", "npm install -D lodash@1 || 2", "npm install -D lodash@@1",
        "composer require --dev vendor/p:", "composer require --dev vendor/p:^",
        "composer require --dev vendor/p:*", "composer require --dev vendor/p:>=1.0",
        "composer require --dev vendor/p:-1", "composer require --dev vendor/p:.1",
        "composer require --dev vendor/p:1.0.0 vendor/q:", "yarn add --dev @scope/",
    ),
    "a repeated option, an unknown option, an option of another form, -- and --registry": (
        "npm install -D -D lodash", "npm install --save-dev --save-dev lodash",
        "npm install --save-dev -D lodash", "npm install -D --save-dev lodash",
        "npm install -D lodash -D", "npm install lodash -D", "npm install lodash --save-dev",
        "npm install -D lodash --save-dev", "npm install -D --save-exact lodash@1.0.0",
        "npm install --save-exact -D lodash@1.0.0", "npm install --save-exact --save-exact lodash@1.0.0",
        "npm install -D --ignore-scripts lodash", "npm install -D lodash --ignore-scripts",
        "npm install -D --no-ignore-scripts lodash", "npm install -D --foreground-scripts lodash",
        "npm install -D --registry=https://example.org/ lodash",
        "npm install -D --registry https://example.org/ lodash",
        "npm install -D lodash --registry=https://example.org/",
        "npm install -D lodash --registry https://example.org/",
        "npm install -D --@scope:registry=https://example.org/ lodash",
        "npm install -D --userconfig=x lodash", "npm install -D --prefix=/ lodash",
        "npm install -D --prefix .. lodash", "npm install -D --global lodash",
        "npm install -D -g lodash", "npm install -g -D lodash", "npm install -D --force lodash",
        "npm install -D --legacy-peer-deps lodash", "npm install -D --cache=/ lodash",
        "npm install -D --script-shell=sh lodash", "npm install -D --", "npm install -D -- lodash",
        "npm install -- -D lodash", "npm install -D lodash --",
        "npm install -D lodash -- --registry=https://example.org/", "npm install -D -",
        "npm install -D --lodash", "npm install --save-exact lodash",
        "npm install --save-exact lodash@1.0.0 other@2.0.0", "npm install --save-exact",
        "npm install --save-exact -D lodash@1.0.0", "npm install --package-lock-only lodash",
        "npm install --package-lock-only -D lodash", "npm install --package-lock-only --package-lock-only",
        "npm install --package-lock-only --ignore-scripts",
        "npm install --package-lock-only --registry=https://example.org/",
        "composer require --dev --dev vendor/p", "composer require --dev vendor/p --dev",
        "composer require vendor/p --dev", "composer require --dev --no-scripts vendor/p",
        "composer require --dev --no-plugins vendor/p", "composer require --dev -W vendor/p",
        "composer require --dev --with-all-dependencies vendor/p",
        "composer require --dev --ignore-platform-reqs vendor/p",
        "composer require --dev --prefer-source vendor/p",
        "composer require --dev --repository=https://example.org vendor/p",
        "composer require --dev -- vendor/p", "composer require --dev --",
        "composer require --dev vendor/p --", "composer update --lock vendor/p",
        "composer update --lock --with-all-dependencies", "composer update --lock --lock",
        "composer update --lock --no-scripts", "composer update --lock --",
        "composer update --lock --dev", "composer update --no-scripts --lock",
        "composer update vendor/p --lock", "yarn add --dev eslint@8.0.0", "yarn add -D eslint",
        "yarn add --dev eslint prettier", "yarn add --dev --registry=https://example.org/ eslint",
        "yarn add --dev eslint --registry https://example.org/", "yarn add --dev --",
        "yarn add --dev -- eslint", "yarn add --dev --dev eslint", "yarn add --dev --ignore-scripts eslint",
        "pnpm add --save-dev eslint", "pnpm add -D eslint@8.0.0", "pnpm add -D eslint prettier",
        "pnpm add -D --registry=https://example.org/ eslint", "pnpm add -D -- eslint",
        "pnpm add -D -D eslint", "pnpm add -D eslint --ignore-scripts", "pnpm add -g -D eslint",
        "pnpm add -D --filter x eslint",
    ),
    "a package given twice": (
        "npm install -D lodash lodash", "npm install -D lodash@1.0.0 lodash@2.0.0",
        "npm install -D lodash lodash@1.0.0", "composer require --dev vendor/p vendor/p",
        "composer require --dev vendor/p:1.0 vendor/p:2.0",
    ),
}


class AllowedInstallTest(unittest.TestCase):
    def allowed(self, command, files=NODE_FILES):
        return fix_commands.allowed_install(command, set(files))

    def test_allowed_install_forms(self):
        accepted = (
            ("npm install --save-dev lodash", NODE_FILES,
             ("npm", "dev", ["lodash"], ["lodash"], NPM_FILES,
              "npm install --save-dev --ignore-scripts lodash")),
            ("npm install -D stylelint", NODE_FILES,
             ("npm", "dev", ["stylelint"], ["stylelint"], NPM_FILES,
              "npm install --save-dev --ignore-scripts stylelint")),
            ("npm install --save-dev lodash@4.17.21 @types/node@^20.1.0 left-pad", NODE_FILES,
             ("npm", "dev", ["lodash@4.17.21", "@types/node@^20.1.0", "left-pad"],
              ["lodash", "@types/node", "left-pad"], NPM_FILES,
              "npm install --save-dev --ignore-scripts lodash@4.17.21 '@types/node@^20.1.0' "
              "left-pad")),
            ("npm install -D @scope/tool@~2.0 a.b_c-d@1.0.0-RC.1+build", NODE_FILES,
             ("npm", "dev", ["@scope/tool@~2.0", "a.b_c-d@1.0.0-RC.1+build"],
              ["@scope/tool", "a.b_c-d"], NPM_FILES,
              "npm install --save-dev --ignore-scripts '@scope/tool@~2.0' "
              "a.b_c-d@1.0.0-RC.1+build")),
            ("npm install --save-exact lodash@4.17.21", NODE_FILES,
             ("npm", "exact", ["lodash@4.17.21"], ["lodash"], NPM_FILES,
              "npm install --save-exact --ignore-scripts lodash@4.17.21")),
            ("npm install --package-lock-only", NODE_FILES,
             ("npm", "lock", [], [], NPM_FILES,
              "npm install --package-lock-only --ignore-scripts")),
            ("composer require --dev phpstan/phpstan", PHP_FILES,
             ("composer", "dev", ["phpstan/phpstan"], ["phpstan/phpstan"],
              ["composer.json", "composer.lock"],
              "composer require --dev --no-scripts --no-plugins --no-interaction phpstan/phpstan")),
            ("composer require --dev phpstan/phpstan:^1.10 squizlabs/php_codesniffer:3.7.2",
             PHP_FILES,
             ("composer", "dev", ["phpstan/phpstan:^1.10", "squizlabs/php_codesniffer:3.7.2"],
              ["phpstan/phpstan", "squizlabs/php_codesniffer"],
              ["composer.json", "composer.lock"],
              "composer require --dev --no-scripts --no-plugins --no-interaction "
              "'phpstan/phpstan:^1.10' squizlabs/php_codesniffer:3.7.2")),
            ("composer update --lock", PHP_FILES,
             ("composer", "lock", [], [], ["composer.json", "composer.lock"],
              "composer update --lock --no-scripts --no-plugins --no-interaction")),
            ("yarn add --dev eslint", YARN_FILES,
             ("yarn", "dev", ["eslint"], ["eslint"], ["package.json", "yarn.lock"],
              "yarn add --dev --ignore-scripts eslint")),
            ("pnpm add -D @scope/eslint", PNPM_FILES,
             ("pnpm", "dev", ["@scope/eslint"], ["@scope/eslint"],
              ["package.json", "pnpm-lock.yaml"],
              "pnpm add --save-dev --ignore-scripts @scope/eslint")),
        )
        for command, files, expected in accepted:
            with self.subTest(command=command):
                found = self.allowed(command, files)
                self.assertIsNotNone(found)
                manager, form, packages, names, locked, rebuilt = expected
                self.assertEqual(
                    {key: found[key] for key in ("manager", "form", "packages", "names", "files",
                                                 "command")},
                    {"manager": manager, "form": form, "packages": packages, "names": names,
                     "files": locked, "command": rebuilt})
        for why, commands in REFUSED.items():
            for command in commands:
                with self.subTest(why=why, refused=command):
                    self.assertIsNone(self.allowed(command, files_of(command)))

    def test_the_manager_files_of_the_folder_decide(self):
        self.assertIsNone(self.allowed("npm install -D lodash", set()))
        self.assertIsNone(self.allowed("npm install -D lodash", {"package-lock.json"}))
        self.assertIsNone(self.allowed("npm install -D lodash", PHP_FILES))
        self.assertIsNone(self.allowed("composer require --dev vendor/p", {"package.json"}))
        self.assertIsNone(self.allowed("composer require --dev vendor/p", {"composer.lock"}))
        self.assertIsNone(self.allowed("yarn add --dev eslint", {"composer.json"}))
        self.assertIsNone(self.allowed("pnpm add -D eslint", set()))
        self.assertIsNotNone(self.allowed("npm install -D lodash", {"package.json"}))
        self.assertIsNotNone(self.allowed("composer require --dev vendor/p", {"composer.json"}))
        self.assertIsNotNone(self.allowed("yarn add --dev eslint", {"package.json"}))
        self.assertIsNotNone(self.allowed("npm install -D lodash", {"package.json", "README.md"}))
        # A folder that another manager already owns is never given a second lock file.
        self.assertIsNone(self.allowed("npm install -D lodash", YARN_FILES))
        self.assertIsNone(self.allowed("npm install -D lodash", PNPM_FILES))
        self.assertIsNone(self.allowed("yarn add --dev eslint", NODE_FILES))
        self.assertIsNone(self.allowed("yarn add --dev eslint", PNPM_FILES))
        self.assertIsNone(self.allowed("pnpm add -D eslint", NODE_FILES))
        self.assertIsNone(self.allowed("pnpm add -D eslint", YARN_FILES))
        self.assertIsNotNone(fix_commands.allowed_install("npm install -D lodash", ["package.json"]))
        self.assertIsNone(fix_commands.allowed_install("npm install -D lodash", None))

    def test_not_a_text_or_out_of_bounds_is_refused(self):
        for command in (None, 3, b"npm install -D lodash", ["npm", "install", "-D", "lodash"], {}):
            with self.subTest(command=command):
                self.assertIsNone(self.allowed(command))
        many = " ".join(f"p{number}" for number in range(fix_commands.MAX_INSTALL_PACKAGES))
        self.assertIsNotNone(self.allowed(f"npm install -D {many}"))
        self.assertIsNone(self.allowed(f"npm install -D {many} one-more"))
        self.assertIsNotNone(self.allowed("npm install -D " + "a" * 214))
        self.assertIsNone(self.allowed("npm install -D " + "a" * 215))
        self.assertIsNotNone(self.allowed("npm install -D lodash@" + "1" * 64))
        self.assertIsNone(self.allowed("npm install -D lodash@" + "1" * 65))
        long_but_valid = " ".join(chr(97 + number) * 100 for number in range(6))
        self.assertGreater(len(long_but_valid), fix_commands.MAX_INSTALL - 20)
        self.assertIsNone(self.allowed(f"npm install -D {long_but_valid}"))

    def test_the_command_is_rebuilt_from_validated_parts_never_the_text_given(self):
        for command, files, argv in (
            ("npm install -D lodash@4.17.21", NODE_FILES,
             ["npm", "install", "--save-dev", "--ignore-scripts", "lodash@4.17.21"]),
            ("npm install --save-dev a b@^1.2.3", NODE_FILES,
             ["npm", "install", "--save-dev", "--ignore-scripts", "a", "b@^1.2.3"]),
            ("composer require --dev a/b c/d:^1.0", PHP_FILES,
             ["composer", "require", "--dev", "--no-scripts", "--no-plugins", "--no-interaction",
              "a/b", "c/d:^1.0"]),
            ("yarn add --dev eslint", YARN_FILES,
             ["yarn", "add", "--dev", "--ignore-scripts", "eslint"]),
            ("pnpm add -D eslint", PNPM_FILES,
             ["pnpm", "add", "--save-dev", "--ignore-scripts", "eslint"]),
        ):
            with self.subTest(command=command):
                found = self.allowed(command, files)
                self.assertEqual(shlex.split(found["command"]), argv)
                self.assertNotEqual(found["command"], command)
        # Every option a manager gets is fixed here and cuts the scripts of what it installs.
        for command, flags in (
            ("npm install -D lodash", ("--ignore-scripts",)),
            ("npm install --save-exact lodash@1.0.0", ("--ignore-scripts",)),
            ("npm install --package-lock-only", ("--ignore-scripts",)),
            ("yarn add --dev eslint", ("--ignore-scripts",)),
            ("pnpm add -D eslint", ("--ignore-scripts",)),
            ("composer require --dev a/b", ("--no-scripts", "--no-plugins", "--no-interaction")),
            ("composer update --lock", ("--no-scripts", "--no-plugins", "--no-interaction")),
        ):
            with self.subTest(command=command):
                words = shlex.split(self.allowed(command, files_of(command))["command"])
                for flag in flags:
                    self.assertEqual(words.count(flag), 1)

    def test_package_names_and_versions_are_checked_by_their_own_patterns(self):
        for command in (
            "npm install -D a", "npm install -D a0", "npm install -D a.b", "npm install -D a_b",
            "npm install -D a-b", "npm install -D @a/b", "npm install -D @a-b/c.d",
            "npm install -D 0a", "npm install -D a@latest", "npm install -D a@next",
            "npm install -D a@1.x", "npm install -D a@^1", "npm install -D a@~1.2",
            "npm install -D a@1.0.0-beta.2", "composer require --dev a/b",
            "composer require --dev a.b/c_d", "composer require --dev a-b/c-d:^1.2",
            "composer require --dev a/b:dev-main", "composer require --dev a/b:1.0.0-RC1",
        ):
            with self.subTest(accepted=command):
                self.assertIsNotNone(self.allowed(command, files_of(command)))
        for command in (
            "npm install -D a/b", "npm install -D @a", "npm install -D @a/b/c",
            "npm install -D a@1/2", "npm install -D a@1:2", "npm install -D a@b@c",
            "composer require --dev a", "composer require --dev a/b/c",
            "composer require --dev @a/b", "composer require --dev a/b:1/2",
            "composer require --dev a/b:1:2", "composer require --dev a/b@1",
        ):
            with self.subTest(refused=command):
                self.assertIsNone(self.allowed(command, files_of(command)))


class InstallTargetTest(unittest.TestCase):
    def install(self, command, image, dir="web", private=False):
        return {"name": f"{command} ({dir})", "dir": dir, "image": image, "kind": "install",
                "command": command, "env": {}, "source": "package.json", "private": private}

    def test_the_image_is_the_one_of_the_install_command_of_the_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            commands = [
                self.install("npm ci", "node:18-alpine"),
                self.install("composer install", "composer:2", dir=""),
                self.install("cd web/theme && npm ci", "node:16-alpine", dir=""),
                {**self.install("npm run build", "node:20-alpine"), "kind": "build"},
            ]
            self.assertEqual(fix_commands.install_target("npm", Path(tmp), "web", commands),
                             {"image": "node:18-alpine", "private": False})
            self.assertEqual(fix_commands.install_target("yarn", Path(tmp), "web", commands)["image"],
                             fix_commands.DEFAULT_NODE)
            self.assertEqual(
                fix_commands.install_target("npm", Path(tmp), "web/theme", commands)["image"],
                "node:16-alpine")
            self.assertEqual(fix_commands.install_target("composer", Path(tmp), "", commands),
                             {"image": "composer:2", "private": False})

    def test_a_private_install_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            commands = [self.install("composer install", "php:8.2-cli", dir="", private=True)]
            self.assertEqual(fix_commands.install_target("composer", Path(tmp), "", commands),
                             {"image": "php:8.2-cli", "private": True})

    def test_without_a_command_the_image_comes_from_the_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, "web/package.json", {"engines": {"node": ">=18"}})
            write(tmp, "composer.json", {"require": {}})
            self.assertEqual(fix_commands.install_target("npm", Path(tmp), "web", []),
                             {"image": "node:18-alpine", "private": False})
            self.assertEqual(fix_commands.install_target("pnpm", Path(tmp), "web", [])["image"],
                             "node:18-alpine")
            self.assertEqual(fix_commands.install_target("composer", Path(tmp), "", []),
                             {"image": fix_commands.COMPOSER_IMAGE, "private": False})
            write(tmp, "composer.json", {"repositories": [{"type": "composer",
                                                           "url": "https://packages.example.org"}]})
            self.assertTrue(fix_commands.install_target("composer", Path(tmp), "", [])["private"])
            write(tmp, "other/package.json", {})
            self.assertEqual(fix_commands.install_target("npm", Path(tmp), "other", [])["image"],
                             fix_commands.DEFAULT_NODE)
            self.assertEqual(fix_commands.install_target("npm", Path(tmp), "absent", [])["image"],
                             fix_commands.DEFAULT_NODE)
            for target in (fix_commands.install_target("npm", Path(tmp), "web", []),
                           fix_commands.install_target("composer", Path(tmp), "", [])):
                self.assertTrue(fix_commands.valid_image(target["image"]))


if __name__ == "__main__":
    unittest.main()
