"""Revise the Office-edited r5 without rebuilding its bound connectors.

Repair development chart styles and move footers into speaker notes. Replace
the Noemate example chart with the approved 50-pair mean latency comparison.
All other chart data, embedded workbooks and bound objects are retained.
"""
from pathlib import Path
from decimal import Decimal
import hashlib
import io
import json
import posixpath
import re
import sys
import zipfile
from lxml import etree as E

NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
}
SOURCE_SHA256 = "2fbf902e74ec26b446267ded768ddd61a7f510e9813de311251bccf75212f9ba"
EVIDENCE = Path(__file__).resolve().parent.parent / "evidence"


def xml_bytes(root):
    return E.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def refresh_numeric_caches(root, chart_path, parts):
    """Match Office cache strings to existing cells without changing workbooks."""
    external = root.find("c:externalData", NS)
    if external is None:
        return False
    relpath = posixpath.join(posixpath.dirname(chart_path), "_rels", posixpath.basename(chart_path) + ".rels")
    rels = E.fromstring(parts[relpath])
    rel = next(r for r in rels if r.get("Id") == external.get("{" + NS["r"] + "}id"))
    book_path = posixpath.normpath(posixpath.join(posixpath.dirname(chart_path), rel.get("Target")))
    changed = False
    with zipfile.ZipFile(io.BytesIO(parts[book_path])) as workbook:
        book = E.fromstring(workbook.read("xl/workbook.xml"))
        book_rels = E.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
        sheets = {}
        for sheet in book.findall("s:sheets/s:sheet", NS):
            relation = next(r for r in book_rels if r.get("Id") == sheet.get("{" + NS["r"] + "}id"))
            target = posixpath.normpath(posixpath.join("xl", relation.get("Target")))
            data = E.fromstring(workbook.read(target))
            sheets[sheet.get("name")] = {c.get("r"): c.findtext("s:v", namespaces=NS) for c in data.findall(".//s:c", NS)}
        for ref in root.findall(".//c:numRef", NS):
            formula = ref.findtext("c:f", namespaces=NS)
            sheet, addresses = formula.rsplit("!", 1)
            match = re.fullmatch(r"\$?([A-Z]+)\$?(\d+):\$?([A-Z]+)\$?(\d+)", addresses)
            assert match and match[1] == match[3], formula
            cells = sheets[sheet.strip("'")]
            for point in ref.findall("c:numCache/c:pt", NS):
                cell = match[1] + str(int(match[2]) + int(point.get("idx")))
                expected = cells[cell]
                value = point.find("c:v", NS)
                assert abs(float(value.text) - float(expected)) <= 1e-9, (formula, cell)
                if value.text != expected:
                    value.text = expected
                    changed = True
    return changed


def update_noemate_comparison(parts, changed):
    """Use every frozen pair and update the chart cache and workbook together."""
    pairs = json.loads((EVIDENCE / "noemate_long_pairs.json").read_text(encoding="utf-8"),
                       parse_float=Decimal)
    expected = {(line, attempt) for line in (178, 259, 319, 353, 399)
                for attempt in range(1, 11)}
    assert len(pairs) == 50 and {(p["line"], p["attempt"]) for p in pairs} == expected
    assert all(p["group"] == "long" for p in pairs)
    assert len({p[k] for p in pairs for k in ("v_id", "d_id")}) == 100
    totals = {side: sum(Decimal(p[f"{side}_ms"]) for p in pairs) / 1000
              for side in ("v", "d")}
    means = {side: value / len(pairs) for side, value in totals.items()}
    reduction = (1 - totals["d"] / totals["v"]) * 100
    faster = sum(p["d_ms"] < p["v_ms"] for p in pairs)

    slide_path = "ppt/slides/slide4.xml"
    slide = E.fromstring(changed[slide_path])
    replacements = {
        "2": ("Noemate 单请求样例与研发任务组，分别展示请求等待和累计 LLM 耗时",
              "Noemate 回放请求与研发任务组，分别展示请求时延和累计 LLM 耗时"),
        "13": ("Noemate 告警分类 178/a1", "Noemate 长请求回放（50 对）"),
        "14": ("单请求 E2E / 秒", "平均请求 E2E / 秒"),
        "15": ("−24.97%", f"−{reduction:.2f}%"),
        "17": ("final 保留 12 条告警及分类字段", "5 个输入 × 10 对回放"),
    }
    for shape in slide.findall("p:cSld/p:spTree/p:sp", NS):
        prop = shape.find("p:nvSpPr/p:cNvPr", NS)
        if prop.get("id") not in replacements:
            continue
        old, new = replacements.pop(prop.get("id"))
        texts = shape.findall(".//a:t", NS)
        assert len(texts) == 1 and texts[0].text == old
        texts[0].text = new
        prop.set("name", new)
    assert not replacements
    changed[slide_path] = xml_bytes(slide)

    chart_path = "ppt/charts/chart4.xml"
    chart = E.fromstring(changed.get(chart_path, parts[chart_path]))
    series = chart.find(".//c:barChart/c:ser", NS)
    series.find("c:tx/c:v", NS).text = "平均请求 E2E / 秒"
    labels = [p.findtext("c:v", namespaces=NS) for p in series.findall("c:cat/c:strRef/c:strCache/c:pt", NS)]
    assert labels == ["DTB", "Vanilla"]
    points = series.findall("c:val/c:numRef/c:numCache/c:pt", NS)
    assert len(points) == 2
    for point, side in zip(points, ("d", "v")):
        point.find("c:v", NS).text = str(means[side])
    changed[chart_path] = xml_bytes(chart)

    book_path = "ppt/embeddings/Microsoft_Excel_Worksheet3.xlsx"
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(parts[book_path])) as source_book:
        sheet_path = "xl/worksheets/sheet1.xml"
        sheet = E.fromstring(source_book.read(sheet_path))
        cells = {c.get("r"): c for c in sheet.findall(".//s:c", NS)}
        assert cells["B1"].findtext("s:is/s:t", namespaces=NS) == "请求 E2E / 秒"
        cells["B1"].find("s:is/s:t", NS).text = "平均请求 E2E / 秒"
        for cell, side in (("B2", "d"), ("B3", "v")):
            cells[cell].find("s:v", NS).text = str(means[side])
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target_book:
            for item in source_book.infolist():
                target_book.writestr(item, xml_bytes(sheet) if item.filename == sheet_path
                                    else source_book.read(item.filename))
    changed[book_path] = output.getvalue()

    notes_path = "ppt/notesSlides/notesSlide4.xml"
    notes = E.fromstring(changed[notes_path])
    body = notes.xpath(".//p:sp[p:nvSpPr/p:nvPr/p:ph[@type='body']]/p:txBody", namespaces=NS)[0]
    for text in body.findall(".//a:t", NS):
        if text.text and text.text.startswith("178/a1："):
            text.text = "单例备查（不作为集合主指标）：" + text.text
        if text.text and text.text.startswith("原页脚说明：历史观测"):
            text.text = "历史口径：Noemate 为冻结长请求回放集合，研发调用轨迹不同，均不代表整体质量不退化。"
    paragraphs = [
        f"Noemate 长请求回放：输入 178、259、319、353、399，每个输入 10 对，共 50 对 Vanilla/DTB。"
        f"全部回放纳入，含慢请求、失败、截断与工具动作变化。平均请求 E2E 为 {means['v']} → {means['d']} 秒，"
        f"降幅 {reduction:.8f}%，计算为 1−ΣDTB/ΣVanilla（总计 {totals['v']} → {totals['d']} 秒），"
        f"不是逐对降幅的平均值。{faster}/50 对更快。正文保留两位小数。",
        "实验条件：20260922T070000Z 的 off 长请求配对回放，单并发，流式，temperature=0.2、top_p=1、"
        "max_tokens=2048、seed=20260918，fixed check/budget=750。5 个输入来自测量前冻结的小集合，"
        "属于重复执行而非 50 个独立业务问题。off 数据没有逐请求触发日志，因此不命名为已确认早退请求的效果。",
        "质量与完成量：178 有字段遗漏、建议错误与草稿泄漏，399 有算术/聚合问题，319 的独立语义审阅未完成。"
        "Vanilla/DTB 的 length 分别为 9/4 次，工具调用为 0/1 次。输出长度与后续动作有变化，"
        "上述数值只说明这组回放的请求时延变化，不证明整体无损收益，也不全部归因于缩短思考。",
        "本地逐对证据：docs/report/closeout/evidence/noemate_long_pairs.json，按 line/attempt 配对，"
        "v_ms/d_ms 对应原 summary.jsonl 的 elapsed_ms，再除以 1000 转秒。固定来源见以下 N*S/N*M 与 S04/S05。",
    ]
    first = body.find("a:p", NS)
    insertion = body.index(first) + 1
    for index, content in enumerate(paragraphs):
        paragraph = E.Element("{" + NS["a"] + "}p")
        run = E.SubElement(paragraph, "{" + NS["a"] + "}r")
        E.SubElement(run, "{" + NS["a"] + "}t").text = content
        body.insert(insertion + index, paragraph)
    manifest = json.loads((EVIDENCE / "source_manifest.json").read_text(encoding="utf-8"))["sources"]
    existing = "\n".join(body.xpath(".//a:t/text()", namespaces=NS))
    for key in ["S04", "S05"] + [f"N{line}{kind}" for line in (178, 259, 319, 353, 399) for kind in ("S", "M")]:
        source = manifest[key]
        if source["url"] in existing:
            continue
        paragraph = E.SubElement(body, "{" + NS["a"] + "}p")
        run = E.SubElement(paragraph, "{" + NS["a"] + "}r")
        E.SubElement(run, "{" + NS["a"] + "}t").text = (
            f"{key}: {source['url']}\ncommit: {source['commit']}; SHA-256: {source['sha256']}")
    changed[notes_path] = xml_bytes(notes)
    return {"pairs": len(pairs), "mean_v_seconds": str(means["v"]),
            "mean_d_seconds": str(means["d"]), "reduction_percent": str(reduction), "faster_pairs": faster}


def main(source, target):
    source, target = Path(source), Path(target)
    assert source.resolve() != target.resolve()
    assert not target.exists(), target
    assert hashlib.sha256(source.read_bytes()).hexdigest() == SOURCE_SHA256, "Unexpected source revision"
    with zipfile.ZipFile(source) as archive:
        parts = {i.filename: archive.read(i.filename) for i in archive.infolist()}
        changed = {}
        for number in range(1, 6):
            slide_path = f"ppt/slides/slide{number}.xml"
            root = E.fromstring(parts[slide_path])
            shapes = root.find("p:cSld/p:spTree", NS)
            foot = []
            for shape in list(shapes):
                prop = shape.find(".//p:cNvPr", NS)
                if prop is None or prop.get("id") not in ("3", "4"):
                    continue
                off = shape.find("p:spPr/a:xfrm/a:off", NS)
                assert off is not None and int(off.get("y")) >= 7800000
                text = "".join(shape.xpath(".//a:t/text()", namespaces=NS))
                if text:
                    foot.append(text)
                shapes.remove(shape)
            assert len(foot) == 1
            if number == 3:
                # Reserve enough right/bottom space for Office tick labels.
                # The actual plotting rectangles retain their original size.
                frames = root.findall(".//p:graphicFrame/p:xfrm/a:ext", NS)
                assert len(frames) == 2
                for frame in frames:
                    frame.set("cx", str(975 * 9525))
                    frame.set("cy", str(235 * 9525))
            changed[slide_path] = xml_bytes(root)
            notes_path = f"ppt/notesSlides/notesSlide{number}.xml"
            notes = E.fromstring(parts[notes_path])
            bodies = notes.xpath(".//p:sp[p:nvSpPr/p:nvPr/p:ph[@type='body']]/p:txBody", namespaces=NS)
            assert len(bodies) == 1
            paragraph = E.SubElement(bodies[0], "{" + NS["a"] + "}p")
            run = E.SubElement(paragraph, "{" + NS["a"] + "}r")
            E.SubElement(run, "{" + NS["a"] + "}t").text = "原页脚说明：" + foot[0]
            changed[notes_path] = xml_bytes(notes)

        chart_count = 0
        for name, data in parts.items():
            if "/charts/chart" not in name or not name.endswith(".xml"):
                continue
            root = E.fromstring(data)
            series = root.findall(".//c:scatterChart/c:ser", NS)
            development = [s for s in series if "研发 " in "".join(s.xpath("c:tx//c:v/text()", namespaces=NS))]
            if not development:
                continue
            assert len(development) == 1
            layout = root.find(".//c:plotArea/c:layout", NS)
            assert layout is not None
            for child in list(layout):
                layout.remove(child)
            manual = E.SubElement(layout, "{" + NS["c"] + "}manualLayout")
            E.SubElement(manual, "{" + NS["c"] + "}layoutTarget", val="inner")
            for axis in ("x", "y", "w", "h"):
                E.SubElement(manual, "{" + NS["c"] + "}" + axis + "Mode",
                             val="edge" if axis in ("x", "y") else "factor")
            for axis, value in {"x": str(75.85 / 975), "y": str(12.95 / 235),
                                "w": str(830.65 / 975), "h": str(135.05 / 235)}.items():
                E.SubElement(manual, "{" + NS["c"] + "}" + axis, val=value)
            series_name = "".join(development[0].xpath("c:tx//c:v/text()", namespaces=NS))
            if "研发 >" in series_name:
                # Draw the 850 boundary as the native lower axis as well.
                # Office slide show can clip a series exactly at plot minimum.
                axis = next(a for a in root.findall(".//c:valAx", NS)
                            if a.find("c:axPos", NS).get("val") == "b")
                line = axis.find("c:spPr/a:ln", NS)
                line.set("w", "13335")
                for child in list(line):
                    line.remove(child)
                E.SubElement(E.SubElement(line, "{" + NS["a"] + "}solidFill"),
                             "{" + NS["a"] + "}srgbClr", val="B16A16")
            if "研发 ≤" in series_name:
                marker = development[0].find("c:marker", NS)
                marker.find("c:size", NS).set("val", "5")
                style = marker.find("c:spPr", NS)
                assert style is not None
                for child in list(style):
                    style.remove(child)
                E.SubElement(style, "{" + NS["a"] + "}noFill")
                line = E.SubElement(style, "{" + NS["a"] + "}ln", w="9525")
                E.SubElement(E.SubElement(line, "{" + NS["a"] + "}solidFill"),
                             "{" + NS["a"] + "}srgbClr", val="819CAC")
            changed[name] = xml_bytes(root)
            chart_count += 1
        assert chart_count == 2
        assert len(changed) == 12
        cache_repairs = []
        for name, data in parts.items():
            if "/charts/chart" not in name or not name.endswith(".xml"):
                continue
            root = E.fromstring(changed.get(name, data))
            if refresh_numeric_caches(root, name, parts):
                changed[name] = xml_bytes(root)
                cache_repairs.append(name)
        noemate = update_noemate_comparison(parts, changed)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as output:
            for item in archive.infolist():
                output.writestr(item, changed.get(item.filename, parts[item.filename]))
    print(json.dumps({"source_sha256": SOURCE_SHA256, "changed_parts": sorted(changed),
                      "numeric_cache_refreshes": cache_repairs, "noemate": noemate}, ensure_ascii=False))


if __name__ == "__main__":
    main(*sys.argv[1:])
