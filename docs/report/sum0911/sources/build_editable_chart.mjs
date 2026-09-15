import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {Presentation,PresentationFile,FileBlob} from '@oai/artifact-tool';
import {createLongTailFigure} from './long_tail_chart.mjs';
import {finalizeOfficeCharts} from './finalize_office_charts.mjs';
const here=path.dirname(fileURLToPath(import.meta.url)),out=path.dirname(here),workspace=path.dirname(out);
const build=path.join(workspace,'.sum0911-build'),revision=Date.now();
const runtime='C:/Users/F-O-O/.cache/codex-runtimes/codex-primary-runtime/dependencies';
const skill='C:/Users/F-O-O/.codex/plugins/cache/openai-primary-runtime/presentations/26.909.12148/skills/presentations';
process.env.RUNTIME_NODE_MODULES=path.join(runtime,'node/node_modules');
process.env.RUNTIME_NODE=path.join(runtime,'node/bin/node.exe');
process.env.RUNTIME_PYTHON=path.join(runtime,'python/python.exe');
const {finalizePresentation}=await import(pathToFileURL(path.join(skill,'container_tools/artifact_tool_utils.mjs')));
const common={workspaceDir:workspace,pythonExecutable:path.join(runtime,'python/python.exe'),
 integrityValidatorPath:path.join(skill,'container_tools/inspect_presentation_package_integrity.py'),
 layoutValidatorPath:path.join(skill,'container_tools/inspect_presentation_layout_geometry.py'),
 requiredNativeTableOwnerSlides:[],fontPolicy:{basis:'design',families:['Microsoft YaHei']},verifyArtifactToolImport:true};
const deck=path.join(out,'DTB阶段性汇报.pptx'),fixedDeck=path.join(build,'release',`office-${revision}.pptx`);
await finalizeOfficeCharts({...common,candidatePath:deck,finalPath:fixedDeck,
 layoutArgs:['--expected-slide-size-emu','15240000,8572500','--validate-bullet-geometry','--validate-heading-fit'],
 explicitTotalSlideCount:4,requiredNativeChartOwnerSlides:[3,4],receiptPath:path.join(build,`office-${revision}.json`)});
await fs.copyFile(deck,path.join(build,`before-office-${revision}.pptx`));
await fs.copyFile(fixedDeck,deck);
await fs.copyFile(path.join(build,`office-${revision}.json`),path.join(build,'validation.json'));
const d=JSON.parse(await fs.readFile(path.join(here,'evidence.json'),'utf8'));
const figure=createLongTailFigure(Presentation,d);
figure.slide.speakerNotes.textFrame.setText('来源：sources/evidence.json，Vanilla-06的391条有效请求。主图按原始timestamp枚举，9个任务区段，纵轴850处分段变尺度。11条长尾含9条标题和2条业务。850为候选介入阈值，潜在尾部不等于可安全删除的冗余。空心样本用同心标记层实现，两个图表系列共用内嵌工作簿中的同一组X/Y数据。');
const raw=path.join(build,`longtail-${revision}.pptx`),packaged=path.join(build,'release',`longtail-data-${revision}.pptx`);
await(await PresentationFile.exportPptx(figure.presentation)).save(raw);
const figOptions={...common,layoutArgs:['--expected-slide-size-emu','15240000,9525000','--validate-bullet-geometry','--validate-heading-fit'],explicitTotalSlideCount:1,requiredNativeChartOwnerSlides:[1]};
await finalizePresentation({...figOptions,candidatePath:raw,finalPath:packaged,materializeLiteralChartWorkbooks:true,receiptPath:path.join(build,`longtail-data-${revision}.json`)});
const fixedFigure=path.join(build,'release',`longtail-office-${revision}.pptx`);
await finalizeOfficeCharts({...figOptions,candidatePath:packaged,finalPath:fixedFigure,receiptPath:path.join(build,`longtail-office-${revision}.json`)});
const figurePath=path.join(out,'统计图','思考长度长尾与850阈值.pptx');
await fs.copyFile(fixedFigure,figurePath);
await fs.copyFile(path.join(build,`longtail-office-${revision}.json`),path.join(build,'longtail-validation.json'));
for(const [file,folder] of [[deck,path.join(out,'预览')],[figurePath,path.join(out,'统计图')]]){
 const imported=await PresentationFile.importPptx(await FileBlob.load(file));
 for(let i=0;i<imported.slides.items.length;i++){
  const png=await imported.export({slide:imported.slides.items[i],format:'png',scale:file===deck?1:2});
  const name=file===deck?`${String(i+1).padStart(2,'0')}.png`:'思考长度长尾与850阈值.png';
  await fs.writeFile(path.join(folder,name),new Uint8Array(await png.arrayBuffer()));
 }
}
console.log('Office marker repair and editable standalone chart delivered.');
