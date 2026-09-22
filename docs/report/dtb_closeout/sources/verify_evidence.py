"""Verify released evidence against data, hashes, IDs and native PPT chart caches."""
import hashlib
import json
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as E

BASE=Path(__file__).resolve().parents[1]
ROOT=BASE.parents[2]
OUT=BASE/'evidence'
def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
def main():
    manifest=read('source_manifest.json')
    for p,item in manifest['sources'].items():
        b=(ROOT/p).read_bytes()
        # Accept exact bytes, or a Git-only LF/CRLF conversion with identical text.
        assert hashlib.sha256(b).hexdigest()==item['sha256'] or hashlib.sha256(b.replace(b'\r\n',b'\n')).hexdigest()==item['sha256_lf'],p
    data=read('distribution.json');rows=data['requests'];summary=data['summary']
    assert len(rows)==len({r['request_id'] for r in rows})==8277
    assert sum(r['reasoning_tokens'] for r in rows)==2322694
    assert sum(r['reasoning_tokens']>750 for r in rows)==415
    assert summary['p95']==753 and summary['p99']==1002
    assert [r['started_at_utc'] for r in rows]==sorted(r['started_at_utc'] for r in rows)
    p=read('paired.json');pairs=p['pairs']
    assert len(pairs)==290
    same=[r for r in pairs if r['group'] in ['short_sampling','short_greedy'] and r['same_output']]
    assert len(same)==37 and abs(sum(r['d_ms']-r['v_ms'] for r in same)/37-.10)<.005
    index=read('index.json')
    for claim in index['claims']:
        for f in claim['figures']:assert (OUT/f).is_file(),f
        assert claim['limitations'] and claim['selection'] and claim['source_sha']
    for n,status in [('l178_a1','success'),('l399_a10','bounded_success'),('l178_a3','failure')]:
        c=read('cards/'+n+'.json');assert c['status']==status
        assert c['pair']['v_ms']==c['vanilla']['elapsed_ms']
        assert c['pair']['d_ms']==c['dtb']['elapsed_ms']
        assert c['pair']['d_id']==c['dtb']['client_request_id']
    ppt=OUT/'figures/vanilla_distribution.pptx'
    ns={'c':'http://schemas.openxmlformats.org/drawingml/2006/chart',
        'p':'http://schemas.openxmlformats.org/presentationml/2006/main'}
    with zipfile.ZipFile(ppt) as z:
        slides=[n for n in z.namelist() if re.fullmatch('ppt/slides/slide[0-9]+.xml',n)]
        charts=[n for n in z.namelist() if re.fullmatch(r'ppt/(?:slides/)?charts/chart[0-9]+\.xml',n)]
        assert len(slides)==len(charts)==1
        assert len([n for n in z.namelist() if n.startswith('ppt/embeddings/') and n.endswith('.xlsx')])==1
        assert not E.fromstring(z.read(slides[0])).findall('.//p:pic',ns),'Raster chart replacement'
        root=E.fromstring(z.read(charts[0]));series=root.findall('.//c:scatterChart/c:ser',ns)
        assert len(series)==3
        for ser,subset in zip(series[:2],[[r for r in rows if r['reasoning_tokens']<=750],[r for r in rows if r['reasoning_tokens']>750]]):
            xs=[float(x.text) for x in ser.findall('c:xVal/c:numRef/c:numCache/c:pt/c:v',ns)]
            ys=[int(float(x.text)) for x in ser.findall('c:yVal/c:numRef/c:numCache/c:pt/c:v',ns)]
            assert ys==[r['reasoning_tokens'] for r in subset]
            assert len(xs)==len(subset) and all(abs(x-r['hours_from_start'])<1e-8 for x,r in zip(xs,subset))
        threshold=[float(x.text) for x in series[2].findall('c:yVal/c:numRef/c:numCache/c:pt/c:v',ns)]
        assert threshold==[750,750]
    print(json.dumps(dict(source_files=len(manifest['sources']),distribution_requests=len(rows),pairs=len(pairs),
        matched_pairs=len(same),ppt_slides=1,native_charts=1,editable_sample_points=8277,embedded_workbooks=1,
        status='PASS',powerpoint_application_test='not_run'),ensure_ascii=False))
if __name__=='__main__':main()
