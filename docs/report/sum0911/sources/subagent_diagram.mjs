// Explicitly requested editable diagram. Box contents are source output excerpts.
const F='Microsoft YaHei';
const ink='#142C3D',teal='#087F7C',muted='#536B7C',amber='#AC6219';
function text(s,t,x,y,w,h,size=26,color=ink,bold=false){
 const z=s.shapes.add({geometry:'textbox',name:t.slice(0,50),position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});
 z.text=t;z.text.style={typeface:F,fontSize:size,color,bold,autoFit:'none'};return z;
}
function box(s,t,x,y,w,h,size=24,stroke='#CCD9DE',fill='#F6F8F9'){
 const z=s.shapes.add({geometry:'roundRect',borderRadius:20,name:'原始模型文本',position:{left:x,top:y,width:w,height:h},fill,line:{fill:stroke,width:2}});
 z.text=t;z.text.style={typeface:F,fontSize:size,color:ink,autoFit:'none',verticalAlignment:'top',insets:{top:20,left:24,right:24,bottom:18}};return z;
}
function emphasis(z,t,color){const r=z.text.get(t);r.bold=true;r.fill=color;}
function arrow(s,a,b,fromSide,toSide,color=teal){return s.shapes.connect(a,b,{fromSide,toSide,kind:'straight',line:{fill:color,width:4},tail:{type:'triangle',width:'med',length:'med'}});}
const info='Entities/Classes mentioned:\n- `SceneInfo` - has `sceneId`, `sceneCode`\n[…]\nFrom `ResponseAssemblerTest.java`:\n- `init()` (Before method)';
const repeated='Now let me look more carefully at the test\nmethod signatures from the diff:\n[…]\nFrom ResponseAssemblerTest.java:\n- `void init()` - @Before method';
const answer='"test_methods": [\n    "void init() // ResponseAssemblerTest - @Before",\n    […]\n  ]';
export function drawCompactSubagent(s){
 text(s,'case28 子 Agent：从 patch 提取设计信息',78,149,747,45,30,teal,true);
 text(s,'原始 reasoning 节选：已列方法名，随后又展开签名',78,207,740,40,23,muted);
 const thought=box(s,'From `ResponseAssemblerTest.java`:\n- `init()` (Before method)\n[…]\nNow let me look more carefully at the test\nmethod signatures from the diff:\n[…]\n- `void init()` - @Before method',78,258,730,287,23);
 emphasis(thought,'`init()`',teal);
 emphasis(thought,'Now let me look more carefully',amber);
 emphasis(thought,'method signatures',amber);
 text(s,'已列过的方法再次展开；可在最终 JSON 中整理',78,551,745,35,23,amber,true);
 const result=box(s,answer,78,695,730,140,22,teal,'#EFF8F6');
 emphasis(result,'"test_methods"',teal);
 s.shapes.connect(thought,result,{kind:'elbow',fromSide:'left',toSide:'left',line:{fill:teal,width:4},tail:{type:'triangle',width:'med',length:'med'}});
 text(s,'851 token 处介入，首 probe 接受',118,590,675,40,28,teal,true);
 text(s,'50-token 试答，.985 ≥ .93；结束思考并生成 final',118,632,675,39,23);
}
export function createSubagentFigure(Presentation,caseData){
 const p=Presentation.create({slideSize:{width:1600,height:1000}});
 const s=p.slides.add();s.background.fill='#FFFFFF';
 text(s,'子 Agent 早退：把已抽取的信息交付为 JSON',70,44,1460,72,45,ink,true);
 text(s,'case28 具体任务：从 patch 提取变更文件、实体字段和测试方法',70,124,1460,48,29,muted);
 text(s,'① 关键信息已经提取',80,205,690,45,29,teal,true);
 const first=box(s,info,80,266,690,218,25);
 emphasis(first,'`SceneInfo`',teal);emphasis(first,'`init()`',teal);
 text(s,'这些信息可以直接\n整理进目标 JSON',800,305,320,100,30,teal,true);
 text(s,'② 模型仍在继续展开',80,536,690,45,29,amber,true);
 const again=box(s,repeated,80,595,690,220,25,'#D9BE9F','#FCF8F2');
 emphasis(again,'Now let me look more carefully',amber);emphasis(again,'`void init()`',amber);
 arrow(s,first,again,'bottom','top','#AEBCC5');
 text(s,'同一方法再次写成完整签名',450,494,610,40,25,amber);
 text(s,'③ 接受后，生成最终答案',1090,451,445,50,28,teal,true);
 const result=box(s,answer,1090,520,445,295,25,teal,'#EFF8F6');
 emphasis(result,'"test_methods"',teal);
 // Model state transition, explicitly requested as a diagram.
 s.shapes.connect(again,result,{kind:'elbow',fromSide:'right',toSide:'left',line:{fill:teal,width:5},tail:{type:'triangle',width:'lg',length:'lg'}});
 text(s,'DTB 介入',802,481,253,48,33,teal,true);
 text(s,'851 token\n首 probe 接受\n.985006 ≥ .93',802,535,253,112,25,teal,true);
 text(s,'结束本轮思考\n重新生成 final',802,753,255,79,24,ink);
 text(s,'批注：已有信息重复展开，可转入结构化交付；不将整段思考都判为冗余',80,850,1450,45,25,amber);
 text(s,'框中为模型原始输出节选，[…] 表示省略；框外为分析。箭头表示实测阶段切换，不展示未生成的反事实思考。\nrun-06 request '+caseData.request_id+'；任务最终 12/12，但未逐字段裁决。对应 Vanilla 思考更短，本图不标净压缩率。',80,912,1450,70,20,muted);
 s.speakerNotes.textFrame.setText('来源：docs/report/sum0911/技术博客.md §4.1。原始full_log第一行；phase索引见sources/evidence.json subagent_case。旧版open trial，gate=.93。试答50token未保存正文，final不是trial原样复用。');
 return {presentation:p,slide:s};
}
