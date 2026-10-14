// Export original r10 native objects as blog figures, without redrawing them.
// Only private export copies have a smaller canvas and translated object origins.
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';

const here = path.dirname(fileURLToPath(import.meta.url));
const report = path.dirname(here);
const root = path.resolve(report, '../../..');
const runtime = process.env.RUNTIME_ROOT ?? path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies');
const packages = path.join(runtime, 'node/node_modules');
const require = createRequire(path.join(packages, 'blog-figure-loader.cjs'));
const JSZip = require('jszip');
const { xml2js, js2xml } = require('xml-js');
const source = path.join(report, 'presentation/DTB_closeout_20260930_r10.pptx');
const output = path.resolve(process.argv[2] ?? path.join(report, 'figures'));
const build = path.join(root, 'artifacts/closeout-blog-figures/20261014');
const sourceBytes = await fs.readFile(source);
const hash = data => crypto.createHash('sha256').update(data).digest('hex');
const sourceSha256 = hash(sourceBytes);
if (sourceSha256 !== 'cd8f1e6982090e0bdfb750ecf12e26dcbf8be346deab533146d181acad6b249c') {
  throw new Error('Source r10 changed; review its figures before exporting a new version.');
}
await fs.mkdir(output, { recursive: true });
await fs.mkdir(build, { recursive: true });

// Isolate the native renderer. A worker can exit nonzero during shutdown after
// writing every image; accept only this attempt's complete, hash-verified files.
// An old manifest can never make an incomplete render look successful.
if (process.env.BLOG_FIGURE_EXPORT_WORKER !== '1') {
  const attempt = crypto.randomUUID();
  const result = spawnSync(process.execPath, [fileURLToPath(import.meta.url), ...process.argv.slice(2)], {
    env: { ...process.env, BLOG_FIGURE_EXPORT_WORKER: '1', BLOG_FIGURE_EXPORT_ATTEMPT: attempt },
    windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], encoding: 'utf8',
  });
  await fs.writeFile(path.join(build, 'renderer.log'), (result.stdout ?? '') + (result.stderr ?? ''));
  const completed = JSON.parse(await fs.readFile(path.join(output, 'source_manifest.json'), 'utf8'));
  if (result.error || completed.export_attempt_id !== attempt || completed.source_sha256 !== sourceSha256 || completed.figures.length !== 7) {
    throw new Error(`Renderer did not complete this attempt: ${result.error ?? result.status}`);
  }
  for (const figure of completed.figures) {
    const data = await fs.readFile(path.join(output, figure.filename));
    const expected = figure.source_bounds_px.slice(2).map(n => n * completed.render_scale);
    if (hash(data) !== figure.sha256 || data.length !== figure.bytes || data.readUInt32BE(16) !== expected[0] || data.readUInt32BE(20) !== expected[1]) {
      throw new Error(`Exported image failed verification: ${figure.filename}`);
    }
  }
  if (hash(await fs.readFile(source)) !== sourceSha256) throw new Error('Source deck changed during export');
  await fs.writeFile(path.join(build, 'render-receipt.json'), JSON.stringify({
    export_attempt_id: attempt, renderer_exit_code: result.status, renderer_signal: result.signal,
    renderer_exited_cleanly: result.status === 0, verified_complete_images: 7, source_deck_unchanged: true,
  }, null, 2) + '\n');
  console.log(JSON.stringify({ output, verifiedFigures: 7, rendererExitCode: result.status, sourceDeckUnchanged: true }));
  process.exit(0);
}

const { FileBlob, PresentationFile } = await import(pathToFileURL(path.join(packages, '@oai/artifact-tool/dist/artifact_tool.mjs')));

const figures = [
  { name: 'noemate_reasoning_distribution', slide: 4, bounds: [62, 198, 715, 602], ids: [6, 7, 8, 31, 10, 11, 12],
    caption: 'Noemate 历史主 Agent 8,277 次有效调用，750 token 阈值，连续线性纵轴。' },
  { name: 'development_reasoning_distribution', slide: 3, bounds: [64, 185, 1474, 393], ids: [6, 36, 37, 9, 10, 11, 12, 13, 14, 15],
    caption: '研发 Vanilla-06 的 391 次有效调用，含标题；0–850 与 850–3500 两段等高。' },
  { name: 'dtb_architecture', slide: 1, bounds: [648, 197, 902, 629],
    ids: [14, 15, 16, 17, 18, 19, 20, 21, 22, 27, 29, 32, 34, 36, 59, 60, 61, 62, 64, 66, 67, 69, 71],
    caption: 'DTB 职责概览：路由、预算、生成中检查、试答或预算收束、原生 parser。' },
  { name: 'native_observation_pipeline', slide: 3, bounds: [64, 584, 1474, 242], ids: [16, 17, 18, 19, 20, 21, 22, 29, 52, 53, 54, 55, 56, 57],
    caption: 'DTB 位于 AsyncLLM.generate 与原生 parser 之间，虚线表示生成控制。' },
  { name: 'protocol_fix_timeline', slide: 2, bounds: [64, 202, 1474, 621], allWithin: true,
    caption: '四轮历史定向协议修复，分母和目标问题各自独立。' },
  { name: 'noemate_replay_latency', slide: 4, bounds: [799, 197, 740, 276], ids: [13, 14, 15, 38],
    caption: '5 个固定长输入 ×10 对回放，平均 HTTP E2E 24.87→18.51 秒。' },
  { name: 'development_llm_e2e', slide: 4, bounds: [799, 556, 740, 267], ids: [19, 20, 21, 44],
    caption: '9 个研发任务的累计 LLM E2E 2,902.45→2,566.70 秒。' },
];

const parse = value => xml2js(value, { compact: false });
const write = value => js2xml(value, { compact: false });
function find(node, name) {
  if (node?.name === name) return node;
  for (const child of node?.elements ?? []) { const found = find(child, name); if (found) return found; }
}
const emu = px => Math.round(px * 9525);
const original = await JSZip.loadAsync(sourceBytes);
const sourceParts = {};
for (const [name, entry] of Object.entries(original.files)) {
  if (!entry.dir && (name.startsWith('ppt/charts/') || name.startsWith('ppt/embeddings/'))) {
    sourceParts[name] = hash(await entry.async('nodebuffer'));
  }
}

const manifest = { schema_version: 1, source: 'presentation/DTB_closeout_20260930_r10.pptx',
  export_attempt_id: process.env.BLOG_FIGURE_EXPORT_ATTEMPT,
  source_sha256: sourceSha256, source_slide_size_px: [1600, 900], render_scale: 2,
  method: 'select original OOXML objects, translate their origins, export a private view with Artifact Tool',
  unchanged_chart_and_workbook_sha256: sourceParts, figures: [] };

for (const figure of figures) {
  const zip = await JSZip.loadAsync(sourceBytes);
  const slidePath = `ppt/slides/slide${figure.slide}.xml`;
  const xml = parse(await zip.file(slidePath).async('string'));
  const tree = find(xml, 'p:spTree');
  const [left, top, width, height] = figure.bounds;
  const selected = [];
  tree.elements = tree.elements.filter(node => {
    const id = find(node, 'p:cNvPr')?.attributes?.id;
    if (id === undefined || node.name === 'p:nvGrpSpPr') return true;
    const transform = find(node, 'p:xfrm') ?? find(node, 'a:xfrm');
    const offset = transform?.elements?.find(child => child.name === 'a:off');
    const extent = transform?.elements?.find(child => child.name === 'a:ext');
    const x = Number(offset?.attributes?.x) / 9525;
    const y = Number(offset?.attributes?.y) / 9525;
    const w = Number(extent?.attributes?.cx) / 9525;
    const h = Number(extent?.attributes?.cy) / 9525;
    const keep = figure.allWithin ? (x >= left && y >= top && x + w <= left + width && y + h <= top + height) : figure.ids.includes(Number(id));
    if (!keep) return false;
    if (!offset) throw new Error(`Shape ${id} has no origin`);
    // Leave rotation, flips, connector geometry, chart layout and text unchanged.
    offset.attributes.x = String(Number(offset.attributes.x) - emu(left));
    offset.attributes.y = String(Number(offset.attributes.y) - emu(top));
    selected.push(Number(id));
    return true;
  });
  if (selected.length === 0 || (figure.ids && (selected.length !== figure.ids.length || figure.ids.some(id => !selected.includes(id))))) {
    throw new Error(`Object selection differs for ${figure.name}: ${selected.join(',')}`);
  }
  zip.file(slidePath, write(xml));
  const presentationXml = parse(await zip.file('ppt/presentation.xml').async('string'));
  const size = find(presentationXml, 'p:sldSz');
  size.attributes.cx = String(emu(width));
  size.attributes.cy = String(emu(height));
  zip.file('ppt/presentation.xml', write(presentationXml));
  for (const [name, expected] of Object.entries(sourceParts)) {
    if (hash(await zip.file(name).async('nodebuffer')) !== expected) throw new Error(`Chart/workbook changed: ${name}`);
  }
  const viewPath = path.join(build, `${figure.name}.pptx`);
  await fs.writeFile(viewPath, await zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' }));
  const view = await PresentationFile.importPptx(await FileBlob.load(viewPath));
  const blob = await view.export({ slide: view.slides.items[figure.slide - 1], format: 'png', scale: 2 });
  const png = new Uint8Array(await blob.arrayBuffer());
  const filename = `${figure.name}.png`;
  await fs.writeFile(path.join(output, filename), png);
  manifest.figures.push({ filename, source_slide: figure.slide, source_shape_ids: selected,
    source_bounds_px: figure.bounds, caption: figure.caption, sha256: hash(png), bytes: png.length });
  console.log(`Exported ${filename}`);
}
if (hash(await fs.readFile(source)) !== sourceSha256) throw new Error('Source deck changed during export');
await fs.writeFile(path.join(output, 'source_manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
console.log(JSON.stringify({ output, figures: figures.length, sourceDeckUnchanged: true }));
