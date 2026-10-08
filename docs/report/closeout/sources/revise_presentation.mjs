// Preserve the bound Office objects in r5; repair styles and update replay latency.
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';

const here = path.dirname(fileURLToPath(import.meta.url));
const report = path.dirname(here);
const root = path.resolve(report, '../../..');
const runtime = process.env.RUNTIME_ROOT ?? path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies');
async function resolvePresentationSkill() {
  if (process.env.PRESENTATIONS_SKILL !== undefined) return process.env.PRESENTATIONS_SKILL;
  const plugins = path.join(os.homedir(), '.codex/plugins/cache/openai-primary-runtime/presentations');
  const versions = (await fs.readdir(plugins))
    .filter(version => /^\d+(?:\.\d+)*$/.test(version))
    .sort((a, b) => b.localeCompare(a, undefined, { numeric: true }));
  for (const version of versions) {
    const candidate = path.join(plugins, version, 'skills/presentations');
    try {
      if ((await fs.stat(path.join(candidate, 'container_tools/artifact_tool_utils.mjs'))).isFile()) return candidate;
    } catch (error) {
      if (error.code !== 'ENOENT' && error.code !== 'ENOTDIR') throw error;
    }
  }
  throw new Error(`No presentations skill found in ${plugins}; set PRESENTATIONS_SKILL explicitly.`);
}
const skill = await resolvePresentationSkill();
process.env.RUNTIME_NODE_MODULES = path.join(runtime, 'node/node_modules');
process.env.RUNTIME_NODE = path.join(runtime, 'node/bin/node.exe');
process.env.RUNTIME_PYTHON = path.join(runtime, 'python/python.exe');
const { FileBlob, PresentationFile } = await import(pathToFileURL(path.join(process.env.RUNTIME_NODE_MODULES, '@oai/artifact-tool/dist/artifact_tool.mjs')));
const { finalizePresentation } = await import(pathToFileURL(path.join(skill, 'container_tools/artifact_tool_utils.mjs')));
const revision = process.argv[2] ?? 'r10';
if (!/^r\d+$/.test(revision)) throw new Error('Revision must be r9, r10, ...');
const source = path.join(report, 'presentation/DTB_closeout_20260930_r5.pptx');
const final = path.join(report, `presentation/DTB_closeout_20260930_${revision}.pptx`);
const build = path.join(root, 'artifacts/closeout-build/20261008', revision);
await fs.mkdir(build, { recursive: true });
try { await fs.access(final); throw new Error(`Output already exists: ${final}`); }
catch (e) { if (e.code !== 'ENOENT') throw e; }
const imported = await PresentationFile.importPptx(await FileBlob.load(source));
const inspection = await imported.inspect({ kind: 'slide,layout,shape,chart', maxChars: 24000 });
await fs.writeFile(path.join(build, 'source-inspection.ndjson'), inspection.ndjson);
const candidate = path.join(build, `candidate-${Date.now()}.pptx`);
const patchLog = execFileSync(process.env.RUNTIME_PYTHON, [path.join(here, 'revise_presentation.py'), source, candidate], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
await fs.writeFile(path.join(build, 'changed-parts.json'), patchLog);
await fs.mkdir(path.join(build, 'release'), { recursive: true });
const validated = path.join(build, 'release', `DTB_closeout_20260930_${revision}.pptx`);
await finalizePresentation({
  workspaceDir: build, candidatePath: candidate, finalPath: validated,
  pythonExecutable: process.env.RUNTIME_PYTHON,
  integrityValidatorPath: path.join(skill, 'container_tools/inspect_presentation_package_integrity.py'),
  layoutValidatorPath: path.join(skill, 'container_tools/inspect_presentation_layout_geometry.py'),
  layoutArgs: ['--expected-slide-size-emu', '15240000,8572500'],
  explicitTotalSlideCount: 5, requiredNativeChartOwnerSlides: [3, 4],
  requiredNativeTableOwnerSlides: [], materializeLiteralChartWorkbooks: false,
  fontPolicy: { basis: 'reference', families: ['Microsoft YaHei'], referencePath: source,
    referenceSha256: '2fbf902e74ec26b446267ded768ddd61a7f510e9813de311251bccf75212f9ba' },
  verifyArtifactToolImport: true, receiptPath: path.join(build, 'validation.json'),
});
await fs.copyFile(validated, final);
console.log(JSON.stringify({ output: final, source, slides: 5, charts: 5 }));
