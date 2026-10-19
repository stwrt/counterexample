from __future__ import annotations

import asyncio
import inspect
import json
import re
import sys
from pathlib import Path

import z3
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

import code_under_test as cut
import engine

HERE = Path(__file__).parent
MAX_CONCURRENT_RUNS = 4
runs = asyncio.Semaphore(MAX_CONCURRENT_RUNS)

app = FastAPI(title="Counterexample")


def shown_source(fn) -> str:
    """The function's real source, with the _v1/_v2 suffix dropped."""
    return re.sub(r"_v\d\(", "(", inspect.getsource(fn), count=1).rstrip()


def changed_lines(a: str, b: str) -> list[int]:
    la, lb = a.splitlines(), b.splitlines()
    return [i for i, line in enumerate(la) if i >= len(lb) or line != lb[i]]


def run_tests(tests, fn):
    results = []
    for t in tests:
        try:
            t(fn)
            results.append({"name": t.__name__, "passed": True})
        except AssertionError as e:
            results.append({"name": t.__name__, "passed": False, "error": str(e) or "assertion failed"})
    return results


def describe(case_id, title, kicker, domain, v1, v2, tests, specs, cases, bounds, note=""):
    s1, s2 = shown_source(v1), shown_source(v2)
    return {
        "id": case_id,
        "title": title,
        "kicker": kicker,
        "domain": domain,
        "note": note,
        "versions": {
            "1": {"source": s1, "changed": changed_lines(s1, s2), "tests": run_tests(tests, v1)},
            "2": {"source": s2, "changed": changed_lines(s2, s1), "tests": run_tests(tests, v2)},
        },
        "tests": [inspect.getsource(t).rstrip() for t in tests],
        "specs": [{"id": i, "title": t, "formula": f} for i, t, f in specs],
        "cases": cases,
        "bounds": bounds,
    }


def catalog():
    return [
        describe(
            "split", "Split a bill", "Ledger",
            "for every total from $10.00 to $10,000,000.00 and every group size from 1 to N",
            cut.split_bill_v1, cut.split_bill_v2,
            [cut.test_even_split, cut.test_remainder_cent, cut.test_two_people_odd_total, cut.test_one_person],
            engine.SPLIT_SPECS, "group size", [100, 1000],
        ),
        describe(
            "dose", "Weight-based dose", "Clinical",
            "for every weight from 1.0 to 200.0 kg or 2.2 to 440.0 lb (0.1 steps), "
            f"for {len(engine.FORMULARY)} formulary drugs in both units",
            cut.dose_mg_v1, cut.dose_mg_v2,
            [cut.test_adult_hits_cap, cut.test_child_in_kg, cut.test_adult_in_pounds, cut.test_ibuprofen_child],
            engine.DOSE_SPECS, "drug × unit", [len(engine.FORMULARY) * len(engine.UNITS)],
            note="Doses here are illustrative values for a code example, not dosing guidance.",
        ),
    ]


CATALOG = catalog()
BY_ID = {c["id"]: c for c in CATALOG}


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.get("/api/cases")
def cases():
    return {
        "cases": CATALOG,
        "formulary": [{"drug": d, "mgPerKg": float(m), "maxMg": x} for d, m, x in engine.FORMULARY],
        "runtime": {"python": sys.version.split()[0], "z3": z3.get_version_string()},
    }


@app.get("/api/run")
async def run(case: str, version: int = Query(ge=1, le=2), bound: int = 0):
    c = BY_ID.get(case)
    if c is None or (bound and bound not in c["bounds"]):
        raise HTTPException(400, "unknown case or bound")
    bound = bound or c["bounds"][0]

    async def events():
        if runs.locked():
            yield "data: " + json.dumps({"type": "queued"}) + "\n\n"
        async with runs:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(HERE / "engine.py"), case, str(version), str(bound),
                cwd=HERE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                yield "data: " + json.dumps({"type": "start", "bound": bound}) + "\n\n"
                async for line in proc.stdout:
                    yield "data: " + line.decode().strip() + "\n\n"
                if await proc.wait() != 0:
                    err = (await proc.stderr.read()).decode()[-400:]
                    yield "data: " + json.dumps({"type": "error", "error": err}) + "\n\n"
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")
