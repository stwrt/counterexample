"""The code under test.

The page displays these functions' real source, the unit tests call them, and
every counterexample the solver finds is replayed against them. v1 is what
shipped; v2 is the fix.
"""

LB_TO_KG = 0.45359237


def split_bill_v1(total_cents: int, people: int) -> list[int]:
    share = round(total_cents / people)
    last = total_cents - share * (people - 1)
    return [share] * (people - 1) + [last]


def split_bill_v2(total_cents: int, people: int) -> list[int]:
    base, extra = divmod(total_cents, people)
    return [base + 1] * extra + [base] * (people - extra)


def dose_mg_v1(
    weight: float, unit: str, mg_per_kg: float, max_mg: float
) -> float:
    kg = weight * LB_TO_KG if unit == "lb" else weight
    return min(weight * mg_per_kg, max_mg)


def dose_mg_v2(
    weight: float, unit: str, mg_per_kg: float, max_mg: float
) -> float:
    kg = weight * LB_TO_KG if unit == "lb" else weight
    return min(kg * mg_per_kg, max_mg)


# Unit tests. Every one of them passes against both versions.

def test_even_split(split_bill):
    assert split_bill(9000, 3) == [3000, 3000, 3000]


def test_remainder_cent(split_bill):
    shares = split_bill(10000, 3)
    assert sum(shares) == 10000
    assert max(shares) - min(shares) <= 1


def test_two_people_odd_total(split_bill):
    assert sorted(split_bill(10001, 2)) == [5000, 5001]


def test_one_person(split_bill):
    assert split_bill(4250, 1) == [4250]


def test_adult_hits_cap(dose_mg):
    assert dose_mg(80, "kg", 15, 1000) == 1000


def test_child_in_kg(dose_mg):
    assert dose_mg(20, "kg", 15, 1000) == 300


def test_adult_in_pounds(dose_mg):
    assert dose_mg(190, "lb", 15, 1000) == 1000


def test_ibuprofen_child(dose_mg):
    assert dose_mg(18, "kg", 10, 400) == 180
