/**
 * Release helper (no external deps; run via tsx/node in CI).
 *
 * Publishable packages = everything under packages/* whose package.json is NOT
 * `"private": true`. When there is no packages/ directory the repo is treated as
 * a SINGLE package rooted at RELEASE_PACKAGE_DIR (default ".", i.e. the cwd) —
 * this repo ships one package (@bnbagent/sdk) from typescript/. All publishable
 * packages share ONE version (see docs/releasing.md).
 *
 *   prepare <current|patch|minor|major>
 *                              publish the current version, resume an untagged
 *                              release commit, or bump every package.
 *   freeze                     rewrite internal `workspace:*` deps to `^<version>`
 *                              for publishing — run AFTER the version commit, NOT committed
 *                              (committing it would break local workspace linking).
 *   pack                       run npm's package-content smoke check for every package.
 *   publish [--dry-run]        publish in dependency order, skipping versions already on npm.
 *   changelog [<range>]        group commits in <range> (default: last tag..HEAD) by
 *                              Conventional-Commit type into Markdown for the GitHub Release.
 */
import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import * as posix from "node:path/posix";
import { pathToFileURL } from "node:url";

const PACKAGES_DIR = "packages";
/**
 * Git tags for this component are the full npm coordinate
 * (`@bnbagent/sdk@vX.Y.Z`) so they never collide with the Python component's
 * `bnbagent-vX.Y.Z` line on the shared GitHub Releases page.
 */
export const RELEASE_TAG_PREFIX = "@bnbagent/sdk@v";
const REPO_URL = "https://github.com/bnb-chain/bnbagent-sdk";

export function releaseTag(version: string): string {
  return `${RELEASE_TAG_PREFIX}${version}`;
}
/** In single-package mode, the dir (relative to cwd) holding the package.json. */
const SINGLE_PACKAGE_DIR = process.env.RELEASE_PACKAGE_DIR ?? ".";
/** Lockfile committed alongside the version bump (relative to each package dir). */
const LOCKFILE = "pnpm-lock.yaml";
const WORKSPACE_DEP_FIELDS = [
  "dependencies",
  "peerDependencies",
  "devDependencies",
] as const;

interface Pkg {
  dir: string;
  name: string;
  pkg: Record<string, any>;
}

interface RunResult {
  status: number | null;
  stdout: Buffer;
  stderr: Buffer;
}

/** Thin wrapper over node's spawnSync with the ergonomics the script needs. */
function runCmd(
  cmd: string[],
  opts: { cwd?: string; inherit?: boolean } = {},
): RunResult {
  const r = spawnSync(cmd[0]!, cmd.slice(1), {
    cwd: opts.cwd,
    stdio: opts.inherit ? "inherit" : "pipe",
  });
  return {
    status: r.status,
    stdout: r.stdout ?? Buffer.alloc(0),
    stderr: r.stderr ?? Buffer.alloc(0),
  };
}

function sh(cmd: string[]): string {
  return runCmd(cmd).stdout.toString().trim();
}

/** Busy-free synchronous sleep (no external deps). */
function sleepSync(milliseconds: number): void {
  Atomics.wait(
    new Int32Array(new SharedArrayBuffer(4)),
    0,
    0,
    Math.max(0, milliseconds),
  );
}

/**
 * Publishable packages: packages/* with a name and not marked private.
 * Falls back to a single package rooted at RELEASE_PACKAGE_DIR when there is no
 * packages/ directory (this repo's layout).
 */
function discoverPackages(): Pkg[] {
  if (existsSync(PACKAGES_DIR)) {
    const out: Pkg[] = [];
    for (const entry of readdirSync(PACKAGES_DIR)) {
      const dir = `${PACKAGES_DIR}/${entry}`;
      const path = `${dir}/package.json`;
      if (!existsSync(path)) continue;
      const pkg = JSON.parse(readFileSync(path, "utf8")) as Record<string, any>;
      if (pkg.private === true || !pkg.name) continue;
      out.push({ dir, name: pkg.name, pkg });
    }
    if (out.length === 0)
      throw new Error(`No publishable packages under ${PACKAGES_DIR}/`);
    return out;
  }

  const path = `${SINGLE_PACKAGE_DIR}/package.json`;
  if (!existsSync(path))
    throw new Error(
      `No ${PACKAGES_DIR}/ and no package.json at "${SINGLE_PACKAGE_DIR}" (set RELEASE_PACKAGE_DIR to the package directory)`,
    );
  const pkg = JSON.parse(readFileSync(path, "utf8")) as Record<string, any>;
  if (pkg.private === true || !pkg.name)
    throw new Error(`Root package at "${SINGLE_PACKAGE_DIR}" is private/unnamed`);
  return [{ dir: SINGLE_PACKAGE_DIR, name: pkg.name, pkg }];
}

const writePkg = (p: Pkg) =>
  writeFileSync(`${p.dir}/package.json`, `${JSON.stringify(p.pkg, null, 2)}\n`);

function currentVersion(): string {
  const packages = discoverPackages();
  const versions = new Set(packages.map((p) => String(p.pkg.version)));
  if (versions.size !== 1)
    throw new Error(
      `Publishable packages are not lockstep: ${[...versions].join(", ")}`,
    );
  return String(packages[0]!.pkg.version);
}

export function resolveVersion(current: string, kind: string): string {
  const m = /^(\d+)\.(\d+)\.(\d+)$/.exec(current);
  if (!m)
    throw new Error(
      `Unsupported current version "${current}" (expected X.Y.Z)`,
    );
  if (kind === "current") return current;

  let [major, minor, patch] = [Number(m[1]), Number(m[2]), Number(m[3])];
  if (kind === "major") {
    major++;
    minor = 0;
    patch = 0;
  } else if (kind === "minor") {
    minor++;
    patch = 0;
  } else if (kind === "patch") {
    patch++;
  } else {
    throw new Error(
      `Unknown version selection "${kind}" (use current|patch|minor|major)`,
    );
  }
  return `${major}.${minor}.${patch}`;
}

function bump(kind: string): string {
  const next = resolveVersion(currentVersion(), kind);
  for (const p of discoverPackages()) {
    p.pkg.version = next;
    writePkg(p);
  }
  return next;
}

interface LockRefreshResult {
  exitCode: number | null;
  stdout: Uint8Array;
  stderr: Uint8Array;
}

/** Keep prepare's stdout machine-readable; dependency-manager logs go to stderr. */
export function refreshLockfile(
  run: () => LockRefreshResult = () => {
    const r = runCmd(["pnpm", "install", "--lockfile-only"]);
    return { exitCode: r.status, stdout: r.stdout, stderr: r.stderr };
  },
  report: (chunk: Uint8Array) => void = (chunk) => {
    if (chunk.byteLength > 0) process.stderr.write(chunk);
  },
): void {
  const result = run();
  report(result.stdout);
  report(result.stderr);
  if (result.exitCode !== 0) {
    throw new Error(`Could not refresh ${LOCKFILE} (exit ${result.exitCode ?? 1})`);
  }
}

export function freezeWorkspaceDeps(
  pkg: Record<string, any>,
  version: string,
): void {
  for (const field of WORKSPACE_DEP_FIELDS) {
    const deps = pkg[field];
    if (!deps) continue;
    for (const name of Object.keys(deps)) {
      if (String(deps[name]).startsWith("workspace:"))
        deps[name] = `^${version}`;
    }
  }
}

function freeze(): void {
  const version = currentVersion();
  for (const p of discoverPackages()) {
    freezeWorkspaceDeps(p.pkg, version);
    writePkg(p);
  }
}

/** Resume the current untagged release commit; otherwise start the next version. */
function prepare(kind: string): { version: string; commit: boolean } {
  const version = currentVersion();
  resolveVersion(version, kind);
  const subject = sh(["git", "log", "-1", "--pretty=%s"]);
  const tag = sh(["git", "tag", "--list", releaseTag(version)]);
  if (subject === `chore(release): v${version} [skip ci]` && !tag) {
    return { version, commit: false };
  }
  if (kind === "current") {
    if (tag)
      throw new Error(
        `Cannot publish current version ${version}: tag ${releaseTag(version)} already exists`,
      );
    return { version, commit: false };
  }
  const next = bump(kind);
  refreshLockfile();
  return { version: next, commit: true };
}

/** Topological order so a package is published after the internal deps it needs. */
function publishOrder(pkgs: Pkg[]): Pkg[] {
  const byName = new Map(pkgs.map((p) => [p.name, p]));
  const seen = new Set<string>();
  const order: Pkg[] = [];
  const visit = (p: Pkg) => {
    if (seen.has(p.name)) return;
    seen.add(p.name);
    const deps = { ...p.pkg.dependencies, ...p.pkg.peerDependencies };
    for (const dep of Object.keys(deps)) {
      const d = byName.get(dep);
      if (d) visit(d);
    }
    order.push(p);
  };
  for (const p of pkgs) visit(p);
  return order;
}

function packageExists(name: string, version: string): boolean {
  const r = runCmd(["npm", "view", `${name}@${version}`, "version", "--json"]);
  if (r.status !== 0) return false;
  const output = r.stdout.toString().trim();
  try {
    return JSON.parse(output) === version;
  } catch {
    return output.replace(/^"|"$/g, "") === version;
  }
}

interface RegistryWaitOptions {
  attempts?: number;
  delayMs?: number;
  exists?: (name: string, version: string) => boolean;
  sleep?: (milliseconds: number) => void;
  report?: (message: string) => void;
}

/** Wait for npm metadata before publishing dependents or creating the tag. */
export function waitForPackage(
  name: string,
  version: string,
  options: RegistryWaitOptions = {},
): void {
  const attempts = options.attempts ?? 12;
  const delayMs = options.delayMs ?? 5_000;
  const exists = options.exists ?? packageExists;
  const sleep = options.sleep ?? sleepSync;
  const report = options.report ?? console.log;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    if (exists(name, version)) return;
    if (attempt < attempts) {
      report(
        `waiting for ${name}@${version} to become visible on npm ` +
          `(${attempt}/${attempts})`,
      );
      sleep(delayMs);
    }
  }
  throw new Error(
    `${name}@${version} was published but is still not visible on npm after ` +
      `${attempts} checks; refusing to publish dependents or create the tag`,
  );
}

function pack(): void {
  for (const p of publishOrder(discoverPackages())) {
    const r = runCmd(["npm", "pack", "--dry-run", "--json"], {
      cwd: p.dir,
      inherit: true,
    });
    if (r.status !== 0) process.exit(r.status ?? 1);
  }
}

interface PublishOneOptions {
  exists?: (name: string, version: string) => boolean;
  run?: (pkg: Pkg) => number | null;
  tag?: string;
  wait?: (name: string, version: string) => void;
  report?: (message: string) => void;
}

export function npmPublishArgs(tag?: string): string[] {
  const args = ["npm", "publish", "--access", "public"];
  if (tag) args.push("--tag", tag);
  return args;
}

export function publishOne(
  p: Pkg,
  dryRun: boolean,
  options: PublishOneOptions = {},
): void {
  const exists = options.exists ?? packageExists;
  const run =
    options.run ??
    ((pkg: Pkg) =>
      runCmd(npmPublishArgs(options.tag), {
        cwd: pkg.dir,
        inherit: true,
      }).status);
  const wait = options.wait ?? waitForPackage;
  const report = options.report ?? console.log;
  const version = String(p.pkg.version);
  if (dryRun) {
    report(`would publish ${p.name}@${version} (${p.dir})`);
    return;
  }
  if (exists(p.name, version)) {
    report(`skip ${p.name}@${version} (already published)`);
    return;
  }
  const exitCode = run(p);
  if (exitCode !== 0) process.exit(exitCode ?? 1);
  wait(p.name, version);
}

function publish(dryRun: boolean, tag?: string): void {
  for (const p of publishOrder(discoverPackages())) {
    publishOne(p, dryRun, { tag });
  }
}

const TYPE_TITLES: Record<string, string> = {
  feat: "Features",
  fix: "Bug Fixes",
  perf: "Performance",
  refactor: "Refactoring",
  docs: "Documentation",
};
const ORDER = [
  "Features",
  "Bug Fixes",
  "Performance",
  "Refactoring",
  "Documentation",
  "Other",
];

/**
 * Range for the component changelog: since the previous @bnbagent/sdk tag, or
 * ALL history for the first release. Never falls back to another component's
 * tag (e.g. Python's `bnbagent-v*`) — that would silently drop every commit
 * older than an unrelated release.
 */
export function releaseRange(lastSdkTag: string): string {
  return lastSdkTag ? `${lastSdkTag}..HEAD` : "HEAD";
}

const SEP = "\x1f";

/**
 * The changelog scopes to this component's sources: typescript/ plus the
 * shared repo-root abis/ (vendored into the published package by codegen).
 * `:(top)` anchors the pathspecs to the repo root — the script runs from
 * typescript/, where a bare `typescript/` pathspec would match nothing.
 */
export function changelogLogArgs(rev: string): string[] {
  return [
    "git",
    "log",
    "--no-merges",
    `--pretty=%h${SEP}%s`,
    rev,
    "--",
    ":(top)typescript/",
    ":(top)abis/",
  ];
}

function changelog(range?: string): string {
  let rev = range;
  let previousTag = "";
  if (!rev) {
    previousTag = sh([
      "git",
      "describe",
      "--tags",
      "--match",
      `${RELEASE_TAG_PREFIX}[0-9]*`,
      "--abbrev=0",
    ]);
    rev = releaseRange(previousTag);
  }
  const log = sh(changelogLogArgs(rev));

  const groups: Record<string, string[]> = {};
  const breaking: string[] = [];
  for (const line of log.split("\n").filter(Boolean)) {
    const [hash, subject = ""] = line.split(SEP);
    const m = /^(\w+)(?:\(([^)]+)\))?(!)?:\s*(.+)$/.exec(subject);
    const title = m ? (TYPE_TITLES[m[1]!] ?? "Other") : "Other";
    const scope = m?.[2] ?? "";
    const desc = m?.[4] ?? subject;
    const entry = `- ${scope ? `**${scope}**: ` : ""}${desc} (${hash})`;
    (groups[title] ??= []).push(entry);
    if (m?.[3] === "!") breaking.push(entry);
  }

  let md = "";
  if (breaking.length)
    md += `### ⚠ BREAKING CHANGES\n${breaking.join("\n")}\n\n`;
  for (const t of ORDER)
    if (groups[t]?.length) md += `### ${t}\n${groups[t].join("\n")}\n\n`;
  if (previousTag) {
    const compare = `${REPO_URL}/compare/${previousTag}...${releaseTag(currentVersion())}`;
    md += `**Full Changelog**: ${compare}\n`;
  }
  return md.trim() || "_No notable changes._";
}

async function ghApi(
  url: string,
  token: string,
  body: unknown,
): Promise<{ ok: boolean; status: number; json: any }> {
  const r = await fetch(url, {
    method: "POST",
    headers: {
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
      accept: "application/vnd.github+json",
      "user-agent": "bnbagent-release",
    },
    body: JSON.stringify(body),
  });
  const json = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, json };
}

/**
 * Create the version-bump commit via the GitHub API so the commit is
 * **GitHub-signed (Verified)** — satisfies org "require signed commits" with no
 * GPG/SSH key. `createCommitOnBranch` pushes the (already-bumped) package.json
 * files onto the current branch. The workflow creates the tag only after every
 * npm package has been published.
 * File paths are made repo-root-relative via `git rev-parse --show-prefix`, so
 * this works whether the script runs from the repo root or a subdirectory
 * (this repo runs it from typescript/).
 * CI-only: needs GH_TOKEN/GITHUB_TOKEN + GITHUB_REPOSITORY + GITHUB_REF_NAME.
 */
async function commit(version: string): Promise<void> {
  if (!/^\d+\.\d+\.\d+$/.test(version))
    throw new Error(`commit: bad version "${version}"`);
  const token = process.env.GH_TOKEN ?? process.env.GITHUB_TOKEN;
  const repo = process.env.GITHUB_REPOSITORY; // owner/name
  const branch = process.env.GITHUB_REF_NAME; // the dispatch branch
  if (!token || !repo || !branch) {
    throw new Error(
      "commit: requires GH_TOKEN, GITHUB_REPOSITORY and GITHUB_REF_NAME (run in CI)",
    );
  }
  const headOid = sh(["git", "rev-parse", "HEAD"]);
  if (!headOid) throw new Error("commit: could not resolve HEAD oid");
  const prefix = sh(["git", "rev-parse", "--show-prefix"]); // "typescript/" or ""

  // Commit the package versions and matching lockfile atomically so a failed
  // release can rerun `pnpm install --frozen-lockfile` before resume.
  const localPaths = discoverPackages().map((p) => `${p.dir}/package.json`);
  if (existsSync(LOCKFILE)) localPaths.push(LOCKFILE);
  const additions = localPaths.map((path) => ({
    path: posix.normalize(`${prefix}${path}`),
    contents: Buffer.from(readFileSync(path)).toString("base64"),
  }));

  const mutation =
    "mutation($input: CreateCommitOnBranchInput!) { createCommitOnBranch(input: $input) { commit { oid } } }";
  const input = {
    branch: { repositoryNameWithOwner: repo, branchName: branch },
    message: { headline: `chore(release): v${version} [skip ci]` },
    fileChanges: { additions },
    expectedHeadOid: headOid,
  };
  const res = await ghApi("https://api.github.com/graphql", token, {
    query: mutation,
    variables: { input },
  });
  if (!res.ok || res.json.errors) {
    throw new Error(
      `createCommitOnBranch failed (HTTP ${res.status}): ${JSON.stringify(res.json.errors ?? res.json).slice(0, 500)}`,
    );
  }
  const oid = res.json?.data?.createCommitOnBranch?.commit?.oid as
    | string
    | undefined;
  if (!oid)
    throw new Error(
      `createCommitOnBranch: no commit oid: ${JSON.stringify(res.json).slice(0, 300)}`,
    );

  console.log(`verified release commit ${oid.slice(0, 9)} on ${branch}`);
}

const invokedDirectly =
  import.meta.url === pathToFileURL(process.argv[1] ?? "").href;

if (invokedDirectly) {
  const [sub, arg] = process.argv.slice(2);
  if (sub === "prepare") {
    const result = prepare(arg ?? "");
    process.stdout.write(
      `version=${result.version}\ncommit=${result.commit}\n`,
    );
  } else if (sub === "bump") process.stdout.write(`${bump(arg ?? "")}\n`);
  else if (sub === "commit") await commit(arg ?? "");
  else if (sub === "freeze") freeze();
  else if (sub === "pack") pack();
  else if (sub === "publish") {
    const args = process.argv.slice(3);
    const dryRun = args.includes("--dry-run");
    const tagIndex = args.indexOf("--tag");
    const tag = tagIndex >= 0 ? args[tagIndex + 1] : undefined;
    if (tagIndex >= 0 && !tag)
      throw new Error("publish: --tag requires a value");
    publish(dryRun, tag);
  } else if (sub === "changelog")
    process.stdout.write(`${changelog(arg)}\n`);
  else {
    console.error(
      "usage: release.ts <prepare current|patch|minor|major | bump patch|minor|major | commit <version> | freeze | pack | publish [--dry-run] [--tag <tag>] | changelog [range]>",
    );
    process.exit(1);
  }
}
