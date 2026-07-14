import { expect, test } from "vitest";
import {
  freezeWorkspaceDeps,
  publishOne,
  refreshLockfile,
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
