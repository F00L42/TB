"""Set scatter marker fills and adjoining equal-height segmented plot rectangles."""
from pathlib import Path
import sys
import zipfile
from lxml import etree as E

NS = {"c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}


def main(src: str, dst: str) -> None:
    assert Path(src).resolve() != Path(dst).resolve()
    changed = 0
    segmented = 0
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if "/charts/chart" in item.filename and item.filename.endswith(".xml"):
                root = E.fromstring(data)
                series_names = ["".join(ser.find("c:tx", NS).itertext())
                                for ser in root.findall(".//c:scatterChart/c:ser", NS)]
                if any(name.startswith("研发 ") for name in series_names):
                    plot = root.find(".//c:plotArea", NS)
                    layout = plot.find("c:layout", NS)
                    if layout is None:
                        layout = E.Element("{" + NS["c"] + "}layout")
                        plot.insert(0, layout)
                    for child in list(layout):
                        layout.remove(child)
                    manual = E.SubElement(layout, "{" + NS["c"] + "}manualLayout")
                    E.SubElement(manual, "{" + NS["c"] + "}layoutTarget", val="inner")
                    for axis in ("x", "y", "w", "h"):
                        E.SubElement(manual, "{" + NS["c"] + "}" + axis + "Mode", val="factor")
                    for axis, value in {"x": "0.082", "y": "0.07", "w": "0.898", "h": "0.73"}.items():
                        E.SubElement(manual, "{" + NS["c"] + "}" + axis, val=value)
                    segmented += 1
                for ser in root.findall(".//c:scatterChart/c:ser", NS):
                    marker = ser.find("c:marker", NS)
                    if marker is None or marker.find("c:symbol", NS).get("val") == "none":
                        continue
                    style = ser.find("c:spPr", NS)
                    line = style.find("a:ln", NS)
                    if line is None or line.find("a:noFill", NS) is None:
                        continue
                    line.set("w", "0")
                    color = style.find("a:solidFill/a:srgbClr", NS).get("val")
                    mstyle = marker.find("c:spPr", NS)
                    if mstyle is None:
                        mstyle = E.SubElement(marker, "{" + NS["c"] + "}spPr")
                    for child in list(mstyle):
                        mstyle.remove(child)
                    E.SubElement(E.SubElement(mstyle, "{" + NS["a"] + "}solidFill"),
                                 "{" + NS["a"] + "}srgbClr", val=color)
                    E.SubElement(E.SubElement(mstyle, "{" + NS["a"] + "}ln", w="0"),
                                 "{" + NS["a"] + "}noFill")
                    changed += 1
                data = E.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            zout.writestr(item, data)
    assert changed == 4, changed
    assert segmented == 2, segmented


if __name__ == "__main__":
    main(*sys.argv[1:])
