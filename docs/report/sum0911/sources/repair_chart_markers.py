"""Repair Office scatter segments, preserving chart data and preview rendering.

Point-level outlines can override series noFill in Office. Hollow samples use
two filled marker layers without any line overrides. Both layers reference the
same embedded-workbook cells, so Edit Data changes both layers together.
Apply after materializing workbooks, before final validation.
"""
from copy import deepcopy
from pathlib import Path
import sys
import zipfile
from lxml import etree as E

NS = {'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
def tag(prefix, name):
    return '{' + NS[prefix] + '}' + name

def paint(style, color):
    for child in list(style):
        style.remove(child)
    E.SubElement(E.SubElement(style, tag('a', 'solidFill')), tag('a', 'srgbClr'), val=color)
    E.SubElement(E.SubElement(style, tag('a', 'ln'), w='0'), tag('a', 'noFill'))

def paint_series(ser, color, size):
    paint(ser.find('c:spPr', NS), color)
    marker = ser.find('c:marker', NS)
    marker.find('c:size', NS).set('val', str(size))
    paint(marker.find('c:spPr', NS), color)
    for point in ser.findall('c:dPt', NS):
        pm = point.find('c:marker', NS)
        if pm is not None:
            point.remove(pm)
        paint(point.find('c:spPr', NS), color)

def repair(source, destination):
    changed = 0
    with zipfile.ZipFile(source) as zin, zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if '/charts/chart' in item.filename and item.filename.endswith('.xml'):
                root = E.fromstring(data)
                touched = False
                for ser in root.findall('.//c:scatterChart/c:ser', NS):
                    marker = ser.find('c:marker', NS)
                    if marker is None or marker.find('c:symbol', NS).get('val') == 'none':
                        continue
                    if ser.find('c:spPr/a:ln/a:noFill', NS) is None:
                        continue
                    ser.find('c:spPr/a:ln', NS).set('w', '0')
                    points = ser.findall('c:dPt', NS)
                    fill = ser.find('c:spPr/a:solidFill/a:srgbClr', NS)
                    outline = points[0].find('c:spPr/a:ln/a:solidFill/a:srgbClr', NS) if points else None
                    if fill is not None and fill.get('val') == 'FFFFFF' and outline is not None:
                        for axis in ['xVal', 'yVal']:
                            assert ser.find(f'c:{axis}/c:numRef/c:f', NS) is not None, 'Materialize workbook first'
                        inner = deepcopy(ser)
                        tx = inner.find('c:tx', NS)
                        name = ''.join(tx.itertext())
                        tx.clear()
                        E.SubElement(tx, tag('c', 'v')).text = name + '（空心中心，共用数据）'
                        size = int(marker.find('c:size', NS).get('val'))
                        paint_series(inner, 'FFFFFF', max(2, size-2))
                        paint_series(ser, outline.get('val'), size)
                        ser.addnext(inner)
                    else:
                        # Filled markers need no per-point line style at all.
                        for point in points:
                            style = point.find('c:spPr', NS)
                            if style is None:
                                continue
                            line = style.find('a:ln', NS)
                            if line is not None:
                                style.remove(line)
                            E.SubElement(E.SubElement(style, tag('a', 'ln'), w='0'), tag('a', 'noFill'))
                        if fill is not None:
                            paint(marker.find('c:spPr', NS), fill.get('val'))
                    changed += 1
                    touched = True
                if touched:
                    for chart in root.findall('.//c:scatterChart', NS):
                        for i, ser in enumerate(chart.findall('c:ser', NS)):
                            ser.find('c:idx', NS).set('val', str(i))
                            ser.find('c:order', NS).set('val', str(i))
                    data = E.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
            zout.writestr(item, data)
    assert changed > 0, 'No unconnected scatter sample series found'
    print(f'Repaired {changed} scatter sample series: {Path(destination).name}')

if __name__ == '__main__':
    repair(sys.argv[1], sys.argv[2])
