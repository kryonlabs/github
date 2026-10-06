#!/usr/bin/env python3
import json
from pathlib import Path
import sys
from test_api import api, Fixture

request = json.loads(Path(sys.argv[1]).read_text())
try:
    response = api.Client(Fixture()).read(request)
except api.Failure as exc:
    response = {"state": "error", "message": str(exc), "rows": []}
Path(sys.argv[2]).write_text(json.dumps(response))
