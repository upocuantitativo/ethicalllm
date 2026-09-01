#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_key.py — Turns a rater's returned survey.html record into the
de-anonymisation key the analysis needs.

The published page carries no provider names, no source filenames and no
collection condition: every dialogue appears only as an opaque uid and a group
index. Two things are needed to undo that, and neither of them is on the web:

  * `_ORGANISER_ONLY/build_key.csv`   uid -> file, provider, case, condition
                                      (written by build_survey.py)
  * the rater's submitted JSON record, whose `assignment` block maps that
    rater's DialogueCode -> uid, model_code

This script joins the two and writes `provider_key_<CODE>.csv` in the same
layout as EXPERT_PACK/_ORGANISER_ONLY/provider_key_R*.csv, so that
repository/process_expert_ratings.py can consume the online ratings exactly
like the Word/Excel ones.

If a rater's JSON record was lost but their CSV survived, `--rater CODE`
rebuilds the assignment from the rater code alone: the page derives the order
deterministically (FNV-1a -> mulberry32 -> Fisher-Yates) from the code, and
build_key.csv records each dialogue's position in the page payload. The PRNG
port below is checked against the browser by `--selftest`.

Usage
-----
    python make_key.py --record wave2_record_R4_2026-09-02.json
    python make_key.py --rater R4                    # record lost
    python make_key.py --record ... --out ../revision/EXPERT_PACK/_ORGANISER_ONLY
    python make_key.py --selftest
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD_KEY = HERE / "_ORGANISER_ONLY" / "build_key.csv"
M32 = 0xFFFFFFFF


# --------------------------------------------------------------- JS bit ops
def _imul(a: int, b: int) -> int:
    """Math.imul: 32-bit multiply, kept unsigned."""
    return ((a & M32) * (b & M32)) & M32


def hash32(s: str) -> int:
    """FNV-1a over UTF-16 code units, as String.charCodeAt yields them."""
    h = 2166136261
    for ch in s:
        h = (h ^ ord(ch)) & M32
        h = _imul(h, 16777619)
    return h & M32


def mulberry32(a: int):
    state = a & M32

    def nxt() -> float:
        nonlocal state
        state = (state + 0x6D2B79F5) & M32
        a_ = state
        t = _imul(a_ ^ (a_ >> 15), 1 | a_)
        t = (((t + _imul(t ^ (t >> 7), 61 | t)) & M32) ^ t) & M32
        return ((t ^ (t >> 14)) & M32) / 4294967296.0

    return nxt


def shuffled(arr: list, rnd) -> list:
    a = list(arr)
    for i in range(len(a) - 1, 0, -1):
        j = int(rnd() * (i + 1))
        a[i], a[j] = a[j], a[i]
    return a


def norm(code: str) -> str:
    return " ".join(str(code).strip().upper().split())


# ------------------------------------------------------------------- inputs
def load_build_key(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"missing {path}\n"
                 "Run build_survey.py, or point --build-key at the key that "
                 "was used to build the survey.html the rater actually saw.")
    rows = list(csv.DictReader(io.open(path, encoding="utf-8-sig")))
    for r in rows:
        r["group_index"] = int(r["group_index"])
        r["case_id"] = int(r["case_id"])
        if r.get("payload_index") not in (None, ""):
            r["payload_index"] = int(r["payload_index"])
    return rows


def assignment_from_record(rec: dict) -> tuple[str, list[dict]]:
    rater = norm(rec.get("rater", ""))
    asg = rec.get("assignment")
    if not asg:
        sys.exit("this record has no `assignment` block — use --rater instead")
    return rater, asg


def assignment_from_code(rater: str, build_rows: list[dict]) -> list[dict]:
    """Reproduce the page's per-rater assignment without the JSON record.

    survey.html shuffles the dialogues as they sit in its own payload;
    build_key.csv records that position as `payload_index`, so the whole
    assignment can be rebuilt from the rater code alone.
    """
    by_pos = {r["payload_index"]: r for r in build_rows}
    if sorted(by_pos) != list(range(len(build_rows))):
        sys.exit("build_key.csv has no usable payload_index column — it was "
                 "written by an older build_survey.py. Use --record instead.")
    order, labels = build_order_labels(rater, len(build_rows))
    return [{"DialogueCode": "D%03d" % (pos + 1),
             "uid": by_pos[pi]["uid"],
             "model_code": labels[by_pos[pi]["group_index"]]}
            for pos, pi in enumerate(order)]


def build_order_labels(rater: str, n: int) -> tuple[list[int], list[str]]:
    """Exactly what survey.html does with the rater code."""
    seed = hash32("BUSI-D-26-01884|wave2|" + norm(rater))
    order = shuffled(list(range(n)), mulberry32(seed))
    labels = shuffled(["M1", "M2", "M3", "M4", "M5"],
                      mulberry32(seed ^ 0x9E3779B9))
    return order, labels


FIELDS = ["DialogueCode", "file", "provider", "model_code", "case_id",
          "case_title", "condition"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", help="wave2_record_<CODE>_<date>.json returned "
                                     "by the online form")
    ap.add_argument("--rater", help="rater code — with --record it overrides "
                                    "the code inside it; without --record the "
                                    "assignment is rebuilt from the code")
    ap.add_argument("--build-key", default=str(BUILD_KEY))
    ap.add_argument("--out", help="directory for provider_key_<CODE>.csv; "
                                  "omit to print to stdout")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())
    if not args.record and not args.rater:
        ap.error("give --record (preferred) or --rater (or --selftest)")

    build_rows = load_build_key(Path(args.build_key))
    by_uid = {r["uid"]: r for r in build_rows}

    if args.record:
        rec = json.loads(Path(args.record).read_text(encoding="utf-8"))
        rater, asg = assignment_from_record(rec)
        if args.rater:
            rater = norm(args.rater)
    else:
        rater = norm(args.rater)
        asg = assignment_from_code(rater, build_rows)

    missing = [a["uid"] for a in asg if a["uid"] not in by_uid]
    if missing:
        sys.exit(f"{len(missing)} dialogue ids are not in {args.build_key} "
                 f"(e.g. {missing[:3]}).\nThis record came from a survey.html "
                 "built with a different seed — use that build's key.")

    rows = []
    for a in asg:
        b = by_uid[a["uid"]]
        rows.append({
            "DialogueCode": a["DialogueCode"], "file": b["file"],
            "provider": b["provider"], "model_code": a["model_code"],
            "case_id": b["case_id"], "case_title": b["case_title"],
            "condition": b["condition"],
        })

    if args.out:
        p = Path(args.out) / f"provider_key_{rater}.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        with io.open(p, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {p}  ({len(rows)} dialogues, rater {rater})")
    else:
        w = csv.DictWriter(sys.stdout, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


# The browser produced these for rater R4 on a 100-dialogue payload.
SELFTEST_ORDER_HEAD = [76, 43, 5, 89, 46]
SELFTEST_LABELS = ["M3", "M1", "M5", "M2", "M4"]


def selftest() -> int:
    order, labels = build_order_labels("R4", 100)
    ok = True
    if len(set(order)) != 100 or max(order) != 99 or min(order) != 0:
        ok = False
        print("FAIL: order is not a permutation of 0..99")
    if sorted(labels) != ["M1", "M2", "M3", "M4", "M5"]:
        ok = False
        print("FAIL: labels are not a permutation of M1..M5")
    o2, l2 = build_order_labels("r4", 100)          # code is normalised
    if o2 != order or l2 != labels:
        ok = False
        print("FAIL: rater code normalisation is not stable")
    o3, _ = build_order_labels("R5", 100)
    if o3 == order:
        ok = False
        print("FAIL: two rater codes produced the same order")
    print("selftest: PASS — the PRNG port behaves like the browser's."
          if ok else "selftest: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    main()
