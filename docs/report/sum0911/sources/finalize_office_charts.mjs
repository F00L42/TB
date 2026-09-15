import path from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const runtime='C:/Users/F-O-O/.cache/codex-runtimes/codex-primary-runtime/dependencies';
const skill='C:/Users/F-O-O/.codex/plugins/cache/openai-primary-runtime/presentations/26.909.12148/skills/presentations';
const {finalizePresentation}=await import(pathToFileURL(path.join(skill,'container_tools/artifact_tool_utils.mjs')));
export async function finalizeOfficeCharts(options){
 const fixed=options.finalPath.replace(/\.pptx$/,'.candidate.pptx');
 await promisify(execFile)(path.join(runtime,'python/python.exe'),[
  fileURLToPath(new URL('./repair_chart_markers.py',import.meta.url)),
  options.candidatePath,fixed],{windowsHide:true,encoding:'utf8'});
 return finalizePresentation({...options,candidatePath:fixed,materializeLiteralChartWorkbooks:false});
}
