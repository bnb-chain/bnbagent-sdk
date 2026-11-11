"""Offline regression tests for the actual trusted workflow programs (stdlib only)."""

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import textwrap
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]
VERSION = "0.6.1"


def run_step(file, name):
    workflow = (ROOT / ".github/workflows" / file).read_text()
    for part in workflow.split("      - name: ")[1:]:
        if part.splitlines()[0] == name:
            match = re.search(r"        run: \|\n((?:          .*\n|\n)*)", part)
            if match:
                return textwrap.dedent(match[1])
    raise AssertionError(f"Missing runnable step: {name}")


def python_program(step):
    return step.split("python - <<'PYTHON'\n", 1)[1].split("\nPYTHON", 1)[0]


def job_definition(file, name):
    workflow = (ROOT / ".github/workflows" / file).read_text()
    start = workflow.index(f"\n  {name}:\n")
    rest = workflow[start + 1 :]
    return re.split(r"\n  [a-z][a-z-]*:\n", rest, maxsplit=1)[0]


class ReleaseAuthorizationTests(unittest.TestCase):
    ACCOUNTS = {
        "jardenx": "274764117",
        "aiden-cao": "104969608",
        "robot-ux": "81206731",
    }

    def authorize(self, actor_id, triggering_actor, triggering_id, api_failure=False):
        script = run_step("npm-publish.yml", "Authorize release actors")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stub = root / "gh"
            stub.write_text(
                "#!/usr/bin/env python3\n"
                + textwrap.dedent("""
                import os, pathlib, sys
                pathlib.Path("api-called").touch()
                if os.environ["API_FAILURE"] == "true":
                    sys.exit(1)
                assert sys.argv[1:] == ["api", "--method", "GET", "users/" + os.environ["RELEASE_TRIGGERING_ACTOR"], "--jq", ".id"]
                print(os.environ["TRIGGERING_ID"])
            """)
            )
            stub.chmod(0o755)
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=root,
                env={
                    **os.environ,
                    "PATH": str(root) + os.pathsep + os.environ["PATH"],
                    "RELEASE_ACTOR_ID": actor_id,
                    "RELEASE_TRIGGERING_ACTOR": triggering_actor,
                    "TRIGGERING_ID": triggering_id,
                    "API_FAILURE": str(api_failure).lower(),
                },
                capture_output=True,
                text=True,
            )
            return result, (root / "api-called").exists()

    def test_all_three_accounts_can_initiate_and_rerun(self):
        for actor_id in self.ACCOUNTS.values():
            for login, triggering_id in self.ACCOUNTS.items():
                with self.subTest(actor_id=actor_id, triggering_actor=login):
                    result, _ = self.authorize(actor_id, login, triggering_id)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_unapproved_original_actor_is_rejected_before_api_lookup(self):
        result, called = self.authorize("999", "jardenx", self.ACCOUNTS["jardenx"])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(called)

    def test_unapproved_rerunner_and_reused_username_are_rejected(self):
        for login in ("outsider", "jardenx"):
            result, _ = self.authorize(self.ACCOUNTS["jardenx"], login, "999")
            self.assertNotEqual(result.returncode, 0)

    def test_failed_identity_lookup_fails_closed(self):
        result, _ = self.authorize(
            self.ACCOUNTS["jardenx"], "jardenx", "", api_failure=True
        )
        self.assertNotEqual(result.returncode, 0)

    def test_renamed_account_keeps_access_by_id(self):
        result, _ = self.authorize(
            self.ACCOUNTS["jardenx"], "renamed-jardenx", self.ACCOUNTS["jardenx"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_malformed_triggering_login_is_rejected_without_lookup(self):
        result, called = self.authorize(
            self.ACCOUNTS["jardenx"], "../jardenx", self.ACCOUNTS["jardenx"]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(called)

    def test_every_privileged_job_rechecks_the_same_actor_policy(self):
        expected = run_step("npm-publish.yml", "Authorize release actors")
        for file, jobs in (
            ("npm-publish.yml", ("validate", "version-commit", "production-release")),
            ("npm-publish-artifact.yml", ("publish",)),
            (
                "pypi-release.yaml",
                ("build", "version-commit", "publish", "github-release"),
            ),
        ):
            for name in jobs:
                with self.subTest(file=file, job=name):
                    job = job_definition(file, name)
                    self.assertTrue(
                        job.split("    steps:\n", 1)[1].startswith(
                            "      - name: Authorize release actors\n"
                        )
                    )
                    self.assertIn(textwrap.indent(expected, "          "), job)


class AbiVerificationTests(unittest.TestCase):
    def verify(self, change=None):
        program = python_program(
            run_step(
                "pypi-release.yaml",
                "Verify built wheel, sdist, ABIs, and smoke install",
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "abis").mkdir()
            abi = b'[{"type":"function","name":"transfer"}]'
            (root / "abis/ERC20.json").write_bytes(abi)
            project = root / "python"
            dist = project / "dist"
            dist.mkdir(parents=True)
            metadata = f"Name: bnbagent\nVersion: {VERSION}\n"
            with zipfile.ZipFile(
                dist / f"bnbagent-{VERSION}-py3-none-any.whl", "w"
            ) as wheel:
                wheel.writestr(f"bnbagent-{VERSION}.dist-info/METADATA", metadata)
                wheel.writestr("bnbagent/abis/ERC20.json", abi)
            entries = [
                ("PKG-INFO", metadata.encode()),
                ("bnbagent/abis/ERC20.json", abi),
            ]
            if change == "missing":
                entries.pop()
            elif change == "tampered":
                entries[-1] = (entries[-1][0], b"[]")
            elif change == "extra":
                entries.append(("bnbagent/abis/Unexpected.json", b"[]"))
            elif change == "duplicate":
                entries.append(entries[-1])
            with tarfile.open(dist / f"bnbagent-{VERSION}.tar.gz", "w:gz") as sdist:
                for name, content in entries:
                    member = tarfile.TarInfo(f"bnbagent-{VERSION}/{name}")
                    member.size = len(content)
                    sdist.addfile(member, io.BytesIO(content))
            return subprocess.run(
                ["python3", "-c", program],
                cwd=project,
                env={**os.environ, "VERSION": VERSION},
                capture_output=True,
                text=True,
            )

    def test_valid_abis(self):
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sdist_abi_mismatch_is_rejected(self):
        for change in ("missing", "tampered", "extra", "duplicate"):
            with self.subTest(change=change):
                self.assertNotEqual(self.verify(change).returncode, 0)

    def test_rebuilt_wheel_abis_are_compared(self):
        step = run_step(
            "pypi-release.yaml", "Verify built wheel, sdist, ABIs, and smoke install"
        )
        program = step.split("REBUILD_DIR=\"$REBUILD_DIR\" python - <<'PYTHON'\n")[
            1
        ].split("\nPYTHON")[0]
        for valid in (False, True):
            with self.subTest(valid=valid), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "wheels").mkdir()
                content = b"[]"
                (root / "release-manifest.json").write_text(
                    json.dumps(
                        {"abis": {"ERC20.json": hashlib.sha256(content).hexdigest()}}
                    )
                )
                with zipfile.ZipFile(root / "wheels/rebuilt.whl", "w") as wheel:
                    wheel.writestr(
                        "bnbagent/abis/ERC20.json", content if valid else b"[1]"
                    )
                result = subprocess.run(
                    ["python3", "-c", program],
                    cwd=root,
                    env={**os.environ, "REBUILD_DIR": str(root)},
                    capture_output=True,
                )
                self.assertEqual(result.returncode == 0, valid, result.stderr)


class NpmConfigurationTests(unittest.TestCase):
    def test_actual_config_ignores_project_user_global_and_environment(self):
        setup = run_step("npm-publish-artifact.yml", "Create isolated npm command")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            (project / "package.json").write_text('{"name":"test"}')
            bad_config = "@bnbagent:registry=http://127.0.0.1:1/\nproxy=http://127.0.0.1:2/\nhttps-proxy=http://127.0.0.1:3/\nstrict-ssl=false\n"
            (project / ".npmrc").write_text(bad_config)
            (root / ".npmrc").write_text(bad_config)
            global_config = root / "global-npmrc"
            global_config.write_text(bad_config)
            env = {
                **os.environ,
                "HOME": str(root),
                "RUNNER_TEMP": str(root),
                "GITHUB_PATH": str(root / "path"),
                "GITHUB_ENV": str(root / "env"),
                "NPM_CONFIG_USERCONFIG": str(root / ".npmrc"),
                "npm_config_globalconfig": str(global_config),
                "NPM_CONFIG_REGISTRY": "http://127.0.0.1:4/",
                "npm_config_@bnbagent:registry": "http://127.0.0.1:5/",
                "HTTPS_PROXY": "http://127.0.0.1:6/",
            }
            subprocess.run(["bash", "-euc", setup], cwd=project, env=env, check=True)
            result = subprocess.run(
                [str(root / "npm-safe"), "config", "list", "--json"],
                cwd=project,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            config = json.loads(result.stdout)
            self.assertEqual(config["registry"], "https://registry.npmjs.org/")
            self.assertEqual(
                config["@bnbagent:registry"], "https://registry.npmjs.org/"
            )
            self.assertTrue(config["strict-ssl"])
            self.assertTrue(config["ignore-scripts"])
            self.assertIsNone(config["proxy"])
            self.assertIsNone(config["https-proxy"])

            # Let the real npm CLI resolve its publishing destination without
            # uploading or executing package lifecycle scripts.
            tarball = root / "package.tgz"
            with tarfile.open(tarball, "w:gz", format=tarfile.USTAR_FORMAT) as archive:
                manifest = json.dumps({"name": "@bnbagent/sdk", "version": VERSION})
                member = tarfile.TarInfo("package/package.json")
                member.size = len(manifest.encode())
                archive.addfile(member, io.BytesIO(manifest.encode()))
            result = subprocess.run(
                [
                    str(root / "npm-safe"),
                    "publish",
                    str(tarball),
                    "--dry-run",
                    "--force",
                    "--offline",
                    "--provenance=false",
                    "--tag=alpha",
                    "--access=public",
                ],
                cwd=project,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("Publishing to https://registry.npmjs.org/", result.stderr)
            self.assertNotIn("127.0.0.1", result.stderr)


class NpmArchiveTests(unittest.TestCase):
    def test_trusted_repacker_rejects_legacy_headers_and_is_idempotent(self):
        step = run_step(
            "npm-publish-artifact.yml",
            "Verify artifact identity and repack clean archive",
        )
        program = step.split("node <<'NODE'\n")[1].split("\nNODE")[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw.tgz"
            clean = root / "clean.tgz"
            again = root / "again.tgz"
            manifest = json.dumps(
                {
                    "name": "@bnbagent/sdk",
                    "version": VERSION,
                    "repository": {"url": "https://github.com/test/repo.git"},
                    "scripts": {"postpublish": "exit 1"},
                }
            ).encode()
            for format in (tarfile.USTAR_FORMAT, tarfile.GNU_FORMAT):
                with tarfile.open(raw, "w:gz", format=format) as archive:
                    member = tarfile.TarInfo("package/package.json")
                    member.size = len(manifest)
                    archive.addfile(member, io.BytesIO(manifest))
                env = {
                    **os.environ,
                    "RAW_TARBALL": str(raw),
                    "CLEAN_TARBALL": str(clean),
                    "EXPECTED_VERSION": VERSION,
                    "EXPECTED_REPO": "https://github.com/test/repo.git",
                }
                result = subprocess.run(
                    ["node", "-e", program], env=env, capture_output=True
                )
                if format == tarfile.GNU_FORMAT:
                    self.assertNotEqual(result.returncode, 0)
                    continue
                self.assertEqual(result.returncode, 0, result.stderr)
                with tarfile.open(clean) as archive:
                    self.assertNotIn(
                        "scripts",
                        json.load(archive.extractfile("package/package.json")),
                    )
                result = subprocess.run(
                    ["node", "-e", program],
                    env={**env, "RAW_TARBALL": str(clean), "CLEAN_TARBALL": str(again)},
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(clean.read_bytes(), again.read_bytes())


class WorkflowContractTests(unittest.TestCase):
    def test_privileged_jobs_never_checkout_or_execute_package_code(self):
        for file, names in (
            ("npm-publish.yml", ("version-commit", "production-release")),
            ("npm-publish-artifact.yml", ("publish",)),
            ("pypi-release.yaml", ("version-commit", "publish", "github-release")),
        ):
            for name in names:
                with self.subTest(file=file, job=name):
                    job = job_definition(file, name)
                    self.assertNotIn("actions/checkout", job)
                    self.assertNotIn("pnpm exec", job)
                    self.assertNotIn("import bnbagent", job)
                    self.assertNotIn("uv pip install", job)
                    self.assertFalse(
                        "contents: write" in job and "id-token: write" in job
                    )

    def test_builds_have_no_publishing_or_write_permissions(self):
        for file, names in (
            ("npm-publish.yml", ("resolve", "verify", "package", "production")),
            ("pypi-release.yaml", ("build",)),
        ):
            for name in names:
                with self.subTest(file=file, job=name):
                    job = job_definition(file, name)
                    for forbidden in (
                        "contents: write",
                        "id-token: write",
                        "secrets.",
                    ):
                        self.assertNotIn(forbidden, job)

    def test_retries_use_unique_uploads_and_download_exact_build_artifact_ids(self):
        for file in (
            "npm-publish.yml",
            "pypi-release.yaml",
            "npm-publish-artifact.yml",
        ):
            workflow = (ROOT / ".github/workflows" / file).read_text()
            for step in workflow.split("      - name: "):
                if "uses: actions/upload-artifact@v4" in step:
                    self.assertIn("github.run_attempt", step)
                if "uses: actions/download-artifact@v4" in step:
                    self.assertIn("artifact-ids:", step)
                    self.assertIn("merge-multiple: true", step)
                    self.assertNotIn("github.run_attempt", step)

    def test_skipped_dependencies_failures_and_cancellation(self):
        for file, name, valid, invalid in (
            (
                "npm-publish.yml",
                "alpha-publish",
                {"resolve": "success", "package": "success", "verify": "skipped"},
                {"resolve": "success", "package": "success", "verify": "failure"},
            ),
            (
                "pypi-release.yaml",
                "publish",
                {"build": "success", "version-commit": "skipped"},
                {"build": "success", "version-commit": "failure"},
            ),
        ):
            job = job_definition(file, name)
            expression = job.split("    if: >-\n")[1].split("\n    needs:")[0]
            for results, cancelled, expected in (
                (valid, False, True),
                (invalid, False, False),
                (valid, True, False),
            ):
                actual = expression.replace("always()", "True").replace(
                    "!cancelled()", str(not cancelled)
                )
                actual = actual.replace("inputs.release_type", repr("alpha"))
                actual = actual.replace("!inputs.release_to_prod", "True").replace(
                    "inputs.release_to_prod", "False"
                )
                for dependency, result in results.items():
                    actual = actual.replace(f"needs.{dependency}.result", repr(result))
                actual = actual.replace("&&", " and ").replace("||", " or ")
                self.assertEqual(
                    eval(" ".join(actual.split()), {"__builtins__": {}}), expected
                )

    def test_tag_named_main_cannot_pass_default_branch_dispatch(self):
        step = run_step("npm-publish.yml", "Require the default-branch workflow")
        for ref, expected in (("refs/heads/main", 0), ("refs/tags/main", 1)):
            result = subprocess.run(
                ["bash", "-c", step],
                env={
                    **os.environ,
                    "DEFAULT_BRANCH": "main",
                    "REF_NAME": ref,
                    "RELEASE_TYPE": "production",
                    "SOURCE_REF": "",
                },
                capture_output=True,
            )
            self.assertEqual(result.returncode, expected)


class VersionCommitTests(unittest.TestCase):
    def commit(self, component, scenario="valid"):
        workflow = "npm-publish.yml" if component == "npm" else "pypi-release.yaml"
        name = (
            "Commit version bump through the GitHub API"
            if component == "npm"
            else "Commit version update through the GitHub API"
        )
        program = python_program(run_step(workflow, name))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "version-files").mkdir()
            package = {
                "name": "@bnbagent/sdk",
                "version": "0.6.0",
                "scripts": {"test": "echo test"},
            }
            staged = {**package, "version": VERSION}
            if scenario == "modified-manifest":
                staged["scripts"] = {"test": "malicious"}
            (root / "version-files/package.json").write_text(json.dumps(staged))
            (root / "version-files/pnpm-lock.yaml").write_text("lock\n")
            stub = root / "gh"
            stub.write_text(
                "#!/usr/bin/env python3\n"
                + textwrap.dedent("""
                import base64, json, os, pathlib, sys
                if "graphql" in sys.argv:
                    payload = json.load(sys.stdin)
                    pathlib.Path("request.json").write_text(json.dumps(payload))
                    result = {"data": {"createCommitOnBranch": {"commit": {
                        "oid": "b" * 40,
                        "signature": {"isValid": os.environ["SCENARIO"] != "unsigned", "wasSignedByGitHub": True}
                    }}}}
                    if os.environ["SCENARIO"] == "race":
                        result = {"errors": [{"message": "expectedHeadOid mismatch"}]}
                    print(json.dumps(result))
                else:
                    endpoint = sys.argv[2]
                    if "package.json" in endpoint:
                        content = os.environ["ORIGINAL_PACKAGE"].encode()
                    elif "pnpm-lock.yaml" in endpoint:
                        content = b"lock\\n"
                    else:
                        content = b'__version__ = "0.6.0"\\n'
                    print(json.dumps({"content": base64.b64encode(content).decode()}))
            """)
            )
            stub.chmod(0o755)
            env = {
                **os.environ,
                "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "V": VERSION,
                "VERSION": VERSION,
                "SOURCE_SHA": "a" * 40,
                "GITHUB_SHA": "a" * 40,
                "COMMIT": "true",
                "BRANCH": "main",
                "GITHUB_REPOSITORY": "test/repo",
                "GITHUB_OUTPUT": str(root / "output"),
                "SCENARIO": scenario,
                "ORIGINAL_PACKAGE": json.dumps(package),
            }
            result = subprocess.run(
                ["python3", "-c", program],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
            )
            payload = (
                json.loads((root / "request.json").read_text())
                if (root / "request.json").exists()
                else None
            )
            output = (root / "output").read_text() if (root / "output").exists() else ""
            return result, payload, output

    def test_only_fixed_version_file_is_committed_with_expected_head(self):
        for component, path in (
            ("npm", "typescript/package.json"),
            ("pypi", "python/bnbagent/_version.py"),
        ):
            with self.subTest(component=component):
                result, payload, output = self.commit(component)
                self.assertEqual(result.returncode, 0, result.stderr)
                request = payload["variables"]["input"]
                self.assertEqual(request["expectedHeadOid"], "a" * 40)
                self.assertEqual(
                    [item["path"] for item in request["fileChanges"]["additions"]],
                    [path],
                )
                contents = base64.b64decode(
                    request["fileChanges"]["additions"][0]["contents"]
                )
                self.assertIn(VERSION.encode(), contents)
                self.assertEqual(output, "commit_sha=" + "b" * 40 + "\n")

    def test_unsigned_commit_or_concurrent_branch_change_fails(self):
        for component in ("npm", "pypi"):
            for scenario in ("unsigned", "race"):
                with self.subTest(component=component, scenario=scenario):
                    result, _, output = self.commit(component, scenario)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(output, "")

    def test_build_cannot_commit_other_manifest_changes(self):
        result, payload, _ = self.commit("npm", "modified-manifest")
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(payload)


class RegistryPublicationTests(unittest.TestCase):
    def publish(self, scenario, channel="latest"):
        step = run_step(
            "npm-publish-artifact.yml",
            "Verify registry state and publish identical archive",
        )
        program = step.split("node <<'NODE'\n")[1].split("\nNODE")[0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tarball = root / "package.tgz"
            tarball.write_bytes(b"verified archive")
            integrity = (
                "sha512-"
                + base64.b64encode(
                    hashlib.sha512(tarball.read_bytes()).digest()
                ).decode()
            )
            version = VERSION if channel == "latest" else VERSION + "-alpha.1"
            stub = root / "npm-safe"
            stub.write_text(
                "#!/usr/bin/env python3\n"
                + textwrap.dedent("""
                import json, os, pathlib, sys
                args = sys.argv[1:]
                with open("calls.jsonl", "a") as output:
                    output.write(json.dumps(args) + "\\n")
                scenario = os.environ["SCENARIO"]
                published = pathlib.Path("published").exists()
                exists = scenario in ("existing", "mismatch") or published
                if args[0] == "publish":
                    if scenario == "publish-failure":
                        sys.exit(1)
                    pathlib.Path("published").touch()
                    print("{}")
                elif scenario == "query-failure":
                    print(json.dumps({"error": {"code": "E503"}}))
                    sys.exit(1)
                elif args[2] == "dist-tags":
                    tags = {"latest": "0.6.0"}
                    if exists:
                        tags[os.environ["CHANNEL"]] = os.environ["VERSION"]
                    print(json.dumps(tags))
                elif args[2] == "dist.integrity" and exists:
                    print(json.dumps("sha512-wrong" if scenario == "mismatch" else os.environ["EXPECTED_INTEGRITY"]))
                else:
                    print(json.dumps({"error": {"code": "E404"}}))
                    sys.exit(1)
            """)
            )
            stub.chmod(0o755)
            env = {
                **os.environ,
                "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "VERSION": version,
                "CHANNEL": channel,
                "TARBALL": str(tarball),
                "EXPECTED_INTEGRITY": integrity,
                "SCENARIO": scenario,
            }
            result = subprocess.run(
                ["node", "-e", program],
                env=env,
                cwd=root,
                capture_output=True,
                text=True,
            )
            calls = [
                json.loads(line)
                for line in (root / "calls.jsonl").read_text().splitlines()
            ]
            return result, [args for args in calls if args[0] == "publish"]

    def test_publish_same_archive_under_correct_tag_and_provenance(self):
        for channel in ("latest", "alpha"):
            with self.subTest(channel=channel):
                result, calls = self.publish("new", channel)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(calls), 1)
                self.assertTrue(calls[0][1].endswith("/package.tgz"))
                self.assertIn(f"--tag={channel}", calls[0])
                self.assertIn(
                    f"--provenance={'true' if channel == 'latest' else 'false'}",
                    calls[0],
                )

    def test_existing_matching_archive_is_not_uploaded_again(self):
        result, calls = self.publish("existing")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [])

    def test_mismatching_digest_and_registry_failure_block_upload(self):
        for scenario in ("mismatch", "query-failure"):
            with self.subTest(scenario=scenario):
                result, calls = self.publish(scenario)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])

    def test_failed_upload_fails_the_publish_job(self):
        result, calls = self.publish("publish-failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
