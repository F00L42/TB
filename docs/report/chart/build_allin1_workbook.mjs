#!/usr/bin/env node

import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [sourcePathArg, outputPathArg, previewDirArg, manifestPathArg] = process.argv.slice(2);
if (!sourcePathArg || !outputPathArg || !previewDirArg) {
  throw new Error(
    "Usage: node build_allin1_workbook.mjs <source.json> <output.xlsx> <preview-dir> [manifest.json]",
  );
}

const sourcePath = path.resolve(sourcePathArg);
const outputPath = path.resolve(outputPathArg);
const previewDir = path.resolve(previewDirArg);
const manifestPath = manifestPathArg ? path.resolve(manifestPathArg) : null;
const source = JSON.parse(await fs.readFile(sourcePath, "utf8"));

const palette = source.palette;
const workbook = Workbook.create();
const allin1 = workbook.worksheets.add("ALLin1");
const pairData = workbook.worksheets.add("Pair Data");
const notes = workbook.worksheets.add("Notes");

allin1.showGridLines = false;
pairData.showGridLines = false;
notes.showGridLines = false;

const pairHeaders = [
  "场景",
  "QA_ID",
  `${source.baseline_label} E2E (s)`,
  `${source.candidate_label} E2E (s)`,
  "E2E gain (s)",
  "E2E gain (%)",
  `${source.baseline_label} 可见 Token`,
  `${source.candidate_label} 可见 Token`,
  "Token saving",
  "Token saving (%)",
  `${source.baseline_label} Judge 正确 (1/0)`,
  `${source.candidate_label} Judge 正确 (1/0)`,
  "Judge transition",
  `${source.baseline_label} 错误 (1/0)`,
  `${source.candidate_label} 错误 (1/0)`,
];
pairData.getRange("A1:O1").values = [pairHeaders];
const pairValues = source.pairs.map((pair) => [
  pair.scene,
  pair.qa_id,
  pair.baseline_e2e_s,
  pair.candidate_e2e_s,
  null,
  null,
  pair.baseline_tokens,
  pair.candidate_tokens,
  null,
  null,
  pair.baseline_correct ? 1 : 0,
  pair.candidate_correct ? 1 : 0,
  null,
  pair.baseline_error ? 1 : 0,
  pair.candidate_error ? 1 : 0,
]);
const pairEndRow = pairValues.length + 1;
pairData.getRange(`A2:O${pairEndRow}`).values = pairValues;
pairData.getRange("E2").formulas = [["=C2-D2"]];
pairData.getRange(`E2:E${pairEndRow}`).fillDown();
pairData.getRange("F2").formulas = [["=IFERROR(E2/C2,0)"]];
pairData.getRange(`F2:F${pairEndRow}`).fillDown();
pairData.getRange("I2").formulas = [["=G2-H2"]];
pairData.getRange(`I2:I${pairEndRow}`).fillDown();
pairData.getRange("J2").formulas = [["=IFERROR(I2/G2,0)"]];
pairData.getRange(`J2:J${pairEndRow}`).fillDown();
pairData.getRange("M2").formulas = [[
  '=IF(AND(K2=1,L2=1),"both_correct",IF(K2=1,"vanilla_only",IF(L2=1,"candidate_only","both_wrong")))',
]];
pairData.getRange(`M2:M${pairEndRow}`).fillDown();

pairData.getRange("A1:O1").format = {
  fill: palette.candidate,
  font: { bold: true, color: "#FFFFFF" },
  wrapText: true,
  verticalAlignment: "center",
  borders: { preset: "outside", style: "thin", color: palette.frame },
};
pairData.getRange(`A2:O${pairEndRow}`).format = {
  font: { color: palette.text },
  borders: {
    insideHorizontal: { style: "thin", color: palette.grid },
  },
};
pairData.getRange(`C2:E${pairEndRow}`).format.numberFormat = "0.000";
pairData.getRange(`F2:F${pairEndRow}`).format.numberFormat = "0.00%";
pairData.getRange(`G2:I${pairEndRow}`).format.numberFormat = "0.0";
pairData.getRange(`J2:J${pairEndRow}`).format.numberFormat = "0.00%";
pairData.getRange(`K2:L${pairEndRow}`).format.numberFormat = "0";
pairData.getRange(`N2:O${pairEndRow}`).format.numberFormat = "0";
pairData.getRange(`A1:O${pairEndRow}`).format.rowHeight = 21;
pairData.getRange("A:A").format.columnWidth = 25;
pairData.getRange("B:B").format.columnWidth = 23;
pairData.getRange("C:J").format.columnWidth = 14;
pairData.getRange("K:L").format.columnWidth = 13;
pairData.getRange("M:M").format.columnWidth = 18;
pairData.getRange("N:O").format.columnWidth = 16;
pairData.freezePanes.freezeRows(1);
pairData.freezePanes.freezeColumns(2);
pairData.getRange(`E2:F${pairEndRow}`).conditionalFormats.add("cellIs", {
  operator: "greaterThan",
  formula: 0,
  format: { fill: palette.positive_fill, font: { color: palette.positive } },
});
pairData.getRange(`E2:F${pairEndRow}`).conditionalFormats.add("cellIs", {
  operator: "lessThan",
  formula: 0,
  format: { fill: palette.negative_fill, font: { color: palette.negative } },
});
pairData.getRange(`I2:J${pairEndRow}`).conditionalFormats.add("cellIs", {
  operator: "greaterThan",
  formula: 0,
  format: { fill: palette.positive_fill, font: { color: palette.positive } },
});
pairData.getRange(`I2:J${pairEndRow}`).conditionalFormats.add("cellIs", {
  operator: "lessThan",
  formula: 0,
  format: { fill: palette.negative_fill, font: { color: palette.negative } },
});

const allin1Headers = [
  "类型",
  "场景",
  "N",
  `${source.baseline_label} 平均 E2E (s)`,
  `${source.candidate_label} 平均 E2E (s)`,
  "E2E 节省 (s)",
  "E2E 优化率",
  "更快请求数",
  "更快请求占比",
  `${source.baseline_label} P50 (s)`,
  `${source.candidate_label} P50 (s)`,
  `${source.baseline_label} P95 (s)`,
  `${source.candidate_label} P95 (s)`,
  `${source.baseline_label} 平均可见 Token`,
  `${source.candidate_label} 平均可见 Token`,
  "可见 Token 节省",
  "可见 Token 节省率",
  `${source.baseline_label} Judge 正确率`,
  `${source.candidate_label} Judge 正确率`,
  "精度变化 (pp)",
  `${source.baseline_label} 正确条目`,
  `${source.candidate_label} 正确条目`,
  `${source.baseline_label}-only`,
  `${source.candidate_label}-only`,
  "净正确条目",
  "客户端错误",
  "状态",
];

allin1.mergeCells("A1:AA1");
allin1.getRange("A1").values = [[`${source.candidate_label} vs ${source.baseline_label}：ALLin1 汇总表`]];
allin1.mergeCells("A2:AA2");
allin1.getRange("A2").values = [[
  `正向定义：E2E/Token 为 ${source.baseline_label} - ${source.candidate_label}；`
  + `精度为 ${source.candidate_label} - ${source.baseline_label}。可见 Token 不含隐藏 confidence probe。`,
]];
allin1.mergeCells("A3:AA3");
allin1.getRange("A3").values = [[
  `来源：${source.baseline_run_dir}  vs  ${source.candidate_run_dir}`,
]];
allin1.getRange("A5:AA5").values = [allin1Headers];

allin1.getRange("A1:AA1").format = {
  fill: palette.text,
  font: { bold: true, color: "#FFFFFF", size: 18 },
  verticalAlignment: "center",
};
allin1.getRange("A2:AA3").format = {
  fill: palette.surface_alt,
  font: { color: palette.muted },
  wrapText: true,
};
allin1.getRange("A5:AA5").format = {
  fill: palette.candidate,
  font: { bold: true, color: "#FFFFFF" },
  wrapText: true,
  verticalAlignment: "center",
  borders: { preset: "outside", style: "thin", color: palette.frame },
};

const sceneStartRow = 6;
source.scene_summaries.forEach((scene, index) => {
  const row = sceneStartRow + index;
  const start = scene.start_row;
  const end = scene.end_row;
  allin1.getRange(`A${row}:B${row}`).values = [["SCENE", scene.scene]];
  allin1.getRange(`AA${row}`).values = [["paired"]];
  allin1.getRange(`C${row}:Z${row}`).formulas = [[
    `=ROWS('Pair Data'!$A$${start}:$A$${end})`,
    `=AVERAGE('Pair Data'!$C$${start}:$C$${end})`,
    `=AVERAGE('Pair Data'!$D$${start}:$D$${end})`,
    `=D${row}-E${row}`,
    `=IFERROR(F${row}/D${row},0)`,
    `=COUNTIF('Pair Data'!$E$${start}:$E$${end},">0")`,
    `=IFERROR(H${row}/C${row},0)`,
    `=MEDIAN('Pair Data'!$C$${start}:$C$${end})`,
    `=MEDIAN('Pair Data'!$D$${start}:$D$${end})`,
    `=PERCENTILE.INC('Pair Data'!$C$${start}:$C$${end},0.95)`,
    `=PERCENTILE.INC('Pair Data'!$D$${start}:$D$${end},0.95)`,
    `=AVERAGE('Pair Data'!$G$${start}:$G$${end})`,
    `=AVERAGE('Pair Data'!$H$${start}:$H$${end})`,
    `=N${row}-O${row}`,
    `=IFERROR(P${row}/N${row},0)`,
    `=SUM('Pair Data'!$K$${start}:$K$${end})/C${row}`,
    `=SUM('Pair Data'!$L$${start}:$L$${end})/C${row}`,
    `=(S${row}-R${row})*100`,
    `=SUM('Pair Data'!$K$${start}:$K$${end})`,
    `=SUM('Pair Data'!$L$${start}:$L$${end})`,
    `=COUNTIFS('Pair Data'!$K$${start}:$K$${end},1,'Pair Data'!$L$${start}:$L$${end},0)`,
    `=COUNTIFS('Pair Data'!$K$${start}:$K$${end},0,'Pair Data'!$L$${start}:$L$${end},1)`,
    `=V${row}-U${row}`,
    `=SUM('Pair Data'!$N$${start}:$N$${end})+SUM('Pair Data'!$O$${start}:$O$${end})`,
  ]];
});

const sceneEndRow = sceneStartRow + source.scene_summaries.length - 1;
const macroRow = sceneEndRow + 1;
const microRow = sceneEndRow + 2;
allin1.getRange(`A${macroRow}:B${macroRow}`).values = [["MACRO", "场景等权宏平均"]];
allin1.getRange(`AA${macroRow}`).values = [["13 scenes, equal weight"]];
allin1.getRange(`C${macroRow}:Z${macroRow}`).formulas = [[
  `=ROWS(B${sceneStartRow}:B${sceneEndRow})`,
  `=AVERAGE(D${sceneStartRow}:D${sceneEndRow})`,
  `=AVERAGE(E${sceneStartRow}:E${sceneEndRow})`,
  `=AVERAGE(F${sceneStartRow}:F${sceneEndRow})`,
  `=AVERAGE(G${sceneStartRow}:G${sceneEndRow})`,
  '=""',
  `=AVERAGE(I${sceneStartRow}:I${sceneEndRow})`,
  `=AVERAGE(J${sceneStartRow}:J${sceneEndRow})`,
  `=AVERAGE(K${sceneStartRow}:K${sceneEndRow})`,
  `=AVERAGE(L${sceneStartRow}:L${sceneEndRow})`,
  `=AVERAGE(M${sceneStartRow}:M${sceneEndRow})`,
  `=AVERAGE(N${sceneStartRow}:N${sceneEndRow})`,
  `=AVERAGE(O${sceneStartRow}:O${sceneEndRow})`,
  `=AVERAGE(P${sceneStartRow}:P${sceneEndRow})`,
  `=AVERAGE(Q${sceneStartRow}:Q${sceneEndRow})`,
  `=AVERAGE(R${sceneStartRow}:R${sceneEndRow})`,
  `=AVERAGE(S${sceneStartRow}:S${sceneEndRow})`,
  `=(S${macroRow}-R${macroRow})*100`,
  '=""',
  '=""',
  '=""',
  '=""',
  '=""',
  `=SUM(Z${sceneStartRow}:Z${sceneEndRow})`,
]];

allin1.getRange(`A${microRow}:B${microRow}`).values = [["MICRO", "请求加权微平均"]];
allin1.getRange(`AA${microRow}`).values = [["all paired requests"]];
allin1.getRange(`C${microRow}:Z${microRow}`).formulas = [[
  `=ROWS('Pair Data'!$A$2:$A$${pairEndRow})`,
  `=AVERAGE('Pair Data'!$C$2:$C$${pairEndRow})`,
  `=AVERAGE('Pair Data'!$D$2:$D$${pairEndRow})`,
  `=D${microRow}-E${microRow}`,
  `=IFERROR(F${microRow}/D${microRow},0)`,
  `=COUNTIF('Pair Data'!$E$2:$E$${pairEndRow},">0")`,
  `=H${microRow}/C${microRow}`,
  `=MEDIAN('Pair Data'!$C$2:$C$${pairEndRow})`,
  `=MEDIAN('Pair Data'!$D$2:$D$${pairEndRow})`,
  `=PERCENTILE.INC('Pair Data'!$C$2:$C$${pairEndRow},0.95)`,
  `=PERCENTILE.INC('Pair Data'!$D$2:$D$${pairEndRow},0.95)`,
  `=AVERAGE('Pair Data'!$G$2:$G$${pairEndRow})`,
  `=AVERAGE('Pair Data'!$H$2:$H$${pairEndRow})`,
  `=N${microRow}-O${microRow}`,
  `=P${microRow}/N${microRow}`,
  `=SUM('Pair Data'!$K$2:$K$${pairEndRow})/C${microRow}`,
  `=SUM('Pair Data'!$L$2:$L$${pairEndRow})/C${microRow}`,
  `=(S${microRow}-R${microRow})*100`,
  `=SUM('Pair Data'!$K$2:$K$${pairEndRow})`,
  `=SUM('Pair Data'!$L$2:$L$${pairEndRow})`,
  `=COUNTIFS('Pair Data'!$K$2:$K$${pairEndRow},1,'Pair Data'!$L$2:$L$${pairEndRow},0)`,
  `=COUNTIFS('Pair Data'!$K$2:$K$${pairEndRow},0,'Pair Data'!$L$2:$L$${pairEndRow},1)`,
  `=V${microRow}-U${microRow}`,
  `=SUM('Pair Data'!$N$2:$N$${pairEndRow})+SUM('Pair Data'!$O$2:$O$${pairEndRow})`,
]];

const allin1EndRow = microRow;
allin1.getRange(`A6:AA${allin1EndRow}`).format = {
  font: { color: palette.text },
  borders: { insideHorizontal: { style: "thin", color: palette.grid } },
  verticalAlignment: "center",
};
allin1.getRange(`A${macroRow}:AA${microRow}`).format = {
  fill: palette.surface_alt,
  font: { bold: true, color: palette.text },
  borders: {
    top: { style: "medium", color: palette.frame },
    insideHorizontal: { style: "thin", color: palette.grid },
  },
};
allin1.getRange(`C6:C${allin1EndRow}`).format.numberFormat = "0";
allin1.getRange(`D6:F${allin1EndRow}`).format.numberFormat = "0.00";
allin1.getRange(`G6:G${allin1EndRow}`).format.numberFormat = "0.00%";
allin1.getRange(`H6:H${allin1EndRow}`).format.numberFormat = "0";
allin1.getRange(`I6:I${allin1EndRow}`).format.numberFormat = "0.00%";
allin1.getRange(`J6:M${allin1EndRow}`).format.numberFormat = "0.00";
allin1.getRange(`N6:P${allin1EndRow}`).format.numberFormat = "0.0";
allin1.getRange(`Q6:S${allin1EndRow}`).format.numberFormat = "0.00%";
allin1.getRange(`T6:T${allin1EndRow}`).format.numberFormat = '0.00 "pp"';
allin1.getRange(`U6:Z${allin1EndRow}`).format.numberFormat = "0";
allin1.getRange(`G6:G${allin1EndRow}`).conditionalFormats.add("cellIs", {
  operator: "greaterThan",
  formula: 0,
  format: { fill: palette.positive_fill, font: { color: palette.positive } },
});
allin1.getRange(`Q6:Q${allin1EndRow}`).conditionalFormats.add("cellIs", {
  operator: "greaterThan",
  formula: 0,
  format: { fill: palette.positive_fill, font: { color: palette.positive } },
});
allin1.getRange(`T6:T${allin1EndRow}`).conditionalFormats.add("cellIs", {
  operator: "greaterThan",
  formula: 0,
  format: { fill: palette.positive_fill, font: { color: palette.positive } },
});
allin1.getRange(`T6:T${allin1EndRow}`).conditionalFormats.add("cellIs", {
  operator: "lessThan",
  formula: 0,
  format: { fill: palette.negative_fill, font: { color: palette.negative } },
});
allin1.getRange("A1:AA3").format.rowHeight = 28;
allin1.getRange("A5:AA5").format.rowHeight = 42;
allin1.getRange(`A6:AA${allin1EndRow}`).format.rowHeight = 24;
allin1.getRange("A:A").format.columnWidth = 10;
allin1.getRange("B:B").format.columnWidth = 26;
allin1.getRange("C:C").format.columnWidth = 8;
allin1.getRange("D:Q").format.columnWidth = 14;
allin1.getRange("R:T").format.columnWidth = 15;
allin1.getRange("U:Z").format.columnWidth = 12;
allin1.getRange("AA:AA").format.columnWidth = 24;
allin1.freezePanes.freezeRows(5);
allin1.freezePanes.freezeColumns(2);

notes.getRange("A1:B1").values = [["项目", "说明"]];
notes.getRange("A2:B13").values = [
  ["对比", `${source.candidate_label} vs ${source.baseline_label}`],
  ["基线目录", source.baseline_run_dir],
  ["候选目录", source.candidate_run_dir],
  ["配对键", "规范化场景名 + QA_ID；缺失或重复时工作流直接失败"],
  ["E2E gain", `${source.baseline_label} E2E - ${source.candidate_label} E2E；正值表示候选更快`],
  ["E2E gain %", `每场景 mean(gain) / mean(${source.baseline_label} E2E)`],
  ["可见 Token saving", "客户端 completion Token 差；不含隐藏 confidence probe"],
  ["精度变化", `${source.candidate_label} Judge accuracy - ${source.baseline_label} Judge accuracy`],
  ["宏平均", "13 个场景等权；百分比为场景级百分比的算术平均"],
  ["微平均", "633 个配对请求等权"],
  ["证据边界", "本工作簿复算本地实测结果，不代表重新运行 vLLM 性能实验"],
  ["生成时间", `'${source.generated_at}`],
];
notes.getRange("A1:B1").format = {
  fill: palette.candidate,
  font: { bold: true, color: "#FFFFFF" },
};
notes.getRange("A2:A13").format = { fill: palette.surface_alt, font: { bold: true, color: palette.text } };
notes.getRange("A1:B13").format.borders = {
  insideHorizontal: { style: "thin", color: palette.grid },
  outside: { style: "thin", color: palette.frame },
};
notes.getRange("A:A").format.columnWidth = 18;
notes.getRange("B:B").format.columnWidth = 88;
notes.getRange("A1:B13").format.wrapText = true;
notes.getRange("A1:B13").format.rowHeight = 28;
notes.freezePanes.freezeRows(1);

const keyRange = await workbook.inspect({
  kind: "table",
  range: "ALLin1!A18:AA20",
  include: "values,formulas",
  tableMaxRows: 3,
  tableMaxCols: 27,
  maxChars: 5000,
});
const formulaErrors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});

const microValues = allin1.getRange(`C${microRow}:Z${microRow}`).values[0];
const semanticChecks = [
  ["paired rows", microValues[0], source.micro.n],
  ["faster rows", microValues[5], source.micro.faster_count],
  ["baseline correct", microValues[18], source.micro.baseline_correct],
  ["candidate correct", microValues[19], source.micro.candidate_correct],
  [`${source.baseline_label}-only`, microValues[20], source.micro.vanilla_only],
  [`${source.candidate_label}-only`, microValues[21], source.micro.candidate_only],
  ["net correct", microValues[22], source.micro.net_correct],
  ["client errors", microValues[23], source.micro.client_errors],
];
for (const [label, actual, expected] of semanticChecks) {
  if (Number(actual) !== Number(expected)) {
    throw new Error(`Workbook semantic check failed for ${label}: ${actual} != ${expected}`);
  }
}

await fs.mkdir(previewDir, { recursive: true });
const previews = [
  ["ALLin1", `A1:AA${allin1EndRow}`, "allin1-preview.png", 0.8],
  ["Pair Data", "A1:O26", "pair-data-preview.png", 1],
  ["Notes", "A1:B13", "notes-preview.png", 1.2],
];
for (const [sheetName, range, filename, scale] of previews) {
  const preview = await workbook.render({ sheetName, range, scale, format: "png" });
  await fs.writeFile(path.join(previewDir, filename), new Uint8Array(await preview.arrayBuffer()));
}

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

if (manifestPath) {
  const manifest = JSON.parse(await fs.readFile(manifestPath, "utf8"));
  const bytes = await fs.readFile(outputPath);
  manifest.outputs = manifest.outputs.filter((item) => item.path !== path.basename(outputPath));
  manifest.outputs.push({
    path: path.basename(outputPath),
    sha256: crypto.createHash("sha256").update(bytes).digest("hex"),
  });
  manifest.workbook = {
    path: path.basename(outputPath),
    sheets: ["ALLin1", "Pair Data", "Notes"],
    formula_driven: true,
    preview_verified: false,
  };
  await fs.writeFile(manifestPath, JSON.stringify(manifest, null, 2), "utf8");
}

console.log(
  JSON.stringify({
    status: "ok",
    outputPath,
    allin1EndRow,
    pairRows: pairValues.length,
    inspect: keyRange.ndjson.slice(0, 1200),
    formulaErrors: formulaErrors.ndjson,
    previews: previews.map((item) => path.join(previewDir, item[2])),
  }),
);
