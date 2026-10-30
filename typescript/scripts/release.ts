/**
 * Release helper (no external deps; run via tsx/node in CI).
 *
 * This repository ships a SINGLE package (@bnbagent/sdk) rooted at
 * RELEASE_PACKAGE_DIR (default ".", i.e. typescript/). A `packages/`
 * directory or symlinked `package.json` is rejected outright so package
 * discovery can never diverge from the root tarball verified in CI.
 *
 *   prepare <current|patch|minor|major>
 *                              publish the current version, resume an untagged
 *                              release commit, or bump the package.
 *   freeze                     rewrite internal `workspace:*` deps to `^<version>`
 *                              for publishing — run AFTER the version commit, NOT committed
 *                              (committing it would break local workspace linking).
 *   pack                       run npm's package-content smoke check.
 *   verify-tarball <tgz> <expectedVersion> <expectedRepoUrl> [--repack <outTgz>]
 *                              validate tar headers, entry paths, and the single
 *                              package/package.json manifest; optionally repack a
 *                              clean archive and print its sha512 integrity.
 *   publish [--dry-run] [--tag <tag>] [--tarball <tgz>]
 *                              publish the verified tarball (or package), verifying
 *                              registry sha512 integrity before skipping an existing version.
 *   changelog [<range>]        group commits in <range> (default: last tag..HEAD) by
 *                              Conventional-Commit type into Markdown for the GitHub Release.
 */
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  existsSync,
  lstatSync,
  readFileSync,
  type Stats,
  writeFileSync,
} from "node:fs";
import * as posix from "node:path/posix";
import { pathToFileURL } from "node:url";
import { gunzipSync, gzipSync } from "node:zlib";

const PACKAGES_DIR = "packages";
/**
 * Git tags for this component are the full npm coordinate
 * (`@bnbagent/sdk@vX.Y.Z`) so they never collide with the Python component's
 * `bnbagent-vX.Y.Z` line on the shared GitHub Releases page.
 */
export const RELEASE_TAG_PREFIX = "@bnbagent/sdk@v";
export const NPM_REGISTRY_URL = "https://registry.npmjs.org";
const EXPECTED_PACKAGE_NAME = "@bnbagent/sdk";
const REPO_URL = `https://github.com/${process.env.GITHUB_REPOSITORY ?? "bnb-chain/bnbagent-sdk"}`;

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

export interface Pkg {
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
 * Discover the single publishable package rooted at `rootDir` (defaults to
 * `SINGLE_PACKAGE_DIR`). Rejects any `packages/` directory or symlinked
 * `package.json` so discovery cannot be redirected to an unverified subpackage.
 */
export function discoverPackages(rootDir: string = SINGLE_PACKAGE_DIR): Pkg[] {
  const packagesPath =
    rootDir === "." ? PACKAGES_DIR : `${rootDir}/${PACKAGES_DIR}`;
  if (existsSync(packagesPath)) {
    throw new Error(
      `Unexpected ${PACKAGES_DIR}/ directory in single-package repository`,
    );
  }

  const path = `${rootDir}/package.json`;
  let stat: Stats;
  try {
    stat = lstatSync(path);
  } catch {
    throw new Error(
      `No package.json at "${rootDir}" (set RELEASE_PACKAGE_DIR to the package directory)`,
    );
  }
  if (stat.isSymbolicLink() || !stat.isFile()) {
    throw new Error(`Refusing non-regular or symlinked manifest at "${path}"`);
  }
  const pkg = JSON.parse(readFileSync(path, "utf8")) as Record<string, any>;
  if (pkg.private === true || !pkg.name)
    throw new Error(`Root package at "${rootDir}" is private/unnamed`);
  return [{ dir: rootDir, name: pkg.name, pkg }];
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
    throw new Error(
      `Could not refresh ${LOCKFILE} (exit ${result.exitCode ?? 1})`,
    );
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

export function computeTarballIntegrity(tarballBytes: Buffer): string {
  return `sha512-${createHash("sha512").update(tarballBytes).digest("base64")}`;
}

function readNullTerminatedAscii(buf: Buffer): string {
  const zero = buf.indexOf(0);
  return (zero >= 0 ? buf.subarray(0, zero) : buf).toString("utf8");
}

function parseOctalField(buf: Buffer, fieldName: string): number {
  const raw = readNullTerminatedAscii(buf).trim();
  if (!raw) return 0;
  if (!/^[0-7]+$/.test(raw)) {
    throw new Error(
      `Invalid octal tar header field ${fieldName}: ${JSON.stringify(raw)}`,
    );
  }
  return Number.parseInt(raw, 8);
}

function isZeroBlock(block: Buffer): boolean {
  for (let i = 0; i < block.length; i++) {
    if (block[i] !== 0) return false;
  }
  return true;
}

function encodeUstarHeader(name: string, mode: number, size: number): Buffer {
  const nameBytes = Buffer.from(name, "utf8");
  if (nameBytes.length === 0 || nameBytes.length > 100) {
    throw new Error(`Tar entry path length out of range (1..100): ${name}`);
  }
  const header = Buffer.alloc(512, 0);
  nameBytes.copy(header, 0);
  header.write(
    `${(mode & 0o7777).toString(8).padStart(7, "0")}\0`,
    100,
    "ascii",
  );
  header.write("0000000\0", 108, "ascii");
  header.write("0000000\0", 116, "ascii");
  header.write(`${size.toString(8).padStart(11, "0")}\0`, 124, "ascii");
  header.write("00000000000\0", 136, "ascii");
  header.write("0", 156, "ascii");
  header.write("ustar\0", 257, "ascii");
  header.write("00", 263, "ascii");
  header.fill(0x20, 148, 156);
  let sum = 0;
  for (const byte of header) sum += byte;
  header.write(`${sum.toString(8).padStart(6, "0")}\0 `, 148, "ascii");
  return header;
}

export interface VerifyTarballOptions {
  expectedName?: string;
  expectedVersion: string;
  expectedRepositoryUrl: string;
  repackPath?: string;
}

export interface VerifiedTarballResult {
  integrity: string;
  manifest: Record<string, unknown>;
  entries: string[];
}

/**
 * Strictly parse and validate a `.tgz` package archive in-memory:
 * - Every tar header must be a regular file (`typeflag === "0"` or `"\0"`) or
 *   directory (`"5"`). Non-standard types (`"Z"`), links (`"1"`/`"2"`), and
 *   PAX headers (`"x"`/`"g"`) are rejected so GNU tar and node-tar cannot
 *   disagree on which entry is `package/package.json`.
 * - Every path must normalize under `package/` with no `..`, leading `/`, or
 *   duplicate normalized names.
 * - `package/package.json` must appear once, match name/version/repository.url,
 *   and must not contain `publishConfig`.
 * - When `repackPath` is supplied, writes a deterministic `.tgz` containing
 *   only the validated regular files (with `publishConfig` and `scripts`
 *   removed from `package/package.json`) and returns the new archive's sha512.
 */
export function verifyTarball(
  tarballPath: string,
  options: VerifyTarballOptions,
): VerifiedTarballResult {
  const expectedName = options.expectedName ?? EXPECTED_PACKAGE_NAME;
  const rawTgz = readFileSync(tarballPath);
  const tarBuf = gunzipSync(rawTgz);
  if (tarBuf.length === 0 || tarBuf.length % 512 !== 0) {
    throw new Error(`Invalid tar archive length (${tarBuf.length} bytes)`);
  }

  const seenPaths = new Set<string>();
  const fileEntries: Array<{ path: string; mode: number; data: Buffer }> = [];
  let manifestObj: Record<string, unknown> | null = null;
  let offset = 0;
  let sawEndOfArchive = false;

  while (offset < tarBuf.length) {
    const header = tarBuf.subarray(offset, offset + 512);
    if (isZeroBlock(header)) {
      sawEndOfArchive = true;
      offset += 512;
      continue;
    }
    if (sawEndOfArchive) {
      throw new Error("Non-zero tar block found after end-of-archive marker");
    }

    const storedChecksum = parseOctalField(
      header.subarray(148, 156),
      "checksum",
    );
    let computedChecksum = 0;
    for (let i = 0; i < 512; i++) {
      computedChecksum += i >= 148 && i < 156 ? 0x20 : (header[i] ?? 0);
    }
    if (storedChecksum !== computedChecksum) {
      throw new Error("Tar header checksum mismatch");
    }

    const typeByte = header[156] ?? 0;
    const typeflag = typeByte === 0 ? "\0" : String.fromCharCode(typeByte);
    if (typeflag !== "0" && typeflag !== "\0" && typeflag !== "5") {
      throw new Error(
        `Disallowed tar entry typeflag ${JSON.stringify(typeflag)} (only regular files and directories are permitted)`,
      );
    }

    const namePart = readNullTerminatedAscii(header.subarray(0, 100));
    const prefixPart = readNullTerminatedAscii(header.subarray(345, 500));
    const rawPath = prefixPart ? `${prefixPart}/${namePart}` : namePart;
    if (!rawPath || rawPath.startsWith("/") || rawPath.includes("\0")) {
      throw new Error(`Invalid tar entry path: ${JSON.stringify(rawPath)}`);
    }
    const segments = rawPath.split("/");
    if (segments.some((s) => s === ".." || s === ".")) {
      throw new Error(
        `Unsafe tar entry path segments in ${JSON.stringify(rawPath)}`,
      );
    }
    const normalized = posix.normalize(rawPath).replace(/\/+$/, "");
    if (normalized !== "package" && !normalized.startsWith("package/")) {
      throw new Error(
        `Tar entry must reside under package/, got ${JSON.stringify(normalized)}`,
      );
    }

    const size = parseOctalField(header.subarray(124, 136), "size");
    const mode = parseOctalField(header.subarray(100, 108), "mode") || 0o644;
    if (typeflag === "5" && size !== 0) {
      throw new Error(
        `Directory entry ${normalized} has non-zero size ${size}`,
      );
    }

    const dataStart = offset + 512;
    const dataEnd = dataStart + size;
    const paddedEnd = dataStart + Math.ceil(size / 512) * 512;
    if (paddedEnd > tarBuf.length) {
      throw new Error(`Tar entry ${normalized} extends past end of archive`);
    }

    if (seenPaths.has(normalized)) {
      throw new Error(`Duplicate tar entry path: ${normalized}`);
    }
    seenPaths.add(normalized);

    if (typeflag !== "5") {
      const content = Buffer.from(tarBuf.subarray(dataStart, dataEnd));
      if (normalized === "package/package.json") {
        let parsed: unknown;
        try {
          parsed = JSON.parse(content.toString("utf8"));
        } catch (err) {
          throw new Error(
            `Malformed JSON in package/package.json: ${(err as Error).message}`,
          );
        }
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          throw new Error("package/package.json must be a JSON object");
        }
        manifestObj = parsed as Record<string, unknown>;
      }
      fileEntries.push({ path: normalized, mode, data: content });
    }

    offset = paddedEnd;
  }

  if (!manifestObj) {
    throw new Error("Tarball is missing package/package.json");
  }
  if (manifestObj.name !== expectedName) {
    throw new Error(
      `Unexpected package name in tarball: expected ${JSON.stringify(expectedName)}, got ${JSON.stringify(manifestObj.name)}`,
    );
  }
  if (manifestObj.version !== options.expectedVersion) {
    throw new Error(
      `Unexpected package version in tarball: expected ${JSON.stringify(options.expectedVersion)}, got ${JSON.stringify(manifestObj.version)}`,
    );
  }
  const repositoryField = manifestObj.repository as
    | { url?: unknown }
    | undefined;
  const repoUrl = repositoryField?.url;
  if (repoUrl !== options.expectedRepositoryUrl) {
    throw new Error(
      `Unexpected repository.url in tarball: expected ${JSON.stringify(options.expectedRepositoryUrl)}, got ${JSON.stringify(repoUrl)}`,
    );
  }
  if (Object.hasOwn(manifestObj, "publishConfig")) {
    throw new Error("Tarball package.json must not contain publishConfig");
  }

  let finalTgz = rawTgz;
  if (options.repackPath) {
    const {
      publishConfig: _droppedPublishConfig,
      scripts: _droppedScripts,
      ...cleanManifest
    } = manifestObj;
    const cleanManifestBuf = Buffer.from(
      `${JSON.stringify(cleanManifest, null, 2)}\n`,
      "utf8",
    );

    const chunks: Buffer[] = [];
    for (const entry of fileEntries) {
      const data =
        entry.path === "package/package.json" ? cleanManifestBuf : entry.data;
      chunks.push(encodeUstarHeader(entry.path, entry.mode, data.length));
      chunks.push(data);
      const pad = (512 - (data.length % 512)) % 512;
      if (pad > 0) chunks.push(Buffer.alloc(pad, 0));
    }
    chunks.push(Buffer.alloc(1024, 0));
    finalTgz = gzipSync(Buffer.concat(chunks));
    writeFileSync(options.repackPath, finalTgz);
  }

  return {
    integrity: computeTarballIntegrity(finalTgz),
    manifest: manifestObj,
    entries: fileEntries.map((e) => e.path),
  };
}

function packageExists(name: string, version: string): boolean {
  const r = runCmd([
    "npm",
    "view",
    `${name}@${version}`,
    "version",
    "--registry",
    NPM_REGISTRY_URL,
    "--json",
  ]);
  if (r.status !== 0) return false;
  const output = r.stdout.toString().trim();
  try {
    return JSON.parse(output) === version;
  } catch {
    return output.replace(/^"|"$/g, "") === version;
  }
}

export function fetchRemoteIntegrity(
  name: string,
  version: string,
): string | undefined {
  const r = runCmd([
    "npm",
    "view",
    `${name}@${version}`,
    "dist.integrity",
    "--registry",
    NPM_REGISTRY_URL,
    "--json",
  ]);
  if (r.status !== 0) return undefined;
  const output = r.stdout.toString().trim();
  if (!output) return undefined;
  try {
    const parsed = JSON.parse(output);
    return typeof parsed === "string" && parsed ? parsed : undefined;
  } catch {
    const trimmed = output.replace(/^"|"$/g, "");
    return trimmed || undefined;
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
    const r = runCmd(
      ["npm", "pack", "--dry-run", "--ignore-scripts", "--json"],
      {
        cwd: p.dir,
        inherit: true,
      },
    );
    if (r.status !== 0) process.exit(r.status ?? 1);
  }
}

export interface PublishOneOptions {
  exists?: (name: string, version: string) => boolean;
  run?: (pkg: Pkg) => number | null;
  tag?: string;
  tarball?: string;
  localIntegrity?: string;
  remoteIntegrity?: (name: string, version: string) => string | undefined;
  wait?: (name: string, version: string) => void;
  report?: (message: string) => void;
}

export function npmPublishArgs(tag?: string, tarballPath?: string): string[] {
  const args = ["npm", "publish"];
  if (tarballPath) args.push(tarballPath);
  args.push(
    "--access",
    "public",
    "--ignore-scripts",
    `--registry=${NPM_REGISTRY_URL}`,
    "--strict-ssl=true",
  );
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
      runCmd(npmPublishArgs(options.tag, options.tarball), {
        cwd: pkg.dir,
        inherit: true,
      }).status);
  const wait = options.wait ?? waitForPackage;
  const report = options.report ?? console.log;
  const getRemoteIntegrity = options.remoteIntegrity ?? fetchRemoteIntegrity;
  const version = String(p.pkg.version);
  const localIntegrity =
    options.localIntegrity ??
    (options.tarball
      ? computeTarballIntegrity(readFileSync(options.tarball))
      : undefined);

  if (dryRun) {
    report(`would publish ${p.name}@${version} (${options.tarball ?? p.dir})`);
    return;
  }
  if (exists(p.name, version)) {
    if (localIntegrity) {
      const remote = getRemoteIntegrity(p.name, version);
      if (!remote || remote !== localIntegrity) {
        throw new Error(
          `Refusing to skip ${p.name}@${version}: registry integrity ${JSON.stringify(remote)} does not match local tarball ${JSON.stringify(localIntegrity)}`,
        );
      }
    }
    report(
      `skip ${p.name}@${version} (already published with matching integrity)`,
    );
    return;
  }
  const exitCode = run(p);
  if (exitCode !== 0) process.exit(exitCode ?? 1);
  wait(p.name, version);
}

function publish(dryRun: boolean, tag?: string, tarball?: string): void {
  const pkgs = publishOrder(discoverPackages());
  if (tarball && pkgs.length !== 1) {
    throw new Error(
      "--tarball is only supported for single-package repositories",
    );
  }
  for (const p of pkgs) {
    if (tarball) {
      verifyTarball(tarball, {
        expectedName: p.name,
        expectedVersion: String(p.pkg.version),
        expectedRepositoryUrl: `${REPO_URL}.git`,
      });
    }
    publishOne(p, dryRun, { tag, tarball });
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
  else if (sub === "verify-tarball") {
    const [tarballPath, expectedVersion, expectedRepositoryUrl, ...rest] =
      process.argv.slice(3);
    if (!tarballPath || !expectedVersion || !expectedRepositoryUrl) {
      throw new Error(
        "verify-tarball requires <tarballPath> <expectedVersion> <expectedRepositoryUrl> [--repack <outPath>]",
      );
    }
    const repackIdx = rest.indexOf("--repack");
    const repackPath = repackIdx >= 0 ? rest[repackIdx + 1] : undefined;
    if (repackIdx >= 0 && !repackPath) {
      throw new Error("verify-tarball: --repack requires an output path");
    }
    const result = verifyTarball(tarballPath, {
      expectedVersion,
      expectedRepositoryUrl,
      repackPath,
    });
    process.stdout.write(`integrity=${result.integrity}\n`);
  } else if (sub === "publish") {
    const args = process.argv.slice(3);
    const dryRun = args.includes("--dry-run");
    const tagIndex = args.indexOf("--tag");
    const tag = tagIndex >= 0 ? args[tagIndex + 1] : undefined;
    if (tagIndex >= 0 && !tag)
      throw new Error("publish: --tag requires a value");
    const tarballIndex = args.indexOf("--tarball");
    const tarball = tarballIndex >= 0 ? args[tarballIndex + 1] : undefined;
    if (tarballIndex >= 0 && !tarball)
      throw new Error("publish: --tarball requires a value");
    publish(dryRun, tag, tarball);
  } else if (sub === "changelog") process.stdout.write(`${changelog(arg)}\n`);
  else {
    console.error(
      "usage: release.ts <prepare current|patch|minor|major | bump patch|minor|major | commit <version> | freeze | pack | verify-tarball <tgz> <version> <repoUrl> [--repack <outTgz>] | publish [--dry-run] [--tag <tag>] [--tarball <tgz>] | changelog [range]>",
    );
    process.exit(1);
  }
}
