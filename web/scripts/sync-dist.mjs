import { cp, rm, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(__dirname, "..");
const out = path.join(webRoot, "out");
const target = path.resolve(webRoot, "../src/autoteam/web/dist");

try {
  await stat(out);
} catch {
  console.error(`Next.js export dir not found: ${out}`);
  process.exit(1);
}

await rm(target, { recursive: true, force: true });
await cp(out, target, { recursive: true });
console.log(`Synced Next.js export → ${target}`);
