"""Check local links and the final PPTX against the report snapshot."""
from pathlib import Path
from urllib.parse import unquote
import hashlib
import json
import re
import zipfile
import xml.etree.ElementTree as ET

out = Path(__file__).resolve().parent.parent
broken = []
count = 0
for file in out.glob('*.md'):
    for target in re.findall(r'\]\(([^)]+)\)', file.read_text(encoding='utf-8')):
        if '://' in target or target.startswith('#'):
            continue
        count += 1
        if not (file.parent / unquote(target.split('#')[0])).exists():
            broken.append([file.name, target])
assert not list(out.glob('*.html')), 'Report deliverables must not include HTML'
pptx = out / 'DTB阶段性汇报.pptx'
with zipfile.ZipFile(pptx) as z:
    names = z.namelist()
    slides = [n for n in names if re.fullmatch(r'ppt/slides/slide\d+.xml', n)]
    charts = [n for n in names if re.search(r'/charts/chart\d+.xml$', n)]
    notes = [n for n in names if re.fullmatch(r'ppt/notesSlides/notesSlide\d+.xml', n)]
    assert len(slides) == 4 and len(charts) == 3 and len(notes) == 4
    assert len([n for n in names if n.endswith('.xlsx')]) == 3
receipt = json.loads((out.parent / '.sum0911-build/validation.json').read_text(encoding='utf-8'))
assert receipt['finalSha256'] == hashlib.sha256(pptx.read_bytes()).hexdigest()
assert receipt['firstPartyImport']['passed']
assert receipt['nativeChartValidation']['passed']
assert len(list((out/'预览').glob('*.png'))) == 4
ns = {'c':'http://schemas.openxmlformats.org/drawingml/2006/chart',
      'a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
for file in [pptx, out/'统计图/思考长度长尾与850阈值.pptx']:
    with zipfile.ZipFile(file) as z:
        chart_names = [n for n in z.namelist() if re.search(r'/charts/chart\d+.xml$', n)]
        assert len(chart_names) == 3
        paired_layers = 0
        for name in chart_names:
            tree = ET.fromstring(z.read(name))
            samples = []
            for series in tree.findall('.//c:scatterChart/c:ser', ns):
                if series.find('c:marker/c:symbol', ns).get('val') == 'none':
                    continue
                assert series.find('c:spPr/a:ln/a:noFill', ns) is not None
                for point in series.findall('c:dPt', ns):
                    assert point.find('c:spPr/a:ln/a:noFill', ns) is not None
                samples.append(series)
            for i, series in enumerate(samples):
                if '空心中心，共用数据' in ''.join(series.find('c:tx', ns).itertext()):
                    assert i > 0
                    for axis in ['xVal', 'yVal']:
                        assert series.find(f'c:{axis}/c:numRef/c:f', ns).text == samples[i-1].find(f'c:{axis}/c:numRef/c:f', ns).text
                    paired_layers += 1
        assert paired_layers == 1
fig_receipt = json.loads((out.parent/'.sum0911-build/longtail-validation.json').read_text(encoding='utf-8'))
assert fig_receipt['finalSha256'] == hashlib.sha256((out/'统计图/思考长度长尾与850阈值.pptx').read_bytes()).hexdigest()
assert fig_receipt['firstPartyImport']['passed'] and fig_receipt['nativeChartValidation']['passed']
assert not broken, broken
print(json.dumps({'links_checked':count, 'broken_links':broken, 'slides':len(slides),
                  'editable_charts':len(charts), 'notes':len(notes),
                  'blog_chars':len((out/'技术博客.md').read_text(encoding='utf-8')),
                  'mail_chars':len((out/'邮件正文.md').read_text(encoding='utf-8'))},ensure_ascii=False))
