import { defineConfig } from "tsup";

export default defineConfig({
  entry: [
    "src/index.ts",
    "src/erc8004/index.ts",
    "src/erc8183/index.ts",
    "src/x402/index.ts",
    "src/storage/index.ts",
    "src/wallets/index.ts",
    "src/signing/index.ts",
    "src/networks/index.ts",
    "src/utils/index.ts",
  ],
  format: ["esm", "cjs"],
  dts: true,
  clean: true,
});
