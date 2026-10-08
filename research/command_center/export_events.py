"""Export non-demo events from the local SQLite store to JSONL (so a copy of the repo can reload them).
Usage: python command_center/export_events.py [db] > command_center/real_events.jsonl"""
import json, sqlite3, sys
from pathlib import Path
db = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent / "data" / "events.sqlite3")
con = sqlite3.connect(db); con.row_factory = sqlite3.Row
for r in con.execute("SELECT * FROM events WHERE source != 'demo' ORDER BY seq"):
    d = {k: r[k] for k in r.keys() if k != "seq"}
    d["payload"] = json.loads(d["payload"]) if isinstance(d["payload"], str) else d["payload"]
    print(json.dumps(d, sort_keys=True))
