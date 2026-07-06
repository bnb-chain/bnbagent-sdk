import { readdirSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { join, basename, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

const ABIS_DIR = join(__dirname, "..", "..", "abis");
const OUT_DIR = join(__dirname, "..", "src", "abis");
mkdirSync(OUT_DIR, { recursive: true });

// lowerCamelCase a PascalCase basename, collapsing a leading acronym run
// (e.g. "ERC20" -> "erc20", "AgenticCommerce" -> "agenticCommerce") so
// identifiers/filenames stay lowerCamelCase even for all-caps acronyms.
function toCamel(base) {
  const acronym = base.match(/^[A-Z]+/)?.[0] ?? "";
  if (acronym.length > 1 && acronym.length < base.length) {
    return acronym.toLowerCase() + base.slice(acronym.length);
  }
  return base[0].toLowerCase() + base.slice(1);
}

const names = [];
for (const file of readdirSync(ABIS_DIR).filter((f) => f.endsWith(".json")).sort()) {
  const raw = JSON.parse(readFileSync(join(ABIS_DIR, file), "utf8"));
  const abi = Array.isArray(raw) ? raw : raw.abi; // tolerate {abi: [...]} artifacts
  const base = basename(file, ".json");
  const mod = toCamel(base);
  const ident = mod + "Abi";
  const out = `// GENERATED from abis/${file} — do not edit. Run: pnpm codegen\nexport const ${ident} = ${JSON.stringify(abi, null, 2)} as const;\n`;
  writeFileSync(join(OUT_DIR, `${mod}.ts`), out);
  names.push({ ident, mod });
}
writeFileSync(join(OUT_DIR, "index.ts"),
  names.map((n) => `export { ${n.ident} } from "./${n.mod}.js";`).join("\n") + "\n");
