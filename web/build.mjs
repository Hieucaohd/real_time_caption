import { build, context } from "esbuild";
import { mkdir, copyFile } from "node:fs/promises";
await mkdir("dist", { recursive: true });
await copyFile("index.html", "dist/index.html");
const options = {
  entryPoints: ["src/main.jsx"],
  bundle: true,
  outdir: "dist/assets",
  jsx: "automatic",
  minify: !process.argv.includes("--watch"),
  sourcemap: false,
  define: { "process.env.NODE_ENV": '"production"' },
  loader: { ".woff": "file", ".woff2": "file", ".ttf": "file" },
};
if (process.argv.includes("--watch")) {
  await (await context(options)).watch();
} else {
  await build(options);
}
