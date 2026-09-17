"""Inspect one captured request, or its exact events; never replay the model."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile

from .analysis import read_json, reconstruct, snapshot_events
from .tokens import TokenCounter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_dir")
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--raw-events", action="store_true")
    args = parser.parse_args()
    if not args.raw_events:
        root = Path(args.capture_dir)
        config = read_json(root / "manifest.json")["config"]
        with tempfile.TemporaryDirectory(prefix="observation-inspect-") as temporary:
            with closing(sqlite3.connect(Path(temporary) / "events.sqlite")) as db:
                problems = []
                snapshot_events(root, db, problems)
                if not db.execute("SELECT 1 FROM events WHERE rid=? LIMIT 1", (args.request_id,)).fetchone():
                    parser.error("request ID not found")
                row, detail = reconstruct(db, args.request_id, config, TokenCounter.from_config(config))
                print(json.dumps({"record": row, "detail": detail, "archive_warnings": problems}, ensure_ascii=False, indent=2))
        return
    found = []
    for path in (Path(args.capture_dir) / "archive").glob("*/*/part-*.jsonl"):
        with path.open(encoding="utf-8") as source:
            for line in source:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("request_id") == args.request_id:
                    found.append(event)
    if not found:
        parser.error("request ID not found")
    for event in sorted(found, key=lambda item: item["event_seq"]):
        print(json.dumps(event, ensure_ascii=False))


if __name__ == "__main__":
    main()
