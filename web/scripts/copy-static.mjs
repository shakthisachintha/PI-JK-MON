// Copy the static export (out/) into the Python package so FastAPI serves it at /.
import { cpSync, existsSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, "../out");
const dest = resolve(here, "../../jkbms_pi/static");

if (!existsSync(resolve(src, "index.html"))) {
  console.error(`static export not found at ${src}`);
  process.exit(1);
}
rmSync(dest, { recursive: true, force: true });
cpSync(src, dest, { recursive: true });
console.log(`dashboard copied to ${dest}`);
