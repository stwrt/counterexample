# Counterexample

Two functions, each with a passing unit-test suite, checked against every input
in range by Z3.

| Function | Unit tests | What the solver finds |
| --- | --- | --- |
| `split_bill` (ledger) | 4 of 4 pass | $10.11 split 47 ways gives one person -1¢; 97 of 100 group sizes break "fair to the cent" |
| `dose_mg` (clinical) | 4 of 4 pass | a 66.7 lb patient gets 1,000 mg of acetaminophen, 2.2× the weight-based dose |

The fixed versions prove every property for every input in range. Every
counterexample is replayed against the real Python function before it is
reported, because the Z3 models are hand translations of that code.

## Run it

```sh
pip install -r requirements.txt
uvicorn app:app --port 8000
```

Or from the command line, one JSON event per line:

```sh
python engine.py split 1 100   # case, version (1 shipped, 2 fixed), max group size
python engine.py dose 2 0
```

## Layout

- `code_under_test.py`: the functions and their unit tests. The page shows this source.
- `engine.py`: the Z3 models and the replay. Each run is its own process.
- `app.py`: FastAPI. `/api/run` streams a run as server-sent events.

## Deploy

`mojave.json` runs it as a Python service on [Mojave](https://mojave.sh):
connect the repo and click Deploy.

## Limits

- The dose model uses exact rational arithmetic, not IEEE-754 doubles.
- Formulary values are illustrative, not dosing guidance.
