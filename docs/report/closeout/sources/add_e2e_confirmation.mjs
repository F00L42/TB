// Add one editable conclusion line to r9 while preserving Office objects.
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';

const here = path.dirname(fileURLToPath(import.meta.url));
const report = path.dirname(here);
const root = path.resolve(report, '../../..');
const runtime = process.env.RUNTIME_ROOT ?? path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies');
const skill = process.env.PRESENTATIONS_SKILL ?? path.join(os.homedir(), '.codex/plugins/cache/openai-primary-runtime/presentations/26.1007.11041/skills/Presentations');
const packages = path.join(runtime, 'node/node_modules');
const require = createRequire(path.join(packages, 'artifact-loader.cjs'));
const JSZip = require('jszip');
const { xml2js, js2xml } = require('xml-js');
process.env.RUNTIME_NODE_MODULES = packages;
process.env.RUNTIME_NODE = path.join(runtime, 'node/bin/node.exe');
process.env.RUNTIME_PYTHON = path.join(runtime, 'python/python.exe');
const { FileBlob, Presentation, PresentationFile } = await import(pathToFileURL(path.join(packages, '@oai/artifact-tool/dist/artifact_tool.mjs')));
const { finalizePresentation } = await import(pathToFileURL(path.join(skill, 'container_tools/artifact_tool_utils.mjs')));

const revision = process.argv[2] ?? 'r10';
if (!/^r\d+$/.test(revision)) throw new Error('Use a new r-number for each output');
const source = path.join(report, 'presentation/DTB_closeout_20260930_r9.pptx');
const final = path.join(report, `presentation/DTB_closeout_20260930_${revision}.pptx`);
const build = path.join(root, 'artifacts/closeout-build/20261013', revision);
await fs.mkdir(build, { recursive: true });
try { await fs.access(final); throw new Error(`Output already exists: ${final}`); }
catch (e) { if (e.code !== 'ENOENT') throw e; }
const sourceBytes = await fs.readFile(source);
const sha = crypto.createHash('sha256').update(sourceBytes).digest('hex');
if (sha !== '9e47f1ac7d24e1f445fa810709736f58404cc163f18b22af384277655905b94e') throw new Error('Unexpected source r9');
const text = '端到端测试确认：长请求加速，短请求性能非劣（固定初始输入）。';

// Author the new editable textbox with Artifact Tool, then transplant only
// this shape into the unchanged package to keep hand-edited charts/connectors.
const overlay = Presentation.create({ slideSize: { width: 1600, height: 900 } });
const overlaySlide = overlay.slides.add();
const shape = overlaySlide.shapes.add({ geometry: 'textbox', name: 'Noemate E2E confirmation',
  position: { left: 824, top: 506, width: 700, height: 25 }, fill: 'none',
  line: { style: 'solid', fill: 'none', width: 0 } });
shape.text = text;
shape.text.style = { typeface: 'Microsoft YaHei', fontSize: 18.6666667, color: '#142C3D',
  alignment: 'left', verticalAlignment: 'middle', autoFit: 'none',
  insets: { left: 0, right: 0, top: 0, bottom: 0 } };
const overlayFile = path.join(build, 'authored-textbox.pptx');
await (await PresentationFile.exportPptx(overlay)).save(overlayFile);

const parse = value => xml2js(value, { compact: false });
const write = value => js2xml(value, { compact: false });
const clone = value => JSON.parse(JSON.stringify(value));
function descend(node, name) {
  if (node.name === name) return node;
  for (const child of node.elements ?? []) { const hit = descend(child, name); if (hit) return hit; }
}
function all(node, name, found = []) {
  if (node.name === name) found.push(node);
  for (const child of node.elements ?? []) all(child, name, found);
  return found;
}
const overlayZip = await JSZip.loadAsync(await fs.readFile(overlayFile));
const textboxXml = parse(await overlayZip.file('ppt/slides/slide1.xml').async('string'));
const newShape = clone(all(textboxXml, 'p:sp').find(s => descend(s, 'a:t')?.elements?.[0]?.text === text));
if (!newShape) throw new Error('Authored shape missing');
const zip = await JSZip.loadAsync(sourceBytes);
const slidePath = 'ppt/slides/slide4.xml';
const slide = parse(await zip.file(slidePath).async('string'));
const spTree = descend(slide, 'p:spTree');
const ids = all(slide, 'p:cNvPr').map(n => Number(n.attributes.id));
const newId = String(Math.max(...ids) + 1);
descend(newShape, 'p:cNvPr').attributes.id = newId;
spTree.elements.push(newShape);

// Keep the original sample label; add an unobtrusive local blog hyperlink.
const sample = all(slide, 'p:sp').find(s => descend(s, 'p:cNvPr')?.attributes?.id === '17');
const paragraph = descend(sample, 'a:p');
const firstRun = paragraph.elements.find(n => n.name === 'a:r');
const linkRun = clone(firstRun);
descend(linkRun, 'a:t').elements = [{ type: 'text', text: '  ·  详见实验博客' }];
const linkStyle = descend(linkRun, 'a:rPr');
linkStyle.attributes.sz = '1500';
linkStyle.attributes.b = '0';
const relPath = 'ppt/slides/_rels/slide4.xml.rels';
const rels = parse(await zip.file(relPath).async('string'));
const relRoot = rels.elements.find(n => n.type === 'element');
const relId = 'rId' + (Math.max(...relRoot.elements.filter(n => n.name === 'Relationship').map(n => Number(n.attributes.Id.replace('rId', '')))) + 1);
relRoot.elements.push({ type: 'element', name: 'Relationship', attributes: {
  Id: relId, Type: 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink',
  Target: '../noemate_e2e_20261013.md', TargetMode: 'External' } });
linkStyle.elements.push({ type: 'element', name: 'a:hlinkClick', attributes: { 'r:id': relId } });
paragraph.elements.push(linkRun);
zip.file(slidePath, write(slide));
zip.file(relPath, write(rels));

const notesPath = 'ppt/notesSlides/notesSlide4.xml';
const notes = parse(await zip.file(notesPath).async('string'));
const bodyShape = all(notes, 'p:sp').find(s => descend(s, 'p:ph')?.attributes?.type === 'body');
const notesBody = descend(bodyShape, 'p:txBody');
const noteLines = [
  '2026-10-13 E2E supplement: fixed initial business inputs, same model weights, formal observability=off (operator confirmed).',
  'Slide wording adopts this run-level performance acceptance: long requests faster; short requests performance noninferior. This does not mean quality noninferiority or an isolated per-prompt causal DTB effect.',
  'Main request mean E2E 14.971→10.685s. Vanilla-prestratified <=750 initial calls: 1475 shared questions, case-weighted mean 9.361→6.752s, D/V=.721, 95% bootstrap CI [.700,.743].',
  'Visible main reasoning >800: 316/8277→0/8018; maximum1841→755. The 114 same-input native controls also improved35.4%; preserve this attribution boundary in the blog.',
  'Do not merge the new 11441-request capture with the old frozen50 replay pairs or replace the25.58% chart value.',
  'Full Markdown blog: ../noemate_e2e_20261013.md; compact evidence: ../evidence/noemate_e2e_20261013/.',
  'DTB source commit03d9ec6c3d7b48dc7925fc024b63462bd9fe7855; Vanilla60f2d7c01faa3268874893dc42f850ad86086c7c. New tool-argument errors/truncations remain documented separately.'
];
for (const line of noteLines) notesBody.elements.push({ type: 'element', name: 'a:p', elements: [
  { type: 'element', name: 'a:r', elements: [{ type: 'element', name: 'a:t', elements: [{ type: 'text', text: line }] }] }
] });
zip.file(notesPath, write(notes));
const rawCandidate = path.join(build, `candidate-${Date.now()}.pptx`);
await fs.writeFile(rawCandidate, await zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE' }));

// Assert the preservation boundary before validation/rendering.
const changed = [slidePath, relPath, notesPath];
const original = await JSZip.loadAsync(sourceBytes);
for (const [name, entry] of Object.entries(original.files)) {
  if (entry.dir || changed.includes(name)) continue;
  const a = await entry.async('nodebuffer'); const b = await zip.file(name).async('nodebuffer');
  if (!a.equals(b)) throw new Error(`Unexpected changed package part: ${name}`);
}
await fs.writeFile(path.join(build, 'change-boundary.json'), JSON.stringify({ sourceSha256: sha,
  changedParts: changed, unchangedChartsAndWorkbooks: true, addedShape: newId, scope: 'slide4_only' }, null, 2));
// Office roundtrips altered cache strings at 1e-13 or less. Retain every
// workbook/data/shape; restore cache text from those exact worksheet cells.
const candidate = path.join(build, `cache-aligned-${Date.now()}.pptx`);
const cacheLog = execFileSync(process.env.RUNTIME_PYTHON,
  [path.join(here, 'refresh_presentation_caches.py'), rawCandidate, candidate],
  { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
await fs.writeFile(path.join(build, 'cache-alignment.json'), cacheLog);
const validated = path.join(build, 'release', path.basename(final));
await fs.mkdir(path.dirname(validated), { recursive: true });
await finalizePresentation({ workspaceDir: build, candidatePath: candidate, finalPath: validated,
  pythonExecutable: process.env.RUNTIME_PYTHON,
  integrityValidatorPath: path.join(skill, 'container_tools/inspect_presentation_package_integrity.py'),
  layoutValidatorPath: path.join(skill, 'container_tools/inspect_presentation_layout_geometry.py'),
  layoutArgs: ['--expected-slide-size-emu', '15240000,8572500'],
  explicitTotalSlideCount: 5, requiredNativeChartOwnerSlides: [3, 4], requiredNativeTableOwnerSlides: [],
  materializeLiteralChartWorkbooks: false,
  fontPolicy: { basis: 'reference', families: ['Microsoft YaHei'], referencePath: source, referenceSha256: sha },
  verifyArtifactToolImport: true, receiptPath: path.join(build, 'validation.json') });

// Render source slide4 and every output slide, without rebuilding the file.
async function saveBlob(file, blob) { await fs.writeFile(file, new Uint8Array(await blob.arrayBuffer())); }
const before = await PresentationFile.importPptx(await FileBlob.load(source));
await saveBlob(path.join(build, 'before-slide4.png'), await before.export({ slide: before.slides.items[3], format: 'png', scale: 1 }));
const after = await PresentationFile.importPptx(await FileBlob.load(validated));
for (const [index, page] of after.slides.items.entries()) {
  await saveBlob(path.join(build, `slide-${index + 1}.png`), await after.export({ slide: page, format: 'png', scale: 1 }));
}
await fs.copyFile(validated, final);
console.log(JSON.stringify({ output: final, slides: 5, unchangedChartData: true, preview: path.join(build, 'slide-4.png') }));
