import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { gzipSync } from "node:zlib";
import { expect, test } from "vitest";
import {
  changelogLogArgs,
  computeTarballIntegrity,
  discoverPackages,
  freezeWorkspaceDeps,
  npmPublishArgs,
  publishOne,
  refreshLockfile,
  releaseRange,
  releaseTag,
  resolveVersion,
  verifyTarball,
  waitForPackage,
} from "./release.ts";

const encoder = new TextEncoder();
const decoder = new TextDecoder();
const publishWorkflow = readFileSync(
  new URL("../../.github/workflows/npm-publish.yml", import.meta.url),
  "utf8",
);
const productionJob = publishWorkflow.slice(
  publishWorkflow.indexOf("\n  production:"),
);

interface TestTarEntry {
  name: string;
  content: string;
  typeflag?: string;
  prefix?: string;
  magic?: "ustar" | "none";
}

function buildTarEntry({
  name,
  content,
  typeflag = "0",
  prefix,
  magic = "ustar",
}: TestTarEntry): Buffer {
  const data = Buffer.from(content, "utf8");
  const header = Buffer.alloc(512, 0);
  header.write(name, 0, "ascii");
  if (prefix) header.write(prefix, 345, "ascii");
  header.write("0000644\0", 100, "ascii");
  header.write("0000000\0", 108, "ascii");
  header.write("0000000\0", 116, "ascii");
  header.write(`${data.length.toString(8).padStart(11, "0")}\0`, 124, "ascii");
  header.write("00000000000\0", 136, "ascii");
  header.write(typeflag, 156, "ascii");
  if (magic === "ustar") {
    header.write("ustar\0", 257, "ascii");
    header.write("00", 263, "ascii");
  }
  header.fill(0x20, 148, 156);
  let sum = 0;
  for (const byte of header) sum += byte;
  header.write(`${sum.toString(8).padStart(6, "0")}\0 `, 148, "ascii");
  const pad = Buffer.alloc((512 - (data.length % 512)) % 512, 0);
  return Buffer.concat([header, data, pad]);
}

function buildTestTgz(entries: TestTarEntry[]): Buffer {
  const chunks = entries.map(buildTarEntry);
  chunks.push(Buffer.alloc(1024, 0));
  return gzipSync(Buffer.concat(chunks));
}

test("release lock refresh routes installer output away from machine stdout", () => {
  const reported: string[] = [];
  refreshLockfile(
    () => ({
      exitCode: 0,
      stdout: encoder.encode("Progress: resolved 1\nSaved pnpm-lock.yaml\n"),
      stderr: encoder.encode("warning from installer\n"),
    }),
    (chunk) => {
      if (chunk.byteLength > 0) reported.push(decoder.decode(chunk));
    },
  );

  expect(reported.join("")).toBe(
    "Progress: resolved 1\nSaved pnpm-lock.yaml\nwarning from installer\n",
  );
});

test("release freeze converts every workspace dependency form", () => {
  const pkg = {
    dependencies: {
      "@bnbagent/deploy-core": "workspace:*",
    },
    peerDependencies: {
      "@bnbagent/deploy-provider-aws": "workspace:^",
      external: "^1.0.0",
    },
  };

  freezeWorkspaceDeps(pkg, "0.3.1");

  expect(pkg).toEqual({
    dependencies: {
      "@bnbagent/deploy-core": "^0.3.1",
    },
    peerDependencies: {
      "@bnbagent/deploy-provider-aws": "^0.3.1",
      external: "^1.0.0",
    },
  });
});

test("release supports publishing the current stable version", () => {
  expect(resolveVersion("0.5.0", "current")).toBe("0.5.0");
  expect(resolveVersion("0.5.0", "patch")).toBe("0.5.1");
  expect(resolveVersion("0.5.0", "minor")).toBe("0.6.0");
  expect(resolveVersion("0.5.0", "major")).toBe("1.0.0");
});

test("release rejects prerelease manifests and unknown version selections", () => {
  expect(() => resolveVersion("0.5.0-alpha.1", "current")).toThrow(
    "expected X.Y.Z",
  );
  expect(() => resolveVersion("0.5.0", "alpha")).toThrow(
    "current|patch|minor|major",
  );
});

test("release tags carry the full npm coordinate", () => {
  expect(releaseTag("0.5.0")).toBe("@bnbagent/sdk@v0.5.0");
});

test("release changelog ranges from the previous sdk tag, never another component's", () => {
  expect(releaseRange("@bnbagent/sdk@v0.5.0")).toBe(
    "@bnbagent/sdk@v0.5.0..HEAD",
  );
  // First release: full history, NOT the latest Python (bnbagent-v*) tag —
  // an unrelated tag would silently truncate the range.
  expect(releaseRange("")).toBe("HEAD");
});

test("release changelog scopes to typescript/ and shared abis/ from the repo root", () => {
  const args = changelogLogArgs("@bnbagent/sdk@v0.5.0..HEAD");
  expect(args.slice(-3)).toEqual(["--", ":(top)typescript/", ":(top)abis/"]);
  expect(args).toContain("--no-merges");
  expect(args).toContain("@bnbagent/sdk@v0.5.0..HEAD");
});

test("release publishes production explicitly under latest with hardened flags", () => {
  expect(npmPublishArgs("latest", "/tmp/bnbagent-sdk-0.6.1.tgz")).toEqual([
    "npm",
    "publish",
    "/tmp/bnbagent-sdk-0.6.1.tgz",
    "--access",
    "public",
    "--ignore-scripts",
    "--registry=https://registry.npmjs.org",
    "--strict-ssl=true",
    "--tag",
    "latest",
  ]);
});

test("production release versions the package before building it", () => {
  const prepare = productionJob.indexOf("- name: Prepare or resume version");
  const build = productionJob.indexOf("- name: Check source and build");

  expect(prepare).toBeGreaterThan(-1);
  expect(build).toBeGreaterThan(prepare);
});

test("production release verifies built_with from the installable tarball", () => {
  expect(productionJob).toContain("npm pack --ignore-scripts --json");
  expect(productionJob).toContain("getBuiltWithValue()");
  expect(productionJob).toContain("TARBALL: ${{ steps.pack.outputs.tarball }}");
  expect(productionJob).toContain('publish --tag latest --tarball "$TARBALL"');
});

test("npm publish steps clear proxy settings in both cases", () => {
  const publishSteps = publishWorkflow
    .split("\n      - name: ")
    .filter((step) => /^Publish (under the alpha|stable package)/.test(step));

  expect(publishSteps).toHaveLength(2);
  for (const step of publishSteps) {
    for (const name of [
      "HTTP_PROXY",
      "HTTPS_PROXY",
      "ALL_PROXY",
      "http_proxy",
      "https_proxy",
      "all_proxy",
      "NPM_CONFIG_PROXY",
      "NPM_CONFIG_HTTPS_PROXY",
      "npm_config_proxy",
      "npm_config_https_proxy",
    ]) {
      expect(step).toContain(`-u ${name}`);
    }
  }
});

test("publish waits for npm visibility before continuing", () => {
  let checks = 0;
  const sleeps: number[] = [];
  waitForPackage("@bnbagent/example", "0.3.0", {
    attempts: 4,
    delayMs: 25,
    exists: () => ++checks === 3,
    sleep: (milliseconds) => sleeps.push(milliseconds),
    report: () => {},
  });

  expect(checks).toBe(3);
  expect(sleeps).toEqual([25, 25]);
});

test("publish waits only after npm publish succeeds", () => {
  const events: string[] = [];
  publishOne(
    {
      dir: "packages/example",
      name: "@bnbagent/example",
      pkg: { version: "0.3.1" },
    },
    false,
    {
      exists: () => false,
      run: () => {
        events.push("publish");
        return 0;
      },
      wait: (name, version) => events.push(`wait:${name}@${version}`),
    },
  );

  expect(events).toEqual(["publish", "wait:@bnbagent/example@0.3.1"]);
});

test("publish visibility timeout blocks dependents and tagging", () => {
  expect(() =>
    waitForPackage("@bnbagent/missing", "0.3.0", {
      attempts: 2,
      delayMs: 0,
      exists: () => false,
      sleep: () => {},
      report: () => {},
    }),
  ).toThrow("refusing to publish dependents or create the tag");
});

test("discoverPackages rejects a packages/ directory and symlinked package.json (SRC-2347)", () => {
  const dir = mkdtempSync(join(tmpdir(), "release-2347-"));
  try {
    writeFileSync(
      join(dir, "package.json"),
      JSON.stringify({ name: "@bnbagent/sdk", version: "0.6.1" }),
    );
    expect(discoverPackages(dir)).toHaveLength(1);

    mkdirSync(join(dir, "packages", "evil"), { recursive: true });
    writeFileSync(
      join(dir, "packages", "evil", "package.json"),
      JSON.stringify({ name: "@bnbagent/sdk", version: "0.6.1" }),
    );
    expect(() => discoverPackages(dir)).toThrow(
      "Unexpected packages/ directory",
    );

    rmSync(join(dir, "packages"), { recursive: true, force: true });
    rmSync(join(dir, "package.json"));
    writeFileSync(
      join(dir, "real.json"),
      JSON.stringify({ name: "@bnbagent/sdk", version: "0.6.1" }),
    );
    symlinkSync(join(dir, "real.json"), join(dir, "package.json"));
    expect(() => discoverPackages(dir)).toThrow(
      "Refusing non-regular or symlinked manifest",
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("verifyTarball rejects non-standard typeflags, duplicate manifests, and publishConfig (SRC-2388 & SRC-2391)", () => {
  const dir = mkdtempSync(join(tmpdir(), "release-tar-"));
  const repoUrl = "https://github.com/bnb-chain/bnbagent-sdk.git";
  try {
    const typeZPath = join(dir, "type-z.tgz");
    writeFileSync(
      typeZPath,
      buildTestTgz([
        {
          name: "package/package.json",
          content: JSON.stringify({
            name: "@bnbagent/sdk",
            version: "0.6.1",
            repository: { url: repoUrl },
          }),
          typeflag: "0",
        },
        {
          name: "package/package.json",
          content: JSON.stringify({
            name: "@bnbagent/sdk",
            version: "0.6.1-alpha.1",
            repository: { url: repoUrl },
          }),
          typeflag: "Z",
        },
      ]),
    );
    expect(() =>
      verifyTarball(typeZPath, {
        expectedVersion: "0.6.1-alpha.1",
        expectedRepositoryUrl: repoUrl,
      }),
    ).toThrow('Disallowed tar entry typeflag "Z"');

    const dupPath = join(dir, "dup.tgz");
    writeFileSync(
      dupPath,
      buildTestTgz([
        {
          name: "package/package.json",
          content: JSON.stringify({
            name: "@bnbagent/sdk",
            version: "0.6.1",
            repository: { url: repoUrl },
          }),
        },
        {
          name: "package/package.json",
          content: JSON.stringify({
            name: "@bnbagent/sdk",
            version: "0.6.1-alpha.1",
            repository: { url: repoUrl },
          }),
        },
      ]),
    );
    expect(() =>
      verifyTarball(dupPath, {
        expectedVersion: "0.6.1-alpha.1",
        expectedRepositoryUrl: repoUrl,
      }),
    ).toThrow("Duplicate tar entry path: package/package.json");

    const pubCfgPath = join(dir, "pubcfg.tgz");
    writeFileSync(
      pubCfgPath,
      buildTestTgz([
        {
          name: "package/package.json",
          content: JSON.stringify({
            name: "@bnbagent/sdk",
            version: "0.6.1-alpha.1",
            repository: { url: repoUrl },
            publishConfig: {
              "https-proxy": "http://127.0.0.1:8080/",
              "strict-ssl": false,
            },
          }),
        },
      ]),
    );
    expect(() =>
      verifyTarball(pubCfgPath, {
        expectedVersion: "0.6.1-alpha.1",
        expectedRepositoryUrl: repoUrl,
      }),
    ).toThrow("must not contain publishConfig");

    const validPath = join(dir, "valid.tgz");
    const repackPath = join(dir, "repacked.tgz");
    writeFileSync(
      validPath,
      buildTestTgz([
        {
          name: "package/package.json",
          content: JSON.stringify({
            name: "@bnbagent/sdk",
            version: "0.6.1",
            repository: { url: repoUrl },
            scripts: { postpack: "echo should-be-stripped" },
          }),
        },
        {
          name: "package/dist/index.js",
          content: "export const ok = true;\n",
        },
      ]),
    );
    const verified = verifyTarball(validPath, {
      expectedVersion: "0.6.1",
      expectedRepositoryUrl: repoUrl,
      repackPath,
    });
    expect(verified.integrity).toBe(
      computeTarballIntegrity(readFileSync(repackPath)),
    );
    const repackedCheck = verifyTarball(repackPath, {
      expectedVersion: "0.6.1",
      expectedRepositoryUrl: repoUrl,
    });
    expect(repackedCheck.manifest.scripts).toBeUndefined();
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("verifyTarball rejects headers whose path node-tar would resolve differently", () => {
  const dir = mkdtempSync(join(tmpdir(), "release-tar-"));
  const repoUrl = "https://github.com/bnb-chain/bnbagent-sdk.git";
  const manifest = (version: string, extra: object = {}) =>
    JSON.stringify({
      name: "@bnbagent/sdk",
      version,
      repository: { url: repoUrl },
      ...extra,
    });
  try {
    // node-tar ignores the prefix on a non-ustar header, so it reads the
    // second entry as package/package.json while a prefix-joining parser
    // reads the first one.
    const v7Path = join(dir, "v7-prefix.tgz");
    writeFileSync(
      v7Path,
      buildTestTgz([
        {
          name: "package.json",
          prefix: "package",
          magic: "none",
          content: manifest("0.6.1"),
        },
        {
          name: "package/package.json",
          prefix: "package/x",
          magic: "none",
          content: manifest("0.6.1-alpha.999", {
            publishConfig: { "https-proxy": "http://127.0.0.1:8080/" },
          }),
        },
      ]),
    );
    expect(() =>
      verifyTarball(v7Path, {
        expectedVersion: "0.6.1",
        expectedRepositoryUrl: repoUrl,
      }),
    ).toThrow("Tar entry header is not POSIX ustar");

    const prefixPath = join(dir, "ustar-prefix.tgz");
    writeFileSync(
      prefixPath,
      buildTestTgz([
        { name: "package.json", prefix: "package", content: manifest("0.6.1") },
      ]),
    );
    expect(() =>
      verifyTarball(prefixPath, {
        expectedVersion: "0.6.1",
        expectedRepositoryUrl: repoUrl,
      }),
    ).toThrow("Tar entry uses the ustar prefix field");
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("npm lanes publish the re-encoded archive, not raw npm pack output", () => {
  expect(productionJob).toContain('--repack "$CLEAN_TARBALL"');
  expect(productionJob).toContain(
    'npm install --ignore-scripts --no-audit --no-fund "$CLEAN_TARBALL"',
  );
  expect(productionJob).toContain('echo "tarball=$CLEAN_TARBALL"');

  const packageJob = publishWorkflow.slice(
    publishWorkflow.indexOf("\n  package:"),
    publishWorkflow.indexOf("\n  alpha-publish:"),
  );
  expect(packageJob).toContain('--repack "$CLEAN_TARBALL"');
  expect(packageJob).toContain("path: ${{ runner.temp }}/npm-clean/*.tgz");

  const alphaPublishJob = publishWorkflow.slice(
    publishWorkflow.indexOf("\n  alpha-publish:"),
    publishWorkflow.indexOf("\n  production:"),
  );
  expect(alphaPublishJob).toContain("Tar entry header is not POSIX ustar");
  expect(alphaPublishJob).toContain("Tar entry uses the ustar prefix field");
});

test("release workflows run no credentialed git commands after checkout", () => {
  const pypiWorkflow = readFileSync(
    new URL("../../.github/workflows/pypi-release.yaml", import.meta.url),
    "utf8",
  );
  for (const workflow of [publishWorkflow, pypiWorkflow]) {
    const commands = workflow
      .split("\n")
      .filter((line) => !line.trimStart().startsWith("#"))
      .join("\n");
    expect(commands).not.toMatch(/\bgit (fetch|push|pull|ls-remote)\b/);
  }
  expect(pypiWorkflow).toContain('gh release create "$TARGET_TAG"');
});

test("PyPI smoke installs locked runtime deps and imports outside the source tree", () => {
  const pypiWorkflow = readFileSync(
    new URL("../../.github/workflows/pypi-release.yaml", import.meta.url),
    "utf8",
  );
  expect(pypiWorkflow).toContain(
    "uv export --frozen --no-dev --no-emit-project",
  );
  expect(pypiWorkflow).toContain("--require-hashes");
  expect(pypiWorkflow).toContain('cd "$SMOKE_DIR"');
  expect(pypiWorkflow).toContain('"$SMOKE_VENV/bin/python" -I -c');
  expect(pypiWorkflow).toContain('"$PWD/dist/bnbagent-${VERSION}.tar.gz"');
});

test("publishOne refuses to skip an existing version when registry integrity mismatches (SRC-2391)", () => {
  expect(() =>
    publishOne(
      {
        dir: ".",
        name: "@bnbagent/sdk",
        pkg: { version: "0.6.1" },
      },
      false,
      {
        exists: () => true,
        localIntegrity: "sha512-localCleanDigest==",
        remoteIntegrity: () => "sha512-attackerPreclaimedDigest==",
        report: () => {},
      },
    ),
  ).toThrow("Refusing to skip @bnbagent/sdk@0.6.1: registry integrity");

  const reports: string[] = [];
  publishOne(
    {
      dir: ".",
      name: "@bnbagent/sdk",
      pkg: { version: "0.6.1" },
    },
    false,
    {
      exists: () => true,
      localIntegrity: "sha512-matchingDigest==",
      remoteIntegrity: () => "sha512-matchingDigest==",
      report: (m) => reports.push(m),
    },
  );
  expect(reports).toEqual([
    "skip @bnbagent/sdk@0.6.1 (already published with matching integrity)",
  ]);
});
