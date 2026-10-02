#!/usr/bin/env python3
"""Validate agent_state/AGENT_HANDOFF.json.

Usage:
    python agent_state/validate_handoff.py                # validate the working file
    python agent_state/validate_handoff.py --previous OLD.json
        # also check append-only rules vs a previous version

Exit code 0 = valid, 1 = problems found. Run in CI and by each agent before handing over.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
MAX_REVIEW_CYCLES = 2


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def schema_errors(data: dict) -> list[str]:
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return [
            "(jsonschema not installed: structural schema check skipped; "
            "run `pip install jsonschema`)"
        ]
    schema = load(HERE / "handoff.schema.json")
    v = jsonschema.Draft7Validator(schema)
    return [
        f"schema: {'/'.join(map(str, e.path)) or '<root>'}: {e.message}"
        for e in v.iter_errors(data)
    ]


def logic_errors(d: dict) -> list[str]:
    errs: list[str] = []

    def ids(key: str) -> list[str]:
        return [x["id"] for x in d.get(key, [])]

    for key in ("requirements", "tasks", "decisions", "validations", "questions", "blockers"):
        seen: set[str] = set()
        for i in ids(key):
            if i in seen:
                errs.append(f"duplicate id in {key}: {i}")
            seen.add(i)

    task_ids = set(ids("tasks"))
    req_ids = set(ids("requirements"))
    by_id = {t["id"]: t for t in d.get("tasks", [])}

    for t in d.get("tasks", []):
        for dep in t.get("depends_on", []):
            if dep not in task_ids:
                errs.append(f"{t['id']}: depends_on unknown task {dep}")
            if dep == t["id"]:
                errs.append(f"{t['id']}: depends on itself")
        for r in t.get("requirements", []):
            if r not in req_ids:
                errs.append(f"{t['id']}: references unknown requirement {r}")
        if t["owner"] == t["reviewer"]:
            errs.append(f"{t['id']}: owner and reviewer must differ")
        if t["status"] == "done":
            if not t.get("completion_note"):
                errs.append(f"{t['id']}: done but completion_note is empty")
            if not t.get("evidence"):
                errs.append(f"{t['id']}: done but evidence is empty")
            for dep in t.get("depends_on", []):
                if by_id.get(dep, {}).get("status") != "done":
                    errs.append(f"{t['id']}: done while dependency {dep} is not done")
        if t.get("review_cycles", 0) > MAX_REVIEW_CYCLES:
            errs.append(
                f"{t['id']}: review_cycles > {MAX_REVIEW_CYCLES}; "
                "a human question is required (anti-loop rule)"
            )

    # requirement coverage consistency
    for r in d.get("requirements", []):
        expected = sorted(
            t["id"] for t in d.get("tasks", []) if r["id"] in t.get("requirements", [])
        )
        if sorted(r.get("covered_by", [])) != expected:
            errs.append(
                f"{r['id']}: covered_by {sorted(r.get('covered_by', []))} "
                f"does not match tasks {expected}"
            )
        if r["status"] == "done":
            for tid in expected:
                if by_id[tid]["status"] != "done":
                    errs.append(f"{r['id']}: marked done but task {tid} is not done")

    # log consistency
    log = d.get("log", [])
    revs = [e["revision"] for e in log]
    if revs != sorted(revs):
        errs.append("log revisions are not non-decreasing")
    if log and log[-1]["revision"] != d["revision"]:
        errs.append(
            f"last log revision ({log[-1]['revision']}) != top-level revision ({d['revision']})"
        )
    for e in log:
        for tid in e.get("task_ids", []):
            if tid not in task_ids:
                errs.append(f"log rev {e['revision']}: unknown task {tid}")

    # validations: a pass must have evidence
    for v in d.get("validations", []):
        if v["result"] in ("pass", "fail") and not v.get("evidence"):
            errs.append(f"{v['id']}: {v['result']} without evidence")

    # decisions: final decisions that are not to be reopened must stay final
    for dec in d.get("decisions", []):
        if dec["status"] == "superseded" and dec.get("do_not_reopen"):
            errs.append(f"{dec['id']}: do_not_reopen decision cannot be superseded (ask the human)")

    # turn / open human questions
    open_human = [
        q["id"] for q in d.get("questions", []) if q["to"] == "human" and q["status"] == "open"
    ]
    if d["turn"] == "human" and not open_human and not d.get("blockers"):
        errs.append("turn is 'human' but there is no open question or blocker for the human")

    # answered questions need an answer
    for q in d.get("questions", []):
        if q["status"] == "answered" and not q.get("answer"):
            errs.append(f"{q['id']}: answered without an answer")

    return errs


def append_only_errors(old: dict, new: dict) -> list[str]:
    errs: list[str] = []
    if new["revision"] < old["revision"]:
        errs.append(
            f"revision cannot decrease (old {old['revision']}, new {new['revision']})"
        )
    if new["log"][: len(old["log"])] != old["log"]:
        errs.append("log is append-only: earlier entries were changed or removed")
    else:
        appended = new["log"][len(old["log"]):]
        revision_delta = new["revision"] - old["revision"]
        if revision_delta != len(appended):
            errs.append(
                "revision increase must match the number of appended handoff entries "
                f"(delta {revision_delta}, entries {len(appended)})"
            )
        expected_revisions = list(range(old["revision"] + 1, new["revision"] + 1))
        if [entry["revision"] for entry in appended] != expected_revisions:
            errs.append("appended log revisions must be contiguous")
        expected_turn = old["turn"]
        for entry in appended:
            if entry["agent"] != expected_turn:
                errs.append(
                    f"log revision {entry['revision']}: expected handoff agent {expected_turn}, "
                    f"found {entry['agent']}"
                )
            expected_turn = "claude" if expected_turn == "codex" else "codex"
        if revision_delta and new["turn"] != expected_turn:
            errs.append("turn does not match the appended handoff sequence")
    old_dec = {x["id"]: x for x in old["decisions"]}
    for x in new["decisions"]:
        o = old_dec.get(x["id"])
        if (
            o
            and o.get("do_not_reopen")
            and (o["statement"] != x["statement"] or x["status"] != o["status"])
        ):
            errs.append(f"{x['id']}: do_not_reopen decision was modified")
    for o in old["decisions"]:
        if o["id"] not in {x["id"] for x in new["decisions"]}:
            errs.append(f"{o['id']}: decision was deleted (mark it superseded instead)")
    old_val = {x["id"]: x for x in old["validations"]}
    for x in new["validations"]:
        o = old_val.get(x["id"])
        if o and o["result"] == "pass" and x["result"] != "pass" and not x.get("reverify_if"):
            errs.append(f"{x['id']}: passed validation changed without reverify_if")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(HERE / "AGENT_HANDOFF.json"))
    ap.add_argument("--previous", help="previous version of the file, to check append-only rules")
    args = ap.parse_args()

    try:
        data = load(Path(args.file))
    except Exception as exc:  # noqa: BLE001
        print(f"cannot read {args.file}: {exc}")
        return 1

    errors = schema_errors(data)
    hard = [e for e in errors if not e.startswith("(")]
    notes = [e for e in errors if e.startswith("(")]
    if not hard:
        errors = logic_errors(data)
    else:
        errors = hard
    if args.previous and not errors:
        errors += append_only_errors(load(Path(args.previous)), data)

    for n in notes:
        print(n)
    if errors:
        print(f"INVALID ({len(errors)} problem(s)):")
        for e in errors:
            print(" -", e)
        return 1
    print(
        f"OK: revision {data['revision']}, turn={data['turn']}, tasks={len(data['tasks'])}, "
        f"open questions={sum(1 for q in data['questions'] if q['status'] == 'open')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
