// Wraps each design/frag/<Name>.html body fragment into a Design Component artboard.
import { readFileSync, writeFileSync, readdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const adw = readFileSync(join(here, '_adw.css'), 'utf8');
const sizes = JSON.parse(readFileSync(join(here, 'sizes.json'), 'utf8'));

for (const f of readdirSync(join(here, 'frag')).filter((n) => n.endsWith('.html'))) {
  const name = f.replace(/\.html$/, '');
  const raw = readFileSync(join(here, 'frag', f), 'utf8');
  let extra = '';
  const body = raw.replace(/^\s*<style>([\s\S]*?)<\/style>/, (_m, css) => { extra = css; return ''; }).trim();
  const size = sizes[name] || { width: 1200, height: 800 };
  const withSidebar = body.replace(/<!--#sidebar ([a-z-]+)-->/g, (_m, key) => {
    const partial = readFileSync(join(here, 'partials', 'sidebar.html'), 'utf8');
    const marker = `class="nav" id="nav-${key}"`;
    if (!partial.includes(marker)) throw new Error(`sidebar has no nav item "${key}"`);
    return partial.replace(marker, `class="nav sel" id="nav-${key}"`).trim();
  });
  const out = `<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
  <style>
${adw}${extra}
  </style>
</helmet>
${withSidebar}
</x-dc>
<script data-dc-script data-props='{"$preview":{"width":${size.width},"height":${size.height}}}'>
class Component extends DCLogic {
  renderVals() { return {}; }
}
</script>
</body>
</html>
`;
  writeFileSync(join(here, `${name}.dc.html`), out);
  console.log(`built ${name}.dc.html (${size.width}x${size.height})`);
}
