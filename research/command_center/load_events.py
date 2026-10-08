"""Load a JSONL event export into a running command centre (idempotent: duplicate event_ids are ignored).
Usage: python command_center/load_events.py [command_center/real_events.jsonl] [--server http://127.0.0.1:8765]"""
import argparse, json, urllib.request
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument("file", nargs="?", default=str(Path(__file__).parent / "real_events.jsonl"))
ap.add_argument("--server", default="http://127.0.0.1:8765")
ap.add_argument("--token", default=None)
a = ap.parse_args()
tok = a.token or (Path(__file__).parent / ".cc_token").read_text().strip()
events = [json.loads(l) for l in Path(a.file).read_text().splitlines() if l.strip()]
req = urllib.request.Request(a.server + "/api/events", data=json.dumps(events).encode(),
                             headers={"Content-Type": "application/json", "X-CC-Token": tok})
print(urllib.request.urlopen(req, timeout=10).read().decode())
