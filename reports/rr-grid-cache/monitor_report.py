import importlib.util, json, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location(
    "kaos_rr_grid", r"C:\Users\Kullanıcı\Entropy\scripts\kaos_rr_grid.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
rows = []
p = m.RESULTS_JSONL
if p.exists():
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
agg = m.aggregate(rows, m.VARIANTS, 3)
done = {(tuple(r["variant"]), r["window"]) for r in rows if not r.get("skipped")}
exp = len(m.VARIANTS) * 3
status = "RUNNING" if len(done) < exp else "COMPLETE (final aggregation pending)"
notes = []
if len(done) < exp:
    notes.append(f"windows completed: {len(done)}/{exp} (variant,window) cells")
m.write_report(agg, m.VARIANTS, 3, status, notes)
print(f"monitor: {len(rows)} cells -> {m.REPORT_PATH} status={status}", flush=True)
