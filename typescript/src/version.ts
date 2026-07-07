/**
 * SDK version, used to build the `built_with` ERC-8004 metadata tag
 * (`https://github.com/bnb-chain/bnbagent-sdk#v<version>`).
 *
 * Hardcoded rather than read from `package.json` at runtime — this file is
 * bundled by `tsup` into both ESM and CJS outputs, and importing JSON keeps
 * the value in lockstep with `package.json` without adding a build step.
 * Keep this in sync with `package.json`'s `version` field.
 */
export const SDK_VERSION = "0.1.0";
