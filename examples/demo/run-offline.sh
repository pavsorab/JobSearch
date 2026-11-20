#!/usr/bin/env bash
# Offline end-to-end run of the demo profile on fixture data (no Apify, no network).
# Used by CI and handy after editing the code: bash examples/demo/run-offline.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
export JOBSEARCH_PROFILE=examples/demo
F=examples/demo/fixtures
R=$(mktemp -d)
cp examples/demo/state/seen-jobs.json "$R/seen.before"
trap 'cp "$R/seen.before" examples/demo/state/seen-jobs.json; rm -rf "$R" examples/demo/results examples/demo/output' EXIT
python3 jptools.py build-inputs
python3 jobpipe.py stage-a --linkedin $F/linkedin.json --naukri $F/naukri.json \
    --indeed $F/indeed.json --out "$R/triage.json" --no-network
python3 jobpipe.py stage-b --triage "$R/triage.json" --details $F/details.json --out "$R/scored.json"
python3 jptools.py brief --scored "$R/scored.json" --outdir "$R"
python3 - "$R" <<'PY'
import json, sys
R = sys.argv[1]
s = json.load(open(f"{R}/scored.json"))
with open(f"{R}/judgments.jsonl", "w") as f:
    for j in s["jobs"]:
        if j.get("description"):
            f.write(json.dumps({"id": j["job_id"], "rs": 30, "diff": 0, "quote": "",
                                "cap": None, "why": "offline demo"}) + "\n")
PY
python3 jptools.py render --scored "$R/scored.json" --judgments "$R/judgments.jsonl" --date 2000-01-01 | tail -3
python3 jptools.py scaffold "Demo Co" --force
python3 jptools.py check-tex "examples/demo/output/Alex Example $(date +%Y) - Demo Co.tex" | head -2 || true
echo "offline demo run: OK"
