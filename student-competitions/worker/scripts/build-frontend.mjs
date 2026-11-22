/**
 * 构建脚本：把交付包的 frontend/ 三个页面原样注入 Worker 源码。
 *
 * 前端不做任何修改 —— 这里只是把 html/svg 读成字符串常量（生成 src/frontend.gen.js），
 * 由 Worker 按原路由（/、/merchant、/rag、/static/*）原样返回，media_type=text/html + no-store。
 */
import { readFileSync, writeFileSync, readdirSync, statSync } from "node:fs";
import { join, extname, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const FRONT =
  process.env.FRONTEND_DIR ||
  join(here, "..", "..", "lingtinghuixiang", "frontend");
const OUT = join(here, "..", "src", "frontend.gen.js");

function walk(dir, base = dir, acc = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, base, acc);
    else acc.push(relative(base, p).split("\\").join("/"));
  }
  return acc;
}

const files = walk(FRONT);
const parts = [];
parts.push("// 自动生成，请勿手改：来自交付包 frontend/（原样注入）");
parts.push("export const FRONTEND_DIR_NAME = " + JSON.stringify(FRONT) + ";");
for (const rel of files) {
  const buf = readFileSync(join(FRONT, rel));
  const isText = [".html", ".svg", ".js", ".css", ".json", ".txt"].includes(extname(rel).toLowerCase());
  const value = isText ? buf.toString("utf8") : buf.toString("base64");
  parts.push(`export const ${"F_" + rel.replace(/[^a-zA-Z0-9]/g, "_")} = ${JSON.stringify(value)};`);
}
parts.push("export const FILES = [");
for (const rel of files) {
  const isText = [".html", ".svg", ".js", ".css", ".json", ".txt"].includes(extname(rel).toLowerCase());
  parts.push(
    `  { path: ${JSON.stringify(rel)}, binary: ${!isText}, body: ${"F_" + rel.replace(/[^a-zA-Z0-9]/g, "_")}, size: ${readFileSync(join(FRONT, rel)).length} },`
  );
}
parts.push("];");
writeFileSync(OUT, parts.join("\n") + "\n", "utf8");
console.log(`[build-frontend] ${files.length} 个文件 → ${OUT}`);
for (const rel of files) console.log("  · " + rel + " (" + readFileSync(join(FRONT, rel)).length + "B)");
