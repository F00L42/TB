import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const here=path.dirname(fileURLToPath(import.meta.url));
const out=path.dirname(here);
const runtime='C:/Users/F-O-O/.cache/codex-runtimes/codex-primary-runtime/dependencies';
process.env.RUNTIME_NODE_MODULES=path.join(runtime,'node/node_modules');
process.env.RUNTIME_NODE=path.join(runtime,'node/bin/node.exe');
process.env.RUNTIME_PYTHON=path.join(runtime,'python/python.exe');
const skill='C:/Users/F-O-O/.codex/plugins/cache/openai-primary-runtime/presentations/26.909.12148/skills/presentations';
const {marked}=await import(pathToFileURL(path.join(runtime,'node/node_modules/marked/lib/marked.esm.js')));
const css=`:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f5f7f8;color:#142c3d;font:17px/1.85 'Microsoft YaHei','Noto Sans CJK SC',sans-serif}main{max-width:1060px;margin:40px auto;background:white;padding:60px 70px}h1{font-size:35px;line-height:1.45;letter-spacing:-.5px;margin:0 0 28px}h2{font-size:26px;margin:52px 0 18px;color:#087f7c;border-bottom:1px solid #dbe4e8;padding-bottom:10px}h3{font-size:20px;margin-top:30px}p{margin:16px 0}a{color:#087f7c;text-underline-offset:3px}strong{font-weight:700}table{width:100%;border-collapse:collapse;margin:25px 0;font-size:14px;line-height:1.65}th{background:#edf3f5;text-align:left}th,td{padding:11px 13px;border-bottom:1px solid #dbe4e8;vertical-align:top}code{font-family:Consolas,monospace;font-size:.88em;overflow-wrap:anywhere}pre{white-space:pre-wrap;background:#f2f5f7;padding:20px}li{margin:9px 0}nav{font-size:14px;margin-bottom:26px;color:#687985}.dist{margin:26px 0;padding:22px 0}.dist p{margin:4px 0}.bar{height:32px;display:flex;background:#dbe4e8;margin:8px 0 15px}.bar span{background:#087f7c}.small{font-size:14px;color:#526976}@media(max-width:700px){main{margin:0;padding:25px 18px}h1{font-size:27px}table{display:block;overflow:auto}}@media print{body{background:white}main{margin:0;padding:0;max-width:none}h2,h3{break-after:avoid}tr{break-inside:avoid}a{color:inherit}}`;
for(const stem of ['邮件正文']){
 let md=await fs.readFile(path.join(out,stem+'.md'),'utf8');
 let body=marked.parse(md);
 await fs.writeFile(path.join(out,stem+'.html'),`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${stem} · DTB 阶段性汇报</title><style>${css}</style><main><nav>DTB 阶段性汇报　2026.09　<a href="技术博客.md">技术博客</a>　<a href="邮件正文.html">邮件正文</a>　<a href="DTB阶段性汇报.pptx">展示 PPT</a></nav>${body}</main></html>`,'utf8');
}

if(process.argv.includes('--html-only')) process.exit(0);

// The deck is generated exclusively from the blog narrative and its source snapshot.
const {Presentation,PresentationFile,FileBlob}=await import('@oai/artifact-tool');
const {applyPresentationChartFont,finalizePresentation}=await import(pathToFileURL(path.join(skill,'container_tools/artifact_tool_utils.mjs')));
const data=JSON.parse(await fs.readFile(path.join(here,'evidence.json'),'utf8'));
const build=path.join(path.dirname(out),'.sum0911-build');
await fs.mkdir(build,{recursive:true});
await fs.writeFile(path.join(build,'.gitignore'),'*\n');
const pres=Presentation.create({slideSize:{width:1600,height:900}});
const F='Microsoft YaHei';
const C={ink:'#142C3D',teal:'#087F7C',muted:'#536B7C',gray:'#CDD8DE',white:'#FFFFFF'};
function txt(s,text,x,y,w,h,size=30,color=C.ink,bold=false){
 const z=s.shapes.add({geometry:'textbox',name:text.slice(0,40),position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});
 z.text=text; z.text.style={typeface:F,fontSize:size,color,bold,autoFit:'none'}; return z;
}
function page(title,n){const s=pres.slides.add();s.background.fill=C.white;txt(s,title,76,48,1435,80,48,C.ink,true);txt(s,String(n).padStart(2,'0'),1460,835,60,32,20,C.muted);return s;}
function note(s,t){s.speakerNotes.textFrame.setText(t+'\n直接信息来源：docs/report/sum0911/技术博客.md。更新日期 2026-09-14。');}
function chart(s,categories,series,pos,opts={}){
 const ch=s.charts.add('bar',{position:pos,categories,series,
  barOptions:{direction:'column',grouping:'clustered',gapWidth:65,...opts.barOptions},
  hasLegend:opts.legend??false,legend:{position:'bottom',textStyle:{typeface:F,fontSize:21,fill:C.muted}},
  xAxis:{textStyle:{typeface:F,fontSize:22,fill:C.ink},line:{fill:C.gray,width:1},...opts.xAxis},
  yAxis:{visible:true,min:0,textStyle:{typeface:F,fontSize:18,fill:C.muted},majorGridlines:{fill:'#E6ECEF',width:1},...opts.yAxis},
  dataLabels:{showValue:true,position:'outEnd',textStyle:{typeface:F,fontSize:23,fill:C.ink},...opts.dataLabels},
  chartFill:'#FFFFFF',plotAreaFill:'#FFFFFF'});
 applyPresentationChartFont(ch,{fontFamily:F});return ch;
}
// 1. Requested overview, no separate cover.
{
 const s=page('DTB：Agent 工程兼容与思考压缩的阶段进展',1);
 txt(s,'Qwen3.6-27B，9 个完整 Agent 任务',78,140,1350,45,28,C.muted);
 txt(s,'01  工程兼容',78,234,360,55,32,C.teal,true);
 txt(s,'内部多阶段生成保持一个完整回合\n修复工具流、停止边界与思考尾段',460,230,1020,105,32);
 txt(s,'02  优化效果',78,386,360,55,32,C.teal,true);
 txt(s,'4/9 持平',460,369,400,76,58,C.ink,true);
 txt(s,'最终 DT 全通过任务数',460,451,420,44,25,C.muted);
 txt(s,'−11.57%',930,369,480,76,58,C.teal,true);
 txt(s,'累计 LLM 端到端耗时',930,451,520,44,25,C.muted);
 txt(s,'可见 reasoning token 减少 23.70%',460,517,1000,45,30);
 txt(s,'03  泛化方向',78,631,360,55,32,C.teal,true);
 txt(s,'面向更多存在冗余思考的模型\n协议适配与参数重标定后，验证质量和净时延',460,620,1040,100,30);
 txt(s,'实测主比较：Vanilla-06 / DTB run-08。4/9 含 Vanilla 缺报告任务的原始测试证据，\n总通过率持平不代表逐任务非劣。跨模型收益尚待验证。',78,772,1350,65,21,C.muted);
 note(s,'博客 §1、§3、§6。工程定向验证为 120 条响应工具协议零已检异常、60 条 DTB reasoning 保真。全任务 4/9 是显式原始测试口径：Vanilla case35 缺报告但 raw 36/36；case36 由4/4退3/4，case2新增6/6。尚无其他模型净收益证据。');
}
// 2. Flat engineering narrative with bounded validation.
{
 const s=page('工程优化：内部切换保持原生输出契约',2);
 txt(s,'核心约束：可以缩短思考，工具调用与 assistant 回合必须完整',78,140,1400,55,30,C.teal,true);
 const rows=[
  ['回合边界错位','统一 think-end 与 tool-start 状态迁移\n保留源 terminal，避免重新生成和 role 尾流'],
  ['工具前缀与 JSON 丢失','对齐 text / token IDs，补齐合批收尾\n函数闭合后生成 canonical JSON，连续解析多工具'],
  ['停止误伤与思考丢字','结构 Token ID 区分边界与字面量\nfinal 恢复原 stop，reasoning parser 保留边界前正文']
 ];
 rows.forEach((r,i)=>{let y=242+i*135;txt(s,r[0],78,y,430,80,33,C.ink,true);txt(s,r[1],545,y,935,100,29);});
 txt(s,'120 条响应',78,673,380,62,44,C.teal,true);
 txt(s,'未检出工具流协议异常',78,739,480,45,25);
 txt(s,'60 / 60 条 DTB',610,673,500,62,44,C.teal,true);
 txt(s,'公开思考正文保真',610,739,470,45,25);
 txt(s,'支持 MTP',1180,673,335,62,44,C.teal,true);
 txt(s,'兼容方案无需关闭投机解码',1120,739,410,45,23);
 txt(s,'定向回放覆盖固定历史上下文与短思考。协议通过与模型语义、任务质量分别验收。',78,826,1360,40,21,C.muted);
 note(s,'博客 §2。源为原工程博客、replay2 RESULT。replay2 Vanilla/DTB 各60条，DTB均短思考未触发probe；48个混合terminal chunk的344字符保留。历史真实MTP回放验证同批参数收尾，最新回放未重录完整MTP启动身份，不作新的MTP性能结论。三补丁固定 vLLM v0.23.0。');
}
// 3. Native chart with actual values; secondary outcomes remain text.
{
 const s=page('累计 LLM 耗时下降 11.57%，总通过率持平',3);
 txt(s,'Vanilla run-06 对 DTB run-08，全部 9 任务',78,140,1380,45,28,C.muted);
 txt(s,'累计 LLM E2E / 秒',88,221,760,50,29,C.ink,true);
 const v=data.variants.vanilla.totals,d=data.variants.run08.totals;
 chart(s,['Vanilla-06','DTB run-08'],[{name:'累计 LLM E2E / 秒',values:[+v.llm_e2e_s.toFixed(3),+d.llm_e2e_s.toFixed(3)],fill:C.gray,points:[{idx:1,fill:C.teal}],valuesFormatCode:'#,##0.000'}],{left:78,top:285,width:795,height:440},{yAxis:{max:3500,majorUnit:1000}});
 txt(s,'−335.758 秒',935,228,575,77,52,C.teal,true);
 txt(s,'6 / 9 个任务的累计 LLM 耗时更短',940,316,575,65,27);
 txt(s,'4/9 与 4/9',940,416,550,65,44,C.ink,true);
 txt(s,'最终 DT 全通过任务数',940,482,550,50,26,C.muted);
 txt(s,'52,112 降至 39,759',940,570,590,55,36,C.teal,true);
 txt(s,'可见 reasoning token，减少 23.70%',940,633,590,50,26);
 txt(s,'平台执行时间合计减少 3.95%，创建到结束合计减少 2.17%。',78,754,1420,45,27);
 txt(s,'4/9 含 Vanilla case35 原始 36/36，仍缺报告。case36 退化、case2 改善。LLM 耗时含标题和恢复请求。\n历史基线、未固定 seed，属实测差异；reasoning 不含隐藏试答，run-08 无 phase 账本。',78,813,1360,68,20,C.muted);
 note(s,'博客 §3。Vanilla 2902.454646秒，run08 2566.696963秒；差335.757683秒。任务初始输入hash相同，后续Agent轨迹不同，不按第N轮配对。各请求E2E之和不是任务wall time。平台start-end 4299.173→4129.363秒，create-end 5201.631→5088.942秒。请求392→350，response completion263947→237105。run08 350请求474工具调用未检出已知协议错误，有一次合法JSON但task_id运行时拒绝后恢复。');
}
// 4. Source reasoning with adjacent analysis, plus non-overlapping length bins.
{
 const s=page('已能汇报测试事实，DTB 停止后续重复推敲',4);
 txt(s,'case37：原始思考文本与批注',78,149,730,48,31,C.teal,true);
 txt(s,'原文节选；[…] 为省略，彩色文字为分析标注',78,198,735,35,21,C.muted);
 function quote(text,x,y,w,h,emphasis,color=C.teal){
  const z=txt(s,text,x,y,w,h,25,C.ink);
  for(const phrase of emphasis){const r=z.text.get(phrase);r.bold=true;r.fill=color;}
  return z;
 }
 quote('I see the results:\n- Tests run: 3\n- Failures: 1 […]\n- 2 tests passed: […]',78,250,455,151,['Tests run: 3','Failures: 1','2 tests passed']);
 txt(s,'① 汇报事实已齐备',550,259,285,40,25,C.teal,true);
 txt(s,'3 条测试，1 条失败\n2 条通过',550,307,285,78,23,C.muted);
 txt(s,'[省略原文中重列的 31 项索引]',78,402,475,31,20,C.muted);
 quote("Let me count:\nThat's 31 indices total",78,438,455,80,['Let me count:','31 indices total'],'#AC6219');
 txt(s,'② 再次计数',550,437,275,38,25,'#AC6219',true);
 txt(s,'没有新增工具证据',550,480,285,39,23,C.muted);
 quote("But then the count should be 34,\nnot 32 […]\nI'm getting 31 items total […]",78,538,455,110,['34','32','31 items total'],'#AC6219');
 txt(s,'③ 继续反复推敲',550,544,285,38,25,'#AC6219',true);
 txt(s,'31 / 34 / 32 来回计数\n根因尚未证实',550,588,285,76,23,C.muted);
 txt(s,'850 token 介入，首 probe 接受',78,693,760,48,31,C.teal,true);
 txt(s,'50-token 试答 .904 ≥ .90，转入 final\n保留 2/3 测试事实，继续创建报告目录',78,748,755,70,24);

 txt(s,'思考长度分布与 token 成本',875,149,650,48,31,C.ink,true);
 txt(s,'Vanilla-06，391 条有效请求，52,112 reasoning token',875,199,655,55,22,C.muted);
 const bins=data.vanilla_distribution_bins;
 chart(s,bins.map(b=>b.label),[
  {name:'请求占比',values:bins.map(b=>+b.request_share.toFixed(8)),fill:C.gray,valuesFormatCode:'0.00%'},
  {name:'reasoning token 占比',values:bins.map(b=>+b.token_share.toFixed(8)),fill:C.teal,valuesFormatCode:'0.00%'}
 ],{left:859,top:268,width:681,height:351},{legend:true,barOptions:{gapWidth:65},
  yAxis:{max:.65,majorUnit:.2,numberFormatCode:'0%'},
  xAxis:{textStyle:{typeface:F,fontSize:23,fill:C.ink}},
  dataLabels:{showValue:true,position:'outEnd',textStyle:{typeface:F,fontSize:20,fill:C.ink,bold:true}}});
 txt(s,'分档单位：token；请求数依次为 128 / 224 / 28 / 11',879,617,652,36,21,C.muted);
 txt(s,'2.81% 的长尾请求\n占 38.88% 的 reasoning token',875,666,670,105,37,C.teal,true);
 txt(s,'短请求原生完成，长请求才试答\n独立 closed 账本：97.25%（354/364）同 child 完成',875,773,665,65,22);
 txt(s,'案例来自 run-06-closed，分析针对汇报子目标，试答分数不是正确概率。11 条长尾中 9 条为标题；closed 旁路比例不代表 run-08。',78,851,1345,35,18,C.muted);
 note(s,'博客 §4.2、§5.1。原始reasoning片段由full-log第20行逐字核验，仅通过[…]省略与换行排版，不展示未保存的hidden trial全文。片段1在开头提取3 tests、1 failure、2 passed，标注只表示汇报事实充分；片段2在完整重列31项索引后再计数；片段3在34/32/31间反复推敲，根因未证实。案例request0480aca87728475f，parent chatcmpl-a385fe220c683c95。850-token native后50-token probe以.904024首次接受，再fresh-final174token并创建报告目录。分档互斥：0–20/21–300/301–850/>850，包含0-token请求。请求数128/224/28/11，reasoning token1976/16210/13663/20263，总数391与52112。分档不是累计阈值；长尾20,263是完整reasoning，不全为可删除冗余。阈值后10913仍须扣除必要推理与控制成本。closed354/364同child，仅10请求介入；该phase run全量慢4.26%，不冒充主性能组。原始来源和字符位置见sources/evidence.json。');
}

const revision=Date.now();
const candidate=path.join(build,`candidate-${revision}.pptx`);
await(await PresentationFile.exportPptx(pres)).save(candidate);
for(let i=0;i<4;i++){
 const slide=pres.slides.items[i];
 const png=await pres.export({slide,format:'png',scale:1});
 await fs.writeFile(path.join(build,`slide-${i+1}.png`),new Uint8Array(await png.arrayBuffer()));
 const layout=await slide.export({format:'layout'});
 await fs.writeFile(path.join(build,`slide-${i+1}.layout.json`),await layout.text());
}
const checkedPath=path.join(build,'release',`validated-${revision}.pptx`);
await fs.mkdir(path.dirname(checkedPath),{recursive:true});
await finalizePresentation({workspaceDir:path.dirname(out),candidatePath:candidate,finalPath:checkedPath,
 pythonExecutable:path.join(runtime,'python/python.exe'),
 integrityValidatorPath:path.join(skill,'container_tools/inspect_presentation_package_integrity.py'),
 layoutValidatorPath:path.join(skill,'container_tools/inspect_presentation_layout_geometry.py'),
 layoutArgs:['--expected-slide-size-emu','15240000,8572500','--validate-bullet-geometry','--validate-heading-fit'],
 explicitTotalSlideCount:4,requiredNativeChartOwnerSlides:[3,4],requiredNativeTableOwnerSlides:[],
 materializeLiteralChartWorkbooks:true,fontPolicy:{basis:'design',families:[F]},
 verifyArtifactToolImport:true,receiptPath:path.join(build,`validation-${revision}.json`)});
await fs.copyFile(path.join(build,`validation-${revision}.json`),path.join(build,'validation.json'));
const outputPath=path.join(out,'DTB阶段性汇报.pptx');
try { await fs.copyFile(outputPath,path.join(build,`before-${revision}.pptx`)); } catch(e) { if(e.code!=='ENOENT') throw e; }
await fs.copyFile(checkedPath,outputPath);
const finalDeck=await PresentationFile.importPptx(await FileBlob.load(outputPath));
await fs.mkdir(path.join(out,'预览'),{recursive:true});
for(let i=0;i<4;i++){
 const png=await finalDeck.export({slide:finalDeck.slides.items[i],format:'png',scale:1});
 await fs.writeFile(path.join(out,'预览',`${String(i+1).padStart(2,'0')}.png`),new Uint8Array(await png.arrayBuffer()));
}
console.log('Report HTML, four-slide deck, and editable data charts generated.');
