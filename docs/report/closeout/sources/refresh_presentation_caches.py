"""Align literal numeric cache strings with unchanged embedded workbooks."""
from pathlib import Path
import json
import re
import sys
import zipfile
from lxml import etree as E
from revise_presentation import refresh_numeric_caches, xml_bytes


def main(source, target):
    source, target = Path(source), Path(target)
    assert source.resolve() != target.resolve()
    assert not target.exists(), target
    with zipfile.ZipFile(source) as archive:
        parts = {info.filename: archive.read(info.filename) for info in archive.infolist()}
        changes = {}
        for name, raw in parts.items():
            if re.fullmatch(r"ppt/charts/chart\d+\.xml", name):
                root = E.fromstring(raw)
                # Helper rejects differences greater than 1e-9. It changes
                # only cache strings, not values, geometry or workbook bytes.
                if refresh_numeric_caches(root, name, parts):
                    changes[name] = xml_bytes(root)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as output:
            for info in archive.infolist():
                output.writestr(info, changes.get(info.filename, parts[info.filename]))
    print(json.dumps({"cacheAlignedParts": sorted(changes), "unchangedWorkbooks": True}))


if __name__ == "__main__":
    main(*sys.argv[1:])
