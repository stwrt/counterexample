"""One verification run, as a child process that prints JSON lines.

    python engine.py split 1 100

Each run gets its own process because z3's Python bindings share one global
context: concurrent runs in one interpreter would corrupt each other, and a
child can be killed the moment its viewer disconnects.

The Z3 models below are hand translations of code_under_test.py. That gap is
why every counterexample is replayed against the real Python function before
it is reported.
"""

from __future__ import annotations

import json
import sys
import time
from fractions import Fraction

from z3 import (And, BoolVal, If, Implies, Int, Not, Optimize, Q, RealVal,
                Solver, Sum, ToReal, sat, unsat)

import code_under_test as cut

CHECK_TIMEOUT_MS = 20_000


def emit(**event):
    print(json.dumps(event), flush=True)


def check(pre, prop):
    """Is there an input satisfying pre that breaks prop?"""
    s = Solver()
    s.set("timeout", CHECK_TIMEOUT_MS)
    s.add(pre, Not(prop))
    r = s.check()
    if r == unsat:
        return "proved"
    if r == sat:
        return "violated"
    return "unknown"


def zabs(x):
    return If(x >= 0, x, -x)


# ---------------------------------------------------------------- split_bill

SPLIT_SPECS = [
    ("conserve", "No money created or lost", "sum(shares) == total_cents"),
    ("nonneg", "Nobody is paid by a split", "every share >= 0"),
    ("fair", "Fair to the cent", "max(shares) - min(shares) <= 1"),
]


def split_model(people: int, version: int):
    """Returns (total_cents var, precondition, {spec: prop}) for a fixed group size.

    Shares are (value, count) runs, mirroring the list-repetition in the code.
    Fixing `people` per query keeps the arithmetic linear (bar v2's run
    lengths), so each query is fast and the run can stream case by case.
    """
    t = Int("total_cents")
    pre = And(t >= 1_000, t <= 1_000_000_000)  # $10.00 to $10,000,000.00
    p = people
    if version == 1:
        # round(t / p): Python rounds half to even. For these magnitudes the
        # float t / p is exactly k + 0.5 whenever the rational is, and at
        # least 1/(2p) away from .5 otherwise, so the exact model is faithful.
        q, r = t / p, t % p
        share = If(2 * r > p, q + 1, If(2 * r < p, q, If(q % 2 == 0, q, q + 1)))
        runs = [(share, p - 1), (t - share * (p - 1), 1)]
    else:
        base, extra = t / p, t % p
        runs = [(base + 1, extra), (base, p - extra)]

    # A run with count 0 contributes no shares; c > 0 is a Python bool for
    # literal counts and a z3 term for v2's symbolic ones.
    total = Sum([v * c for v, c in runs])
    nonneg = And([Implies(c > 0, v >= 0) for v, c in runs])
    pairs = [(a, b) for i, a in enumerate(runs) for b in runs[i + 1:]]
    fair = And([Implies(And(a[1] > 0, b[1] > 0), zabs(a[0] - b[0]) <= 1)
                for a, b in pairs]) if pairs else BoolVal(True)
    return t, pre, {"conserve": total == t, "nonneg": nonneg, "fair": fair}


def compress(shares: list[int]) -> str:
    out, i = [], 0
    while i < len(shares):
        j = i
        while j < len(shares) and shares[j] == shares[i]:
            j += 1
        n = j - i
        out.append(f"{shares[i]}" if n == 1 else f"{shares[i]} ×{n}")
        i = j
    return "[" + ", ".join(out) + "]"


def cents(c: int) -> str:
    sign = "-" if c < 0 else ""
    c = abs(c)
    return f"{sign}${c // 100:,}.{c % 100:02d}"


def split_replay(version: int, spec: str, total: int, people: int):
    fn = cut.split_bill_v1 if version == 1 else cut.split_bill_v2
    shares = fn(total, people)
    holds = {
        "conserve": sum(shares) == total,
        "nonneg": min(shares) >= 0,
        "fair": max(shares) - min(shares) <= 1,
    }[spec]
    lo, hi = min(shares), max(shares)
    why = {
        "conserve": f"shares add up to {cents(sum(shares))}, not {cents(total)}",
        "nonneg": f"one share is {cents(lo)}: the split pays that person",
        "fair": f"shares range from {cents(lo)} to {cents(hi)}, {hi - lo}¢ apart",
    }[spec]
    return {
        "call": f"split_bill({total}, {people})",
        "plain": f"{cents(total)} split {people} ways",
        "got": compress(shares),
        "why": why,
        "reproduced": not holds,
    }


def run_split(version: int, bound: int):
    found: set[str] = set()
    counts = {k: {"proved": 0, "violated": 0, "unknown": 0} for k, _, _ in SPLIT_SPECS}
    for people in range(1, bound + 1):
        t, pre, props = split_model(people, version)
        for spec, _, _ in SPLIT_SPECS:
            t0 = time.perf_counter()
            result = check(pre, props[spec])
            counts[spec][result] += 1
            emit(type="case", spec=spec, index=people - 1, label=f"people = {people}",
                 result=result, ms=round((time.perf_counter() - t0) * 1000, 1))
            if result == "violated" and spec not in found:
                found.add(spec)
                # Smallest total that breaks it, so the example reads like a real bill.
                o = Optimize()
                o.set("timeout", CHECK_TIMEOUT_MS)
                o.add(pre, Not(props[spec]))
                o.minimize(t)
                o.check()
                total = o.model()[t].as_long()
                emit(type="counterexample", spec=spec,
                     **split_replay(version, spec, total, people))
    return counts


# ------------------------------------------------------------------- dose_mg

# Illustrative values for the demo, not dosing guidance.
FORMULARY = [
    ("Acetaminophen", 15, 1000),
    ("Ibuprofen", 10, 400),
    ("Amoxicillin", 25, 1000),
    ("Ondansetron", Fraction(15, 100), 8),
]
UNITS = [("kg", 10, 2_000), ("lb", 22, 4_400)]  # weight in tenths: 1–200 kg, 2.2–440 lb

DOSE_SPECS = [
    ("cap", "Never above the max single dose", "dose <= max_mg"),
    ("over", "Never above the weight-based dose", "dose <= kg × mg_per_kg"),
    ("under", "Never below the ordered dose", "dose >= min(kg × mg_per_kg, max_mg)"),
]


def real(x) -> object:
    x = Fraction(x)
    return Q(x.numerator, x.denominator)


def dose_model(version: int, unit: str, lo: int, hi: int, mg_per_kg, max_mg):
    """Exact rational arithmetic: the model ignores IEEE-754 rounding."""
    n = Int("weight_tenths")
    w = ToReal(n) / 10
    pre = And(n >= lo, n <= hi)
    kg = w * real(cut.LB_TO_KG) if unit == "lb" else w
    raw = (kg if version == 2 else w) * real(mg_per_kg)
    mx = RealVal(max_mg)
    dose = If(raw < mx, raw, mx)
    ideal = kg * real(mg_per_kg)
    ordered = If(ideal < mx, ideal, mx)
    return n, pre, dose, ideal, {"cap": dose <= mx, "over": dose <= ideal, "under": dose >= ordered}


def dose_replay(version: int, spec: str, drug: str, weight: float, unit: str, mg_per_kg, max_mg):
    fn = cut.dose_mg_v1 if version == 1 else cut.dose_mg_v2
    got = fn(weight, unit, float(mg_per_kg), max_mg)
    kg = weight * cut.LB_TO_KG if unit == "lb" else weight
    ideal = kg * float(mg_per_kg)
    ordered = min(ideal, max_mg)
    eps = 1e-9 * max(1.0, ideal)
    holds = {"cap": got <= max_mg + eps, "over": got <= ideal + eps, "under": got >= ordered - eps}[spec]
    why = {
        "cap": f"returns {got:,.1f} mg, above the {max_mg:,} mg max",
        "over": f"returns {got:,.1f} mg; the weight-based dose is {ideal:,.1f} mg ({got / ideal:.1f}×)",
        "under": f"returns {got:,.1f} mg; the order calls for {ordered:,.1f} mg",
    }[spec]
    mg = f"{float(mg_per_kg):g}"
    return {
        "call": f'dose_mg({weight:g}, "{unit}", {mg}, {max_mg})',
        "plain": f"{drug}, {weight:g} {unit} patient",
        "got": f"{got:,.1f} mg",
        "why": why,
        "reproduced": not holds,
    }


def run_dose(version: int, _bound: int):
    found: set[str] = set()
    counts = {k: {"proved": 0, "violated": 0, "unknown": 0} for k, _, _ in DOSE_SPECS}
    index = 0
    for drug, mg_per_kg, max_mg in FORMULARY:
        for unit, lo, hi in UNITS:
            n, pre, dose, ideal, props = dose_model(version, unit, lo, hi, mg_per_kg, max_mg)
            for spec, _, _ in DOSE_SPECS:
                t0 = time.perf_counter()
                result = check(pre, props[spec])
                counts[spec][result] += 1
                emit(type="case", spec=spec, index=index, label=f"{drug}, {unit}",
                     result=result, ms=round((time.perf_counter() - t0) * 1000, 1))
                if result == "violated" and spec not in found:
                    found.add(spec)
                    # Worst case: the largest overdose the solver can find.
                    o = Optimize()
                    o.set("timeout", CHECK_TIMEOUT_MS)
                    o.add(pre, Not(props[spec]))
                    o.maximize(dose - ideal if spec != "under" else ideal - dose)
                    o.check()
                    weight = o.model()[n].as_long() / 10
                    emit(type="counterexample", spec=spec,
                         **dose_replay(version, spec, drug, weight, unit, mg_per_kg, max_mg))
            index += 1
    return counts


def main():
    case, version, bound = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    t0 = time.perf_counter()
    counts = (run_split if case == "split" else run_dose)(version, bound)
    emit(type="done", counts=counts, ms=round((time.perf_counter() - t0) * 1000))


if __name__ == "__main__":
    main()
