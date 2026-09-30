// Build the five-slide closeout deck from the frozen evidence beside this file.
// Requires the bundled @oai/artifact-tool runtime. No model/server calls.
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';

const here = path.dirname(fileURLToPath(import.meta.url));
const report = path.dirname(here);
const root = path.resolve(report, '../../..');
const runtime = process.env.RUNTIME_ROOT ?? path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies');
const skill = process.env.PRESENTATIONS_SKILL ?? path.join(os.homedir(), '.codex/plugins/cache/openai-primary-runtime/presentations/26.909.12148/skills/presentations');
process.env.RUNTIME_NODE_MODULES = path.join(runtime, 'node/node_modules');
process.env.RUNTIME_NODE = path.join(runtime, 'node/bin/node.exe');
process.env.RUNTIME_PYTHON = path.join(runtime, 'python/python.exe');
const { Presentation, PresentationFile } = await import(pathToFileURL(path.join(process.env.RUNTIME_NODE_MODULES, '@oai/artifact-tool/dist/artifact_tool.mjs')));
const { finalizePresentation, applyPresentationChartFont } = await import(pathToFileURL(path.join(skill, 'container_tools/artifact_tool_utils.mjs')));
const build = path.join(root, 'artifacts/closeout-build/20260930');
const output = path.join(report, 'presentation');
const revision = process.argv[2] ?? 'r1';
if (!/^r\d+$/.test(revision)) throw new Error('Revision must be r1, r2, ...');
await fs.mkdir(build, { recursive: true });
await fs.mkdir(output, { recursive: true });
const finalPath = path.join(output, `DTB_closeout_20260930_${revision}.pptx`);
try { await fs.access(finalPath); throw new Error(`Output already exists: ${finalPath}`); }
catch (e) { if (e.code !== 'ENOENT') throw e; }

const evidence = path.join(report, 'evidence');
const readJson = async name => JSON.parse(await fs.readFile(path.join(evidence, name), 'utf8'));
const data = await readJson('display_data.json');
const manifest = await readJson('source_manifest.json');
const sourceDoc = await fs.readFile(path.join(report, 'sources.md'), 'utf8');
const readCsv = async name => {
  const lines = (await fs.readFile(path.join(evidence, name), 'utf8')).trim().split('\n');
  const columns = lines.shift().split(',');
  return lines.map(line => Object.fromEntries(line.split(',').map((v, i) => [columns[i], v])));
};
const dev = await readCsv('development_distribution.csv');
const noe = await readCsv('noemate_distribution.csv');
const start = Date.parse(noe[0].started_at_utc);
const devPoints = dev.map(r => ({ x: Number(r.sequence), y: Number(r.reasoning_tokens) }));
const noePoints = noe.map(r => ({ x: Number(((Date.parse(r.started_at_utc) - start) / 3600000).toFixed(8)), y: Number(r.reasoning_tokens) }));
if (devPoints.length !== 391 || noePoints.length !== 8277) throw new Error('Distribution count mismatch');
const noemateBefore = 24.794401, noemateAfter = 18.603930;
const devBefore = data.development.totals.vanilla.llm_e2e_s;
const devAfter = data.development.totals.run08.llm_e2e_s;
const pct = (a, b) => ((1 - b / a) * 100).toFixed(2);

const F = 'Microsoft YaHei';
const C = { ink: '#142C3D', muted: '#536B7C', gray: '#BCCBD3', teal: '#087F7C', gold: '#B16A16', grid: '#E8EFF2', light: '#F2F7F8', warm: '#FFF4E4' };
const p = Presentation.create({ slideSize: { width: 1600, height: 900 } });
const t = (s, text, x, y, w, h, size = 28, color = C.ink, bold = false, align = 'left') => {
  const shape = s.shapes.add({ geometry: 'textbox', name: text.slice(0, 70), position: { left: x, top: y, width: w, height: h }, fill: 'none', line: { fill: 'none', width: 0 } });
  shape.text = text;
  shape.text.style = { typeface: F, fontSize: size, color, bold, alignment: align, verticalAlignment: 'middle', autoFit: 'none', insets: { left: 0, right: 0, top: 0, bottom: 0 } };
  return shape;
};
const rule = (s, x, y, w, color = C.grid, thickness = 1) => s.shapes.add({ geometry: 'line', position: { left: x, top: y, width: w, height: 0 }, fill: 'none', line: { fill: color, width: thickness } });
const node = (s, text, x, y, w, h, emphasis = false, size = 27) => {
  const shape = s.shapes.add({ geometry: 'rect', name: text, position: { left: x, top: y, width: w, height: h }, fill: emphasis ? C.warm : C.light, line: { fill: emphasis ? C.gold : '#CEDCE3', width: emphasis ? 2 : 1 } });
  shape.text = text;
  shape.text.style = { typeface: F, fontSize: size, color: emphasis ? C.gold : C.ink, bold: emphasis, alignment: 'center', verticalAlignment: 'middle', autoFit: 'none', insets: { left: 8, right: 8, top: 4, bottom: 4 } };
  return shape;
};
const arrow = (s, a, b, options = {}) => s.shapes.connect(a, b, { kind: 'straight', fromSide: 'right', toSide: 'left', line: { fill: C.muted, width: 2 }, tail: { type: 'triangle', width: 'sm', length: 'sm' }, ...options });
function slide(title, subtitle, n, foot) {
  const s = p.slides.add(); s.background.fill = '#FFFFFF';
  t(s, title, 72, 44, 1456, 74, 48, C.ink, true);
  if (subtitle) t(s, subtitle, 74, 127, 1450, 45, 28, C.muted);
  rule(s, 74, 829, 1452);
  if (foot) t(s, foot, 74, 842, 1390, 34, 18, C.muted);
  t(s, String(n).padStart(2, '0'), 1478, 842, 48, 34, 19, C.muted, false, 'right');
  return s;
}
function notes(s, text, ids) {
  const refs = ids.map(id => {
    const source = manifest.sources[id];
    if (source) return `${id}: ${source.url ?? source.path}\ncommit: ${source.commit ?? 'user reference'}; SHA-256: ${source.sha256}`;
    const line = sourceDoc.split('\n').find(line => line.startsWith(`| ${id} |`));
    if (!line) throw new Error(`Missing source: ${id}`);
    return line;
  });
  s.speakerNotes.textFrame.setText(`${text}\n\n引用来源\n${refs.join('\n\n')}\n\n详细口径及反例：docs/report/closeout/outlines.md；本地精简证据：docs/report/closeout/evidence/。`);
}
function scatter(s, points, threshold, xMax, yMax, box, markerSize) {
  const low = points.filter(r => r.y <= threshold), high = points.filter(r => r.y > threshold);
  const axis = { textStyle: { typeface: F, fontSize: 19, fill: C.muted }, line: { fill: '#CDD8DE', width: 1 } };
  const chart = s.charts.add('scatter', {
    position: box,
    series: [
      { name: `≤${threshold} token`, xValues: low.map(r => r.x), values: low.map(r => r.y), fill: C.gray, line: { fill: 'none', width: 0 }, marker: { symbol: 'circle', size: Math.round(markerSize) } },
      { name: `>${threshold} token`, xValues: high.map(r => r.x), values: high.map(r => r.y), fill: C.gold, line: { fill: 'none', width: 0 }, marker: { symbol: 'circle', size: Math.round(markerSize + 1.5) } },
      { name: `${threshold} token 介入参考`, xValues: [0, xMax], values: [threshold, threshold], line: { fill: C.gold, width: 1.4 }, marker: { symbol: 'none' } },
    ],
    scatterOptions: { style: 'lineWithMarkers' }, hasLegend: false,
    xAxis: { ...axis, min: 0, max: xMax, majorUnit: xMax > 100 ? 100 : 3, numberFormatCode: '0', majorGridlines: null },
    yAxis: { ...axis, min: 0, max: yMax, majorUnit: yMax === 3500 ? 1000 : 500, numberFormatCode: '#,##0', majorGridlines: { fill: C.grid, width: 1 } },
    dataLabels: { showValue: false, textStyle: { typeface: F, fontSize: 18 } }, chartFill: '#FFFFFF', plotAreaFill: '#FFFFFF', chartLine: { fill: 'none', width: 0 }, plotAreaLine: { fill: 'none', width: 0 },
  });
  applyPresentationChartFont(chart, { fontFamily: F });
  return chart;
}
function segmentedDevelopmentScatter(s) {
  // Two native charts retain the actual token values. The package repair sets
  // identical plot rectangles, so the ranges touch and have equal plot height.
  const chartHeight = 185;
  const plotHeight = chartHeight * 0.73;
  const axis = { textStyle: { typeface: F, fontSize: 19, fill: C.muted }, line: { fill: '#CDD8DE', width: 1 } };
  for (const upper of [true, false]) {
    const points = devPoints.filter(r => upper ? r.y > 850 : r.y <= 850);
    const series = [{ name: upper ? '研发 >850 token' : '研发 ≤850 token', xValues: points.map(r => r.x), values: points.map(r => r.y), fill: upper ? C.gold : C.gray, line: { fill: 'none', width: 0 }, marker: { symbol: 'circle', size: upper ? 6 : 4 } }];
    if (upper) series.push({ name: '850 token 分段界线', xValues: [0, 400], values: [850, 850], line: { fill: C.gold, width: 1.4 }, marker: { symbol: 'none' } });
    const chart = s.charts.add('scatter', {
      position: { left: 74, top: 220 + (upper ? 0 : plotHeight), width: 925, height: chartHeight },
      series, scatterOptions: { style: 'lineWithMarkers' }, hasLegend: false,
      xAxis: { ...axis, min: 0, max: 400, majorUnit: 100, numberFormatCode: '0', tickLabelPosition: upper ? 'none' : 'nextTo', majorGridlines: null, line: { fill: upper ? 'none' : '#CDD8DE', width: upper ? 0 : 1 } },
      yAxis: { ...axis, min: upper ? 850 : 0, max: upper ? 3500 : 850, majorUnit: upper ? 1000 : 500, numberFormatCode: '#,##0', majorGridlines: { fill: C.grid, width: 1 } },
      dataLabels: { showValue: false, textStyle: { typeface: F, fontSize: 18 } }, chartFill: 'none', plotAreaFill: 'none', chartLine: { fill: 'none', width: 0 }, plotAreaLine: { fill: 'none', width: 0 },
    });
    applyPresentationChartFont(chart, { fontFamily: F });
  }
}
function comparison(s, values, box, max, unit) {
  const chart = s.charts.add('bar', {
    position: box, categories: ['DTB', 'Vanilla'],
    series: [{ name: unit, values: [...values].reverse(), valuesFormatCode: '#,##0.00', fill: C.gray, points: [{ idx: 0, fill: C.teal, line: { fill: 'none', width: 0 } }, { idx: 1, fill: C.gray, line: { fill: 'none', width: 0 } }], line: { fill: 'none', width: 0 } }],
    barOptions: { direction: 'bar', grouping: 'clustered', gapWidth: 95 }, hasLegend: false,
    xAxis: { visible: true, textStyle: { typeface: F, fontSize: 23, fill: C.muted }, majorGridlines: null, line: { fill: 'none', width: 0 } },
    yAxis: { visible: false, min: 0, max, tickLabelPosition: 'none', majorGridlines: null, line: { fill: 'none', width: 0 } },
    dataLabels: { showValue: true, position: 'outEnd', textStyle: { typeface: F, fontSize: 25, fill: C.ink, bold: true } },
    chartFill: '#FFFFFF', plotAreaFill: '#FFFFFF', chartLine: { fill: 'none', width: 0 }, plotAreaLine: { fill: 'none', width: 0 },
  });
  applyPresentationChartFont(chart, { fontFamily: F });
  return chart;
}

// 1. Background and the algorithm, not a cover slide.
{
  const s = slide('动态思考预算控制（DTB）', '按场景设置预算，在生成过程中决定继续思考或收束为答案', 1, '目标是净 E2E 收益；试答评分用于生成决策，不等于答案正确概率');
  t(s, '背景与问题', 74, 208, 510, 46, 33, C.ink, true);
  t(s, '同一任务的思考步骤与耗时存在波动，\nAgent 多轮调用会累积这些等待。', 74, 269, 535, 85, 27);
  t(s, '信息已经足够，模型仍可能反复推敲，\n思考越长，并不意味着任务收益越大。', 74, 366, 535, 85, 27);
  rule(s, 74, 473, 527);
  t(s, '核心思想', 74, 494, 510, 43, 31, C.teal, true);
  t(s, '用预算约束思考长度，\n用生成中检查决定是否继续。', 74, 550, 535, 80, 29, C.ink, true);
  t(s, '质量与协议正确是前提，\n收益按客户端端到端耗时衡量。', 74, 650, 535, 78, 26, C.muted);
  t(s, '实现位置：vLLM 服务内部', 74, 758, 535, 41, 26, C.teal, true);

  t(s, '整体架构：场景预算与生成中检查', 668, 207, 858, 46, 32, C.ink, true);
  const query = node(s, 'Agent 请求', 670, 285, 180, 76, false, 25);
  const route = node(s, '场景识别\n与路由', 899, 285, 210, 76, false, 26);
  const budget = node(s, '预算参数\n阈值 / 步长 / 上限', 1158, 285, 367, 76, true, 25);
  const engine = node(s, 'vLLM 生成与观察', 972.5, 442, 285, 79, false, 27);
  const probe = node(s, '隐藏试答\nToken 概率评分', 990, 622, 250, 88, true, 25);
  const thinking = node(s, '继续思考', 670, 622, 206, 88, false, 27);
  const final = node(s, '引导收束\n最终生成', 1342, 622, 183, 88, false, 26);
  const parser = node(s, '原生 reasoning / tool parser 与响应输出', 924, 760, 601, 53, false, 24);
  arrow(s, query, route); arrow(s, route, budget);
  arrow(s, budget, engine, { kind: 'elbow', fromSide: 'bottom', toSide: 'top' });
  arrow(s, engine, probe, { fromSide: 'bottom', toSide: 'top' });
  t(s, '满足检查条件', 1131, 548, 145, 37, 22, C.muted);
  arrow(s, probe, thinking, { fromSide: 'left', toSide: 'right' });
  t(s, '评分不足\n且有预算', 877, 610, 109, 48, 20, C.muted, false, 'center');
  arrow(s, thinking, engine, { kind: 'elbow', fromSide: 'top', toSide: 'left', line: { fill: C.muted, width: 2, style: 'dashed' } });
  arrow(s, probe, final);
  t(s, '评分达标', 1241, 624, 101, 30, 20, C.muted, false, 'center');
  arrow(s, engine, final, { kind: 'elbow', fromSide: 'right', toSide: 'top' });
  t(s, '预算耗尽', 1327, 498, 192, 33, 22, C.muted, false, 'center');
  arrow(s, final, parser, { kind: 'elbow', fromSide: 'bottom', toSide: 'top' });
  t(s, '原生分流或自然结束\n沿原生路径完成响应', 669, 756, 246, 59, 21, C.muted);
  notes(s, '采用R01的“背景与问题、核心思想、实现方式、整体架构”紧凑组织，改写为当前服务内控制链。问题为原方案的定性动机和S13报告阶段反复计数记录，不引用缺乏同条件原始对照的“增加4倍”“提速40%”“261至419秒”。\n架构主图按职责组织：场景识别和native分流、阈值/步长/上限配置、vLLM原生生成观察、隐藏试答与token概率评分、继续思考或cue引导final、原生parser输出。预算参数是配置职责；流式观察触发后才裁决完整预算。非流式直接进入三状态，并非每请求都先观察。达到总预算可直接final，短请求同一child自然完成，显式native绕过DTB。probe接受后进入final生成，不保证复用试答全文。\n源码：request_controller.py:128–173；budget_resolver.py:62–78,157–214,336–358；deferred_observation.py:128–146,226–305；generation_states.py:401–428,531–584,613–668,758–780；response_adapter.py:73–101。difficulty/history有实现但默认关闭（budget_config.yaml:14–16），从主图省略。原图中的在线学习最优预算、信息增益、语义重复/工具循环检测、熵变化、人工接管、Planning/反思/Tool独立子预算未纳入当前主图。', ['R01', 'C01', 'C02', 'C03', 'C05', 'C06', 'C07', 'C08', 'P01', 'P03', 'P05', 'S13']);
}
// 2. Four historical engineering milestones, one simple timeline.
{
  const s = slide('流式与投机解码兼容', '接管生成后，文本、token ID、工具参数和结束事件仍需连续、合法', 2, '历史定向回放，各轮样本不同；数字仅对应所列目标问题');
  rule(s, 113, 279, 1350, '#CEDCE3', 3);
  const stages = [
    ['8/13–8/14', '输出边界', 'tool 后多出正文', '50/100', '修正后 0/100', '对齐文本与 token ID\n处理 XML 和 role 尾流'],
    ['8/17–8/19', '连续工具调用', '265 个调用', '0', '目标结构异常', '保留同批结构标记\n衔接连续工具调用'],
    ['9/1', '参数完整性', '非法参数 JSON', '133/300', '修正后 0/300', '区分生成结束\n与 parser 收尾'],
    ['9/4', '混合 chunk', '同批正常正文', '344 字符', '全部保留', '处理思考 stop\n与正常文字同批输出'],
  ];
  stages.forEach((a, i) => {
    const x = 74 + i * 370;
    t(s, a[0], x, 215, 320, 46, 29, C.muted);
    s.shapes.add({ geometry: 'ellipse', position: { left: x + 37, top: 271, width: 16, height: 16 }, fill: C.teal, line: { fill: 'none', width: 0 } });
    t(s, a[1], x, 324, 320, 55, 33, C.ink, true);
    t(s, a[2], x, 394, 320, 45, 25, C.muted);
    t(s, a[3], x, 448, 328, 75, 47, C.teal, true);
    t(s, a[4], x, 532, 320, 43, 27, C.teal, true);
    t(s, a[5], x, 623, 330, 85, 26, C.muted);
  });
  t(s, '形成 serving、Qwen tool parser、Qwen reasoning parser 三份 vLLM 0.23.0 部署补丁', 74, 759, 1450, 45, 26, C.ink, true);
  notes(s, '8/13–14：tool后content 50/100至0/100；后续前缀对齐XML泄漏13/100至0/100，但两轮另有22/100、12/100其他异常。8/17–19：180对回放中170条含工具DTB响应、265个调用，目标结构异常0；另外10条无工具响应，8条raw显示未生成工具，2条证据不足。9/1：非法JSON133/300至0/300，仍有DTB缺terminal和Vanilla空参数。9/4：60/60公开正文与pre-parser一致；48个混合chunk中的344字符全保留。不能相加为统一通过率，也不能写为当轮所有故障归零。投机解码可使结构边界与文本处在同一批输出。9/4未补齐完整MTP启动身份，不作为MTP性能验证。具体失败请求与原文均见S14。', ['S14', 'C04']);
}
// 3. The requested distribution and editable output/control pipeline.
{
  const s = slide('抓大放小：先观察，按需接管', '研发 Agent 全链路请求（含标题），Vanilla-06 共 391 条有效请求', 3, '历史长度分布不等于实际介入率或可节省比例；11 条长尾中有 9 条标题请求');
  t(s, '可见思考长度 / token', 74, 195, 620, 32, 20, C.muted);
  segmentedDevelopmentScatter(s);
  t(s, '请求发起序号', 665, 539, 332, 31, 20, C.muted, false, 'right');
  t(s, '分段等高：0–850 / 850–3500 token', 85, 539, 590, 31, 20, C.gold);
  t(s, '97.19%', 1090, 215, 415, 83, 59, C.teal, true);
  t(s, '请求思考不超过 850 token', 1090, 300, 420, 40, 26, C.muted);
  t(s, '2.81% 的长尾', 1090, 370, 420, 55, 34, C.gold, true);
  t(s, '贡献 38.88%', 1090, 432, 430, 47, 34, C.ink, true);
  t(s, '的可见思考 token', 1090, 482, 430, 39, 26, C.muted);
  t(s, '在原生 parser 之前观察内容与长度', 74, 592, 1250, 45, 30, C.ink, true);
  const nodes = [
    node(s, 'EngineCore', 74, 663, 180, 76, false, 26),
    node(s, '跨进程传输\n输出处理', 300, 663, 212, 76, false, 24),
    node(s, 'AsyncLLM\ngenerate', 558, 663, 185, 76, false, 24),
    node(s, 'DTB 观察\n按需接管', 789, 663, 196, 76, true, 26),
    node(s, '原生 parser\nSSE', 1031, 663, 193, 76, false, 24),
    node(s, 'HTTP 服务\n请求方', 1270, 663, 257, 76, false, 24),
  ];
  for (let i = 0; i < nodes.length - 1; i++) arrow(s, nodes[i], nodes[i + 1]);
  arrow(s, nodes[3], nodes[2], { kind: 'elbow', fromSide: 'bottom', toSide: 'bottom', line: { fill: C.gold, width: 2, style: 'dashed' } });
  t(s, 'abort / drain，前缀＋cue 写入新 prompt 后续写', 340, 777, 1035, 32, 23, C.gold, false, 'center');
  notes(s, '研发点集S10的vanilla_chronological_requests逐ID对照S11。392总请求中7774ec46a9f245c9无有效SSE，分布排除后391。380≤850，11>850；全部52112思考token，长尾20263，占38.883558%。11条中9标题、2业务。业务382条中仅2>850。长尾完整思考和超阈值10913均不等于可安全删除量。长度是历史本地重编码。采用850处分段线性纵轴，下半段0–850，上半段850–3500，两段绘图区等高且在850处相接。使用两个原生散点图保留原始token值，380个短点与11个长点合计391，无删除、无数值映射。各段分别线性，不跨段比较视觉距离或斜率。\n管道是vLLM0.23.0固定源码的逻辑输出路径，不代表独立进程数量。EngineCore输出经output_queue、Msgpack/ZeroMQ、EngineCoreClient AsyncMPClient接收，再由AsyncLLM.output_handler和OutputProcessor detokenize/检查stop，进入每请求RequestOutputCollector，AsyncLLM.generate yield。DTB增量观察后经stream_output/result_queue/stream_queue，交给原生chat_completion_stream_generator parser.parse_delta，序列化SSE。HTTP具体组件为FastAPI StreamingResponse与Uvicorn，客户端库不固定。\n虚线控制先进入AsyncLLM请求接口，再经EngineCoreClient到EngineCore。触发且安全接管时abort并drain，保留已接受前缀。强制final分支将answer cue及闭合标记写入内部prompt，启动新的final child，继续同一个外部HTTP响应。cue不写入用户SSE，不修改已发内容；可能有batch/在途尾部，不是任意token精确硬截断。通用路径也可能hidden probe或继续思考。短请求沿同一child完成，仍有观察成本；明确native路由则绕过DTB段。非流式不使用这个观察路径。跨进程与HTTP完整组件表及行号见sources.md。', ['S10', 'S11', 'S13', 'V01', 'C06', 'C05', 'U01', 'U02', 'U03', 'U04', 'U05', 'U06', 'U07', 'P01', 'P02', 'P03', 'P04']);
}
// 4. One background distribution and two clearly scoped comparisons.
{
  const s = slide('历史回放与研发任务的效果', 'Noemate 单请求样例与研发任务组，分别展示请求等待和累计 LLM 耗时', 4, '历史观测；178/a1 为选取样例，研发调用轨迹不同，均不代表整体质量不退化');
  t(s, 'Noemate 主 Agent 思考分布', 74, 205, 700, 47, 30, C.ink, true);
  t(s, '8,277 条有效请求', 74, 263, 700, 37, 24, C.muted);
  t(s, '可见思考长度 / token', 74, 312, 655, 29, 19, C.muted);
  scatter(s, noePoints, 750, 12.15, 2000, { left: 68, top: 349, width: 681, height: 315 }, 2.2);
  t(s, '距首请求小时数', 435, 665, 310, 31, 20, C.muted, false, 'right');
  t(s, '金线：750 token', 80, 665, 355, 31, 20, C.gold);
  t(s, '94.99% ≤750 token', 74, 725, 670, 63, 38, C.teal, true);
  t(s, 'Noemate 告警分类 178/a1', 823, 205, 650, 47, 30, C.ink, true);
  t(s, '单请求 E2E / 秒', 823, 265, 392, 36, 24, C.muted);
  t(s, `−${pct(noemateBefore, noemateAfter)}%`, 1257, 258, 268, 53, 38, C.teal, true, 'right');
  comparison(s, [noemateBefore, noemateAfter], { left: 808, top: 316, width: 715, height: 147 }, 31, '请求 E2E / 秒');
  t(s, 'final 保留 12 条告警及分类字段', 824, 467, 700, 40, 27, C.teal, true);
  rule(s, 824, 536, 700);
  t(s, '辅助研发 9 任务', 823, 564, 650, 45, 30, C.ink, true);
  t(s, '累计 LLM E2E / 秒', 823, 619, 450, 36, 24, C.muted);
  t(s, `−${pct(devBefore, devAfter)}%`, 1257, 611, 268, 53, 38, C.teal, true, 'right');
  comparison(s, [devBefore, devAfter], { left: 808, top: 668, width: 715, height: 145 }, 3700, '累计 LLM E2E / 秒');
  notes(s, 'Noemate分布：12214捕获，主Agent8306；排除29后8277。≤750为7862=94.9861%，>750为415=5.0139%；P50/95/99=207/753/1002，max1841。横轴由UTC时间差计算到小时，全部点，连续线性0–2000。采集baseline标签缺完整服务端native及权重身份，不称当前交付原生行为。分布不是收益证明。\n178/a1：24.794401秒对18.603930秒，减少24.9672%。固定同输入重复中的一次选例，off、stream、temperature0.2/top_p1/max_tokens2048/seed20260918；budget/check750，min1，stride256，probe4，prob0.95。vanilla ID dtbtool-l178-a1-vanilla-main_20260922T070000Z_long_l178_measured；DTB ID同前缀dtb。12条输入真值包含ID、名称、级别、时间、网元，严重/重要/次要各4条，DTB final字段完整。其他attempt存在遗漏、建议错误和草稿泄漏，不能推整体质量不降。正文长度亦变化，延迟变化不能全部归因于reasoning。\n研发Vanilla-06对run08：按case配对9个相同主任务输入，累计LLM E2E2902.454646秒对2566.696963秒，减少11.5681%。请求392对350，后续轨迹不同。该值是各请求metrics.e2e_ms之和，不是任务wall time。平台start-end只减少3.95%。全通过任务4/9对4/9，但通过集合变化，case36回退，case29测试范围变化，不能宣称质量持平。run08缺完整manifest与显式seed，属于历史观测。\n图表用原始未四舍五入数据计算百分比，显示2位小数。', ['S01', 'S02', 'S03', 'V02', 'S06', 'S07', 'S09', 'N178S', 'N178M', 'S10', 'S11', 'S13', 'S15']);
}
// 5. Delivery and next-use boundary, with no extra appendix slide.
{
  const s = slide('交付集成 DTB 与独立观察器 SDK', '代码与部署入口已经整理，后续可在观察器接口上扩展自定义检测算法', 5, '当前交付版本仍待目标服务器功能、真实并发、质量与 E2E 验收');
  t(s, 'vLLM 集成 DTB', 74, 225, 688, 65, 39, C.teal, true);
  t(s, '按场景路由与预算控制', 74, 326, 650, 49, 31, C.ink, true);
  t(s, '原生观察、接管与状态机\n三份 vLLM 0.23.0 部署 patch', 74, 390, 670, 99, 29);
  t(s, '通用采集与 replay 验证', 74, 535, 690, 48, 31, C.ink, true);
  t(s, 'vllm_gateway 透传与记录\n7 条样例及参数矩阵、部署手册', 74, 597, 690, 98, 29);
  t(s, '独立思维链观察器 SDK', 846, 225, 680, 65, 39, C.teal, true);
  t(s, '与 DTB 算法解耦的接入接口', 846, 326, 670, 49, 31, C.ink, true);
  t(s, '增量观察与每请求检测状态\n合法 stop / abort / drain 与 cue 续写', 846, 390, 680, 99, 29);
  t(s, '扩展方向：重复检测', 846, 535, 680, 48, 31, C.ink, true);
  t(s, '接入检测算法，再验证触发准确性\n以及停止与续写后的净收益', 846, 597, 680, 98, 29);
  t(s, 'README / AGENTS 与模块导航，帮助人和新 Agent Session 快速定位实现与验证入口', 74, 752, 1450, 49, 27, C.muted);
  notes(s, 'DTB交付分支codex/dtb-static-refactor，源码快照f5512aa35152a13a9ee164a64928674badc26ceb，已含system路由34f39983及LF修复0777aef4。入口dtb/、vllm_gateway/、patches/vllm/v0.23.0/和validation/replay/README.md。checkout仓库并不自动修改服务器已安装vLLM，需按部署手册应用整套代码和patch。C04记录541项测试、1966子测试、3patch检查，均为历史本地无模型验证，不能证明服务器质量性能。\nSDK分支codex/thinking-observer固定7ccd6b99eb7a435c2d0d1f66dce24affe0728a0d，入口thinking_observer/README.md。SDK不包含重复检测算法，TokenLimitDetector是接线示例。SDK与DTB serving patch为独立入口，不能直接叠加。停止保持已接受batch与abort尾部，不撤回已发文本。真实模型、HTTP/SSE、设备及净收益pending_remote。API转发think_budget、LightCode实现已退出交付，不恢复旧接口。', ['C01', 'C02', 'C03', 'C04', 'O01', 'O02']);
}

const draft = path.join(build, `draft-${revision}.pptx`);
const repaired = path.join(build, `markers-${revision}.pptx`);
const validatedPath = path.join(build, 'release', `DTB_closeout_20260930_${revision}.pptx`);
await fs.mkdir(path.dirname(validatedPath), { recursive: true });
await (await PresentationFile.exportPptx(p)).save(draft);
// Encode marker fills, zero-width sample lines, and equal adjoining plot areas.
execFileSync(process.env.RUNTIME_PYTHON, [path.join(here, 'repair_chart_markers.py'), draft, repaired], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
const receipt = await finalizePresentation({
  workspaceDir: build, candidatePath: repaired, finalPath: validatedPath,
  pythonExecutable: process.env.RUNTIME_PYTHON,
  integrityValidatorPath: path.join(skill, 'container_tools/inspect_presentation_package_integrity.py'),
  layoutValidatorPath: path.join(skill, 'container_tools/inspect_presentation_layout_geometry.py'),
  layoutArgs: ['--expected-slide-size-emu', '15240000,8572500', '--validate-bullet-geometry', '--validate-heading-fit'],
  explicitTotalSlideCount: 5, requiredNativeChartOwnerSlides: [3, 4], requiredNativeTableOwnerSlides: [],
  materializeLiteralChartWorkbooks: true, fontPolicy: { basis: 'design', families: [F] },
  verifyArtifactToolImport: true, receiptPath: path.join(build, `validation-${revision}.json`),
});
await fs.copyFile(validatedPath, finalPath);
await fs.writeFile(path.join(build, `deck-${revision}.json`), JSON.stringify(p.toProto()));
console.log(JSON.stringify({ output: finalPath, slides: 5, nativeCharts: 5, samplePoints: [391, 8277], receipt: path.join(build, `validation-${revision}.json`) }));
