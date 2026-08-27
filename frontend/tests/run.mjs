// Bundles the JSX tests with the project's own esbuild and runs them in Node.
// There is no browser here, so the components are rendered with
// react-dom/server — enough to assert on the markup the user actually sees.
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const esbuild = join(root, 'node_modules', '.bin', 'esbuild');
if (!existsSync(esbuild)) {
  console.error('esbuild not found — run `npm install` in frontend/ first.');
  process.exit(1);
}

const outDir = join(root, 'node_modules', '.cache', 'tests');
rmSync(outDir, { recursive: true, force: true });
mkdirSync(outDir, { recursive: true });

const targets = process.argv.slice(2).map((f) => (f.endsWith('.jsx') ? f : `${f}.jsx`));
const files = targets.length ? targets : ['trade_log_ui.jsx'];

let failed = 0;
for (const file of files) {
  const entry = join(here, file);
  const out = join(outDir, file.replace(/\.jsx$/, '.cjs'));
  console.log(`\n=== ${file} ===`);
  try {
    execFileSync(esbuild, [
      entry, '--bundle', '--platform=node', '--format=cjs', `--outfile=${out}`,
      '--define:import.meta.env.VITE_API_URL=""',
      '--loader:.js=jsx', '--jsx=automatic', '--log-level=error',
    ], { cwd: root, stdio: 'inherit' });
  } catch {
    console.error(`bundle failed for ${file}`);
    failed = 1;
    continue;
  }
  try {
    execFileSync(process.execPath, [out], { stdio: 'inherit' });
  } catch {
    failed = 1;
  }
}
process.exit(failed);
