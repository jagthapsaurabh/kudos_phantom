// Static guard: every capitalized identifier used as a JSX component must be
// either imported from a module, exported from 'react', defined locally in the
// file, or a JS/DOM global. Catches production-only crashes like
// "ReferenceError: ShieldCheck is not defined" (Vite does not fail the build
// for undefined identifiers — esbuild strips them silently).
import fs from 'node:fs';
import path from 'node:path';

const root = path.resolve(process.argv[2] || 'src');
const files = [];
(function walk(dir) {
  for (const entry of fs.readdirSync(dir)) {
    const p = path.join(dir, entry);
    if (fs.statSync(p).isDirectory()) walk(p);
    else if (/\.[jj]sx$/.test(entry)) files.push(p);
  }
})(root);

const REACT_EXPORTS = new Set([
  'React', 'Fragment', 'Profiler', 'StrictMode', 'Suspense', 'Children',
  'Component', 'PureComponent', 'memo', 'forwardRef', 'lazy', 'cloneElement',
  'createContext', 'createElement', 'createRef', 'isValidElement',
  'useState', 'useEffect', 'useLayoutEffect', 'useInsertionEffect',
  'useMemo', 'useCallback', 'useReducer', 'useRef', 'useContext',
  'useTransition', 'useDeferredValue', 'useId', 'useSyncExternalStore',
  'useImperativeHandle', 'useDebugValue', 'useOptimistic', 'useActionState',
  'useFormStatus', 'use', 'cache', 'experimental_',
]);
const GLOBALS = new Set([
  'window', 'document', 'localStorage', 'sessionStorage', 'fetch', 'console',
  'JSON', 'Math', 'Number', 'String', 'Boolean', 'Date', 'Object', 'Array',
  'isNaN', 'isFinite', 'parseFloat', 'parseInt', 'setTimeout', 'setInterval',
  'clearInterval', 'clearTimeout', 'queueMicrotask', 'requestAnimationFrame',
  'Promise', 'Error', 'TypeError', 'RangeError', 'Map', 'Set', 'WeakMap',
  'WeakSet', 'URL', 'URLSearchParams', 'encodeURIComponent',
  'decodeURIComponent', 'Intl', 'navigator', 'alert', 'confirm', 'location',
  'history', 'Blob', 'File', 'FileReader', 'FormData', 'Symbol', 'BigInt',
  'RegExp', 'Infinity', 'NaN', 'undefined', 'globalThis', 'process',
  'arguments', 'AbortController', 'Event', 'CustomEvent', 'WebSocket',
]);

const importRe = /import\s+([^;]*?)\s+from\s*['"][^'"]+['"]/g;
const namedRe = /([A-Za-z_$][\w$]*)\s*(?:as\s+([A-Za-z_$][\w$]*))?/g;

let problems = 0;
for (const file of files) {
  const src = fs.readFileSync(file, 'utf8');

  // 1. Identifiers available in module scope.
  const imported = new Set();
  for (const m of src.matchAll(importRe)) {
    const clause = m[1];
    // default import: `import X from` / `import X, { a } from`
    const defaultPart = clause.replace(/\{[^}]*\}/g, '').replace(/\*\s+as\s+[A-Za-z_$][\w$]*/g, '');
    for (const tok of defaultPart.split(',')) {
      const n = tok.trim();
      if (/^[A-Za-z_$][\w$]*$/.test(n)) imported.add(n);
    }
    // named imports (incl. `x as y`) and namespace imports
    for (const nm of clause.matchAll(/\{([^}]*)\}/g)) {
      for (const part of nm[1].split(',')) {
        const bits = part.trim().match(/^([A-Za-z_$][\w$]*)\s*(?:as\s+([A-Za-z_$][\w$]*))?$/);
        if (bits) imported.add(bits[2] || bits[1]);
      }
    }
    for (const ns of clause.matchAll(/\*\s+as\s+([A-Za-z_$][\w$]*)/g)) imported.add(ns[1]);
  }

  // 2. Locally defined: function/const/let/var/class declarations (top level
  //    or nested — conservative, a false "defined" is impossible because we
  //    only check that a declaration with that name exists somewhere).
  const defined = new Set();
  for (const m of src.matchAll(/\b(?:function|class|const|let|var)\s+([A-Za-z_$][\w$]*)/g)) {
    defined.add(m[1]);
  }
  // destructured consts like `const { a, b: c } = ...`
  for (const m of src.matchAll(/\b(?:const|let|var)\s*\{([^}]*)\}\s*=/g)) {
    for (const part of m[1].split(',')) {
      const bits = part.trim().match(/^([A-Za-z_$][\w$]*)\s*(?::\s*([A-Za-z_$][\w$]*))?$/);
      if (bits) defined.add(bits[2] || bits[1]);
    }
  }
  // function parameters used as components (<Icon .../> render props)
  const params = new Set();
  for (const m of src.matchAll(/\(\s*\{([^}]*)\}\s*\)/g)) {
    for (const part of m[1].split(',')) {
      const bits = part.trim().split('=')[0].trim().split(/\s*:\s*/).pop();
      if (/^[A-Za-z_$][\w$]*$/.test(bits)) params.add(bits);
    }
  }
  for (const m of src.matchAll(/\b([A-Za-z_$][\w$]*)\s*:/g)) params.add(m[1]); // object keys can shadow in JSX? no — skip
  const arrowArgs = new Set();
  // Trailing delimiter is a LOOKAHEAD: consuming it would swallow the comma
  // that leads the next identifier, so `[k, label, Icon] =>` used to capture
  // only every second element and flag real components (e.g. a destructured
  // map variable used as <Icon/>) as missing. `[` is a leading delimiter too
  // — the first element of an array destructure follows it, not `(`/`,`.
  for (const m of src.matchAll(/(?:\(|,|\[)\s*([A-Za-z_$][\w$]*)\s*(?=,|\]|=>|\))/g)) arrowArgs.add(m[1]);

  // 3. Identifiers used as JSX components or referenced inside <>{expr}</>.
  const used = new Set();
  for (const m of src.matchAll(/<([A-Z][\w$]*)[\s/>]/g)) used.add(m[1]);
  for (const m of src.matchAll(/<\/([A-Z][\w$]*)>/g)) used.add(m[1]);

  for (const name of Array.from(used).sort()) {
    if (imported.has(name) || defined.has(name) || params.has(name) || arrowArgs.has(name)) continue;
    if (REACT_EXPORTS.has(name) || GLOBALS.has(name)) continue;
    console.log(`MISSING ${path.relative(process.cwd(), file)}: <${name}> is used but never imported or defined`);
    problems += 1;
  }
}

if (problems === 0) console.log(`OK: every JSX component in ${files.length} files resolves to an import or a local definition`);
process.exit(problems ? 1 : 0);
