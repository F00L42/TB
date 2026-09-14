import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';

const here=path.dirname(fileURLToPath(import.meta.url));
const out=path.dirname(here);
const root=path.resolve(out,'../../..');
const runtime='C:/Users/F-O-O/.cache/codex-runtimes/codex-primary-runtime/dependencies';
const skill='C:/Users/F-O-O/.codex/plugins/cache/openai-primary-runtime/presentations/26.909.12148/skills/presentations';
process.env.RUNTIME_NODE_MODULES=path.join(runtime,'node/node_modules');
process.env.RUNTIME_NODE=path.join(runtime,'node/bin/node.exe');
process.env.RUNTIME_PYTHON=path.join(runtime,'python/python.exe');
const {Presentation,PresentationFile,FileBlob}=await import(pathToFileURL(path.join(runtime,'node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs')));
const {finalizePresentation}=await import(pathToFileURL(path.join(skill,'container_tools/artifact_tool_utils.mjs')));
const revision=process.argv[2]||'v1';
const build=path.join(root,'build/engineering-timeline-0914',revision);
await fs.mkdir(path.join(build,'final'),{recursive:true});
const p=Presentation.create({slideSize:{width:1920,height:1080}});
const s=p.slides.add();
s.background.fill='#FFFFFF';
const C={red:'#C81018',ink:'#23262B',muted:'#626870',line:'#D3D6DB',panel:'#F5F6F8',green:'#17765B'};
const F='Microsoft YaHei',M='Consolas';
function txt(name,value,x,y,w,h,size=26,color=C.ink,bold=false,font=F,alignment='left'){
 const z=s.shapes.add({geometry:'textbox',name,position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});
 z.text=value;z.text.style={typeface:font,fontSize:size,color,bold,alignment,verticalAlignment:'top',autoFit:'none',wrap:'none',insets:{top:0,right:0,bottom:0,left:0}};return z;
}
function rect(name,x,y,w,h,fill){return s.shapes.add({geometry:'rect',name,position:{left:x,top:y,width:w,height:h},fill,line:{fill:'none',width:0}});}
function line(name,x,y,w,h,color=C.line,width=2){return s.shapes.add({geometry:'line',name,position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:color,width}});}
txt('title','DTB 接入 Agent：工程攻关复盘',64,37,1220,74,52,C.red,true);
txt('period','8/13 至 9/4  三周分层排障与回放验证',1260,66,596,36,24,C.muted,false,F,'right');
line('title-rule',64,119,1792,0);
rect('contract-background',64,145,1792,102,C.panel);
rect('contract-accent',64,145,6,102,C.red);
txt('difficulty','难点',90,165,90,38,27,C.ink,true);
txt('difficulty-text','多轮工具调用叠加思考与投机解码，边界偏差引发 XML 泄漏、重复生成、JSON 不完整和思考丢字',190,165,1640,39,27);
txt('contract','验收',90,206,90,38,27,C.ink,true);
txt('contract-text','一个外部请求保持一个连续 assistant 回合，工具参数完整、结束唯一，公开思考正文保真',190,206,1640,39,27);

// Native, editable alternating timeline inspired by the supplied reference PNG.
line('timeline',146,399,1628,0,'#B8BCC2',4);
const events=[
 ['8/13','建立单回合验收','检出 98/100 协议异常'],
 ['8/14','统一显式 / 隐式结束边界','role 尾流、tool 后正文均归零'],
 ['8/14','对齐 stop 的 text / IDs','XML 泄漏 13/100 降至 0/100'],
 ['8/17–19','透传源终止事件，修复同批收尾','265 个调用无目标结构异常'],
 ['9/1','final 移除内部思考 stop','非法 JSON 133/300 降至 0/300'],
 ['9/3–4','结构 token 停止与正文保真','思考丢字 47/60 降至 0/60'],
];
events.forEach(([date,title,result],i)=>{
 const cx=250+i*284;
 const x=cx-148;
 s.shapes.add({geometry:'ellipse',name:`node-${i}`,position:{left:cx-13,top:386,width:26,height:26},fill:'#FFFFFF',line:{fill:C.red,width:6}});
 const top=i%2===0;
 const dateY=top?278:428,titleY=top?323:473,resultY=top?359:509;
 txt(`date-${i}`,date,x,dateY,296,43,31,C.red,true,F,'center');
 txt(`event-${i}`,title,x-60,titleY,416,35,24,C.ink,true,F,'center');
 txt(`result-${i}`,result,x-64,resultY,424,36,24,C.muted,false,F,'center');
});

txt('examples-heading','具体故障：Agent 实际会收到什么',64,578,1180,49,33,C.ink,true);
txt('examples-note','真实片段与简化示意分别标注',1316,589,540,34,23,C.muted,false,F,'right');
const cards=[64,518,972,1426];
cards.forEach((x,i)=>rect(`example-bg-${i}`,x,642,430,250,C.panel));
function cardTitle(i,title,source){const x=cards[i]+22;txt(`example-title-${i}`,title,x,659,386,37,27,C.red,true);txt(`example-source-${i}`,source,x,700,386,28,19,C.muted);}
cardTitle(0,'① XML 泄漏与冗余 role','真实 SSE 拼接节选 [A]');
txt('xml-channel','delta.content：',86,738,380,30,23,C.ink,false,M);
txt('xml-leak','function=bash> ...\n</tool_call>\n\\nuser\\n<t',86,767,382,83,23,C.red,false,M);
txt('xml-fix','修复后：工具结构化，无 role 尾流',86,855,386,30,21,C.green,true);

cardTitle(1,'② tool delta 后继续正文','按实测顺序简化，不是逐字日志 [B]');
txt('tool-then-content','delta.tool_calls = [...]\ndelta.content = "..."\nfinish_reason = "tool_calls"',540,742,386,93,23,C.red,false,M);
txt('tool-content-impact','工具已开始，状态机却又生成正文',540,836,386,30,22);
txt('tool-content-fix','修复后：沿同一回合续写，只终止一次',540,866,386,30,21,C.green,true);

cardTitle(2,'③ 参数 JSON 未闭合','真实响应字段节选 [C]');
txt('json-example','name: "write"\narguments: "{"\nfinish_reason: "tool_calls"',994,742,386,93,23,C.red,false,M);
txt('json-impact','只有左括号，却已经报告工具完成',994,836,386,30,22);
txt('json-fix','修复后：真实函数闭合才发送完整参数',994,866,386,30,21,C.green,true);

cardTitle(3,'④ 思考结束时吞掉正常尾句','真实 mixed-terminal chunk [D]');
txt('reason-prefix','已有正文：Let me try a',1448,738,386,30,23);
txt('reason-tail','" different approach.\\n"',1448,772,386,30,23,C.red,false,M);
txt('reason-id','同批末尾 ID = 248069',1448,807,386,30,23);
txt('reason-fix','修复前丢弃尾句，修复后完整保留',1448,855,386,30,21,C.green,true);

line('metrics-rule',64,920,1792,0);
const metrics=[
 ['0 / 120','工具流协议异常','Vanilla / DTB 各 60 条'],
 ['60 / 60','DTB 思考正文保真','48 个混合 chunk，344 字符保留'],
 ['3 份补丁','固定 vLLM 版本交付','serving / tool / reasoning'],
 ['兼容 MTP','无需关闭投机解码','历史真实 MTP 回放覆盖同批收尾'],
];
metrics.forEach(([v,label,detail],i)=>{const x=cards[i];txt(`metric-${i}`,v,x,934,420,56,40,C.red,true);txt(`metric-label-${i}`,label,x,985,420,34,25,C.ink,true);txt(`metric-detail-${i}`,detail,x,1019,420,29,21,C.muted);});
txt('evidence-boundary','数据范围截至 9/4。不同轮次、分母与故障类型分别统计，不构成统一失败率曲线。协议通过与任务质量、时延收益分别验收。',64,1052,1792,24,17,C.muted);

const notes=await fs.readFile(path.join(out,'工程复盘时间线_报错样例.md'),'utf8');
s.speakerNotes.textFrame.setText(notes);
const candidate=path.join(build,'candidate.pptx');
await(await PresentationFile.exportPptx(p)).save(candidate);
const draft=await p.export({slide:s,format:'png',scale:1});
await fs.writeFile(path.join(build,'draft.png'),new Uint8Array(await draft.arrayBuffer()));
const layout=await s.export({format:'layout'});
await fs.writeFile(path.join(build,'layout.json'),await layout.text());
const checked=path.join(build,'final/timeline.pptx');
const result=await finalizePresentation({workspaceDir:root,candidatePath:candidate,finalPath:checked,
 pythonExecutable:process.env.RUNTIME_PYTHON,
 integrityValidatorPath:path.join(skill,'container_tools/inspect_presentation_package_integrity.py'),
 layoutValidatorPath:path.join(skill,'container_tools/inspect_presentation_layout_geometry.py'),
 explicitTotalSlideCount:1,requiredNativeTableOwnerSlides:[],requiredNativeChartOwnerSlides:[],
 layoutArgs:['--expected-slide-size-emu','18288000,10287000','--validate-bullet-geometry','--validate-heading-fit'],
 fontPolicy:{basis:'design',families:[F,M]},verifyArtifactToolImport:true,
 receiptPath:path.join(build,'validation.json')});
const loaded=await PresentationFile.importPptx(await FileBlob.load(checked));
const preview=await loaded.export({slide:loaded.slides.getItem(0),format:'png',scale:1});
await fs.writeFile(path.join(out,'工程复盘时间线_含报错示例.png'),new Uint8Array(await preview.arrayBuffer()));
await fs.copyFile(checked,path.join(out,'工程复盘时间线_含报错示例.pptx'));
console.log(JSON.stringify({slides:result.firstPartyImport.slideCount,integrity:result.packageIntegrity.status,layoutFindings:result.presentationLayout.findingCount,sha256:result.finalSha256}));
