// Native charts retain raw token values; two panels provide an explicit scale change.
const F='Microsoft YaHei';
const C={ink:'#142C3D',teal:'#087F7C',muted:'#536B7C',gray:'#CDD8DE',gold:'#B16A16'};
function text(s,value,x,y,w,h,size=26,color=C.ink,bold=false){
 const t=s.shapes.add({geometry:'textbox',name:value.slice(0,45),position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});
 t.text=value;t.text.style={typeface:F,fontSize:size,color,bold,autoFit:'none'};return t;
}
export function scatter(s,data,pos,zoom=false){
 const ranked=data.vanilla_ranked_requests,short=ranked.filter(r=>r.reasoning_tokens<850),long=ranked.filter(r=>r.reasoning_tokens>850);
 if(short.length!==380||long.length!==11)throw new Error('Unexpected threshold cohorts');
 const axes={textStyle:{typeface:F,fontSize:20,fill:C.muted},line:{fill:C.gray,width:1}};
 let series;
 if(!zoom){
  series=[
   {name:'阈值以下（380条）',xValues:short.map(r=>r.rank),values:short.map(r=>r.reasoning_tokens),fill:'#95AAB5',line:{fill:'none',width:0},marker:{symbol:'circle',size:4}},
   {name:'阈值以上（11条）',xValues:long.map(r=>r.rank),values:long.map(r=>r.reasoning_tokens),fill:C.gold,line:{fill:'none',width:0},marker:{symbol:'circle',size:8}},
   {name:'850 token 介入阈值',xValues:[1,391],values:[850,850],line:{fill:C.gold,width:2},marker:{symbol:'none'}}
  ];
 }else{
  series=long.map((r,i)=>({name:`长尾${i+1}`,xValues:[i+1,i+1],values:[850,r.reasoning_tokens],
   line:{fill:r.role==='auxiliary'?C.gold:C.teal,width:3},fill:r.role==='auxiliary'?C.gold:C.teal,
   marker:{symbol:'none'}}));
  series.push({name:'长尾原始长度',xValues:long.map((r,i)=>i+1),values:long.map(r=>r.reasoning_tokens),
   line:{fill:'none',width:0},fill:C.gold,marker:{symbol:'circle',size:8},
   points:long.map((r,i)=>({idx:i,fill:r.role==='auxiliary'?C.gold:C.teal}))});
  series.push({name:'850 token 介入阈值',xValues:[.5,11.5],values:[850,850],line:{fill:C.gold,width:2},marker:{symbol:'none'}});
 }
 return s.charts.add('scatter',{position:pos,series,scatterOptions:{style:'lineWithMarkers'},hasLegend:false,
  legend:{textStyle:{typeface:F,fontSize:20}},
  xAxis:{...axes,min:0,max:zoom?12:400,majorUnit:zoom?2:100,numberFormatCode:'0',majorGridlines:null},
  yAxis:{...axes,min:0,max:3600,majorUnit:1000,numberFormatCode:'#,##0',majorGridlines:{fill:'#E6ECEF',width:1}},
  chartFill:'#FFFFFF',plotAreaFill:'#FFFFFF',dataLabels:{showValue:false,textStyle:{typeface:F,fontSize:20}}});
}
function chronological(s,data,pos){
 const requests=data.vanilla_chronological_requests;
 const upperHeight=Math.round(pos.height*.40),gap=16,lowerTop=pos.top+upperHeight+gap;
 const lowerHeight=pos.height-upperHeight-gap;
 const axes={textStyle:{typeface:F,fontSize:18,fill:C.muted},line:{fill:C.gray,width:1}};
 const panel=(upper)=>{
  const min=upper?850:0,max=upper?3500:850;
  const group=requests.filter(r=>upper?r.reasoning_tokens>=850:r.reasoning_tokens<850);
  const series=data.vanilla_task_blocks.slice(1).map(b=>({name:`任务 ${b.case} 开始`,xValues:[b.first_sequence-.5,b.first_sequence-.5],values:[min,max],line:{fill:'#DFE6EB',width:1},marker:{symbol:'none'}}));
  series.push({name:upper?'长思考（11条）':'短思考（380条，空心）',xValues:group.map(r=>r.sequence),values:group.map(r=>r.reasoning_tokens),
   fill:upper?C.gold:'#FFFFFF',line:{fill:'none',width:0},marker:{symbol:'circle',size:upper?8:5},
   points:group.map((r,i)=>({idx:i,fill:upper?C.gold:'#FFFFFF',line:{fill:upper?C.gold:C.muted,width:upper?1:1.2}}))});
  series.push({name:'850 token 阈值',xValues:[0,400],values:[850,850],line:{fill:C.gold,width:2},marker:{symbol:'none'}});
  return s.charts.add('scatter',{position:{left:pos.left+(upper?0:11),top:upper?pos.top:lowerTop,width:pos.width-(upper?0:11),height:upper?upperHeight:lowerHeight},series,
   scatterOptions:{style:'lineWithMarkers'},hasLegend:false,legend:{textStyle:{typeface:F,fontSize:18}},
   xAxis:{...axes,min:0,max:400,majorUnit:100,numberFormatCode:upper?';;;':'0',majorGridlines:null},
   yAxis:{...axes,min,max,majorUnit:upper?1000:200,numberFormatCode:'0',majorGridlines:{fill:'#EDF1F4',width:1}},
   chartFill:'#FFFFFF',plotAreaFill:'#FFFFFF',dataLabels:{showValue:false,textStyle:{typeface:F,fontSize:18}}});
 };
 panel(true);panel(false);
 const plotLeft=pos.left+62,plotWidth=pos.width-76;
 data.vanilla_task_blocks.forEach(b=>text(s,String(b.case),plotLeft+((b.first_sequence+b.last_sequence)/2/400)*plotWidth-25,pos.top-29,60,30,17,C.muted));
 text(s,'最大 3,482',pos.left+82,pos.top+7,250,31,20,C.gold,true);
 text(s,'850 · 此处分段变尺度',pos.left+80,lowerTop-26,pos.width-90,28,19,C.gold,true);
}
export function drawLongTailCompact(s,data){
 text(s,'850 token 将介入集中在长尾',875,149,650,48,31,C.ink,true);
 text(s,'按发起时间枚举；空心点 = 阈值以下请求',875,199,655,38,22,C.muted);
 text(s,'reasoning token  /  顶部数字为任务编号',875,238,655,35,20,C.muted);
 chronological(s,data,{left:862,top:304,width:678,height:316});
 text(s,'请求发起序号（1–391）；纵轴上下刻度不同',875,619,665,32,20,C.muted);
 text(s,'97.19% 请求无需介入试答',875,653,665,43,28,C.teal,true);
 text(s,'2.81% 的长尾请求\n占 38.88% 的 reasoning token',875,703,665,87,31,C.teal,true);
 text(s,'阈值以上 10,913 token（20.94%）\n为潜在介入尾部，仍需试答和质量验证',875,790,665,58,21);
}
export function createLongTailFigure(Presentation,data){
 const presentation=Presentation.create({slideSize:{width:1600,height:1000}}),s=presentation.slides.add();s.background.fill='#FFFFFF';
 text(s,'850 token 将介入集中在少量长尾请求',70,42,1460,82,49,C.ink,true);
 text(s,'Vanilla-06：391 条有效请求，52,112 reasoning token；空心点显示密集短请求',74,130,1460,50,27,C.muted);
 text(s,'全部请求：按实际发起时间枚举',73,202,950,50,30,C.ink,true);
 text(s,'放大 11 条长尾\n线段为阈值以上部分',1051,202,510,76,28,C.ink,true);
 text(s,'reasoning token  /  顶部为任务编号；上段至 3,500',77,255,950,34,22,C.muted);
 chronological(s,data,{left:62,top:319,width:963,height:397});
 scatter(s,data,{left:1044,top:304,width:501,height:410},true);
 text(s,'请求发起序号（每点 1 条；上下纵轴刻度不同）',135,720,900,41,23,C.muted);
 text(s,'380 条（97.19%）低于阈值，无需触发试答',135,760,900,37,24,C.teal,true);
 text(s,'长尾内部排序\n9 条标题，2 条业务（绿色）',1049,720,510,61,22,C.muted);
 text(s,'2.81% 的长尾请求',74,810,720,56,41,C.teal,true);
 text(s,'占 38.88% 的 reasoning token',74,867,950,60,41,C.teal,true);
 text(s,'阈值以上：10,913 token',1035,810,510,56,36,C.gold,true);
 text(s,'占全部思考的 20.94%\n需要试答与质量验证的潜在优化空间',1035,870,510,68,25,C.muted);
 text(s,'数据说明：长尾总思考 20,263 token；扣除 11×850 后为 10,913。达到阈值且仍在思考才考虑介入。分布支持 850 作为候选，不证明其最优或可全部删除尾部。',74,952,1460,37,19,C.muted);
 return {presentation,slide:s};
}
