// Local preview only: real canvas runtime, real fonts, served from a temp folder.
const { chromium } = require('playwright');
const http = require('http'), fs = require('fs'), path = require('path');
const S = process.argv[2], outDir = process.argv[3];
const design = path.join(S, 'design/project'), site = path.join(S, 'preview/site'), fontsDir = path.join(S, 'preview/fonts');
const srv = http.createServer((req, res) => {
  const u = decodeURIComponent(req.url.split('?')[0]).replace(/^\//, '');
  let file = u === 'support.js' ? path.join(site, 'support.js') : path.join(design, path.basename(u)); if (!fs.existsSync(file)) file = path.join(S, 'preview/thin', path.basename(u));
  if (!fs.existsSync(file)) { res.writeHead(404); return res.end(); }
  res.writeHead(200, { 'content-type': file.endsWith('.js') ? 'text/javascript' : 'text/html; charset=utf-8' });
  fs.createReadStream(file).pipe(res);
});
function fontCss() {
  let css = '';
  for (const [fam, slug] of [['IBM Plex Sans', 'sans'], ['IBM Plex Mono', 'mono']]) for (const w of [400, 500, 600]) {
    const dir = fs.readdirSync(fontsDir).find(d => d.startsWith('x_') && d.includes('plex-' + slug));
    const f = path.join(fontsDir, dir, 'package/files', `ibm-plex-${slug}-latin-${w}-normal.woff2`);
    css += `@font-face{font-family:'${fam}';font-weight:${w};src:url(data:font/woff2;base64,${fs.readFileSync(f).toString('base64')}) format('woff2');}`;
  }
  return css;
}
(async () => {
  await new Promise(r => srv.listen(0, r));
  const port = srv.address().port;
  const browser = await chromium.launch();
  const css = fontCss();
  const steps = require(path.resolve(process.argv[4]));
  const problems = [];
  for (const step of steps) {
    const page = await browser.newPage({ viewport: { width: step.w || 1280, height: step.h || 800 } });
    page.on('pageerror', e => problems.push(step.name + ' pageerror: ' + e.message));
    page.on('console', m => { if (['error', 'warning'].includes(m.type()) && !/fonts|ERR_|Failed to load resource/.test(m.text())) problems.push(step.name + ' console: ' + m.text().slice(0, 300)); });
    await page.route(/fonts\.(googleapis|gstatic)\.com/, r => r.fulfill({ status: 200, contentType: 'text/css', body: css }));
    await page.goto(`http://localhost:${port}/${step.file || 'Main.dc.html'}`);
    await page.waitForTimeout(700);
    await page.evaluate(() => document.fonts.ready);
    if (step.run) await step.run(page, (n) => page.screenshot({ path: path.join(outDir, n + '.png') }));
    if (step.shot !== false) await page.screenshot({ path: path.join(outDir, step.name + '.png'), fullPage: !!step.full });
    if (step.check) console.log(step.name, JSON.stringify(await page.evaluate(step.check)));
    await page.close();
  }
  await browser.close(); srv.close();
  console.log(problems.length ? 'PROBLEMS:\n' + problems.join('\n') : 'no page errors');
})().catch(e => { console.error('HARNESS FAILED', e); process.exit(1); });
