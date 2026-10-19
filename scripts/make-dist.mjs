// Builds dist/ for the desktop app from web/mochi-diary.html:
// wraps the page in a full HTML document and swaps the Google Fonts link for
// bundled copies of Mochiy Pop One and M PLUS Rounded 1c, so the app works offline.
// Only the font slices that cover Latin text and punctuation are copied (the
// full Japanese sets are ~50 MB).
import { readFileSync, writeFileSync, mkdirSync, copyFileSync, rmSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(root, 'dist');
const fontsOut = join(dist, 'fonts');
rmSync(dist, { recursive: true, force: true });
mkdirSync(fontsOut, { recursive: true });

const FONTS = [
  { pkg: 'mochiy-pop-one', weights: [400] },
  { pkg: 'm-plus-rounded-1c', weights: [400, 500, 700, 800] }
];
// Basic Latin, Latin-1, Latin Extended-A/B, general punctuation, currency, arrows.
const WANTED = [[0x20, 0x24f], [0x2000, 0x206f], [0x20a0, 0x20cf], [0x2190, 0x21ff]];

function parseRanges(s) {
  return s.split(',').map(p => p.trim().replace(/^U\+/i, '')).filter(Boolean).map(p => {
    if (p.includes('?')) return [parseInt(p.replace(/\?/g, '0'), 16), parseInt(p.replace(/\?/g, 'F'), 16)];
    const [a, b] = p.split('-');
    return [parseInt(a, 16), parseInt(b || a, 16)];
  });
}
const wanted = ranges => ranges.some(([a, b]) => WANTED.some(([c, d]) => a <= d && b >= c));

let fontCss = '';
let files = 0;
let bytes = 0;
for (const { pkg, weights } of FONTS) {
  const base = join(root, 'node_modules', '@fontsource', pkg);
  if (!existsSync(base)) throw new Error(`Missing @fontsource/${pkg}. Run "npm install" first.`);
  for (const w of weights) {
    const css = readFileSync(join(base, `${w}.css`), 'utf8');
    for (const block of css.split('@font-face').slice(1)) {
      const name = /url\(\.\/files\/([^)]+?\.woff2)\)/.exec(block);
      const range = /unicode-range:\s*([^;]+);/.exec(block);
      if (!name || (range && !wanted(parseRanges(range[1])))) continue;
      const src = join(base, 'files', name[1]);
      copyFileSync(src, join(fontsOut, name[1]));
      files++; bytes += readFileSync(src).length;
      const family = /font-family:\s*'([^']+)'/.exec(block)[1];
      fontCss += `@font-face{font-family:'${family}';font-style:normal;font-display:swap;font-weight:${w};src:url(fonts/${name[1]}) format('woff2');${range ? `unicode-range:${range[1]};` : ''}}\n`;
    }
  }
  copyFileSync(join(base, 'LICENSE'), join(fontsOut, `${pkg}-LICENSE.txt`));
}
if (!files) throw new Error('No font files matched; check the @fontsource packages.');

let page = readFileSync(join(root, 'web', 'mochi-diary.html'), 'utf8');
const fontLinks = /<link rel="preconnect"[^>]*>\s*|<link rel="stylesheet" href="https:\/\/fonts\.googleapis\.com[^>]*>\s*/g;
if (!fontLinks.test(page)) throw new Error('Could not find the Google Fonts links in web/mochi-diary.html');
page = page.replace(fontLinks, '');

const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<style>
${fontCss}:root{color-scheme:light}
body{margin:0;font:14px system-ui,sans-serif}
img{max-width:100%}
[hidden]{display:none!important}
</style>
</head>
<body>
${page}
</body>
</html>
`;
writeFileSync(join(dist, 'index.html'), html);
console.log(`dist/index.html written (${Math.round(html.length / 1024)} KB), ${files} font files (${Math.round(bytes / 1024)} KB)`);
