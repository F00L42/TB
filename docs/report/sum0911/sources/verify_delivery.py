"""Check local links and the final PPTX against the report snapshot."""
from pathlib import Path
from urllib.parse import unquote
import hashlib
import json
import re
import zipfile

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
for file in out.glob('*.html'):
    for target in re.findall(r'href="([^"]+)"', file.read_text(encoding='utf-8')):
        if '://' in target or target.startswith('#'):
            continue
        count += 1
        if not (file.parent / unquote(target.split('#')[0])).exists():
            broken.append([file.name, target])
pptx = out / 'DTB阶段性汇报.pptx'
with zipfile.ZipFile(pptx) as z:
    names = z.namelist()
    slides = [n for n in names if re.fullmatch(r'ppt/slides/slide\d+.xml', n)]
    charts = [n for n in names if re.search(r'/charts/chart\d+.xml$', n)]
    notes = [n for n in names if re.fullmatch(r'ppt/notesSlides/notesSlide\d+.xml', n)]
    assert len(slides) == 4 and len(charts) == 2 and len(notes) == 4
    assert len([n for n in names if n.endswith('.xlsx')]) == 2
receipt = json.loads((out.parent / '.sum0911-build/validation.json').read_text(encoding='utf-8'))
assert receipt['finalSha256'] == hashlib.sha256(pptx.read_bytes()).hexdigest()
assert receipt['firstPartyImport']['passed']
assert receipt['nativeChartValidation']['passed']
assert len(list((out/'预览').glob('*.png'))) == 4
assert not broken, broken
print(json.dumps({'links_checked':count, 'broken_links':broken, 'slides':len(slides),
                  'editable_charts':len(charts), 'notes':len(notes),
                  'blog_chars':len((out/'技术博客.md').read_text(encoding='utf-8')),
                  'mail_chars':len((out/'邮件正文.md').read_text(encoding='utf-8'))},ensure_ascii=False))
