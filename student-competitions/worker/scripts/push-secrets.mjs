import { readFileSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const envPath = join(here, "..", "..", "lingtinghuixiang", "backend", ".env");

const WANT = [
  "ZHIPU_API_KEY",
  "VOLCANO_APP_KEY",
  "VOLCANO_TTS_API_KEY",
  "ENGINEER_API_KEY",
  "ZHIPU_MODEL",
  "VOLCANO_RESOURCE_ID",
  "VOLCANO_TTS_RESOURCE_ID",
  "VOLCANO_TTS_SPEAKER",
  "ENGINEER_BASE_URL",
  "ENGINEER_MODEL",
  "TTS_BACKEND",
];

const raw = readFileSync(envPath, "utf8");
const out = {};
for (const line of raw.split(/\r?\n/)) {
  const m = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
  if (!m) continue;
  let v = m[2].trim();
  if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1);
  out[m[1]] = v;
}

const picked = {};
for (const k of WANT) {
  if (out[k]) picked[k] = out[k];
  else if (process.env[k]) picked[k] = process.env[k];
}
if (process.env.VOLCANO_TTS_SPEAKER) picked.VOLCANO_TTS_SPEAKER = process.env.VOLCANO_TTS_SPEAKER;

// 只写非空、且不打印明文
const target = process.argv[2];
if (target) writeFileSync(target, JSON.stringify(picked, null, 2), "utf8");
console.log(JSON.stringify({ keys: Object.keys(picked), lens: Object.fromEntries(Object.entries(picked).map(([k, v]) => [k, v.length])) }, null, 2));
