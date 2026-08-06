import { expect, test } from "vitest";
import {
  changelogLogArgs,
  freezeWorkspaceDeps,
  npmPublishArgs,
  publishOne,
  refreshLockfile,
  releaseRange,
  releaseTag,
  resolveVersion,
  waitForPackage,
} from "./release.ts";

const encoder = new TextEncoder();
const decoder = new TextDecoder();

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

test("release publishes production explicitly under latest", () => {
  expect(npmPublishArgs("latest")).toEqual([
    "npm",
    "publish",
    "--access",
    "public",
    "--tag",
    "latest",
  ]);
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
