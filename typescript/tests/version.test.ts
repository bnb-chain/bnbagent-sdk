import { expect, test } from "vitest";
import packageJson from "../package.json" with { type: "json" };
import { getBuiltWithValue } from "../src/erc8004/constants.js";
import { SDK_VERSION } from "../src/version.js";

test("SDK metadata uses the package version", () => {
  expect(SDK_VERSION).toBe(packageJson.version);
  expect(getBuiltWithValue()).toBe(
    `https://github.com/bnb-chain/bnbagent-sdk#v${packageJson.version}`,
  );
});
