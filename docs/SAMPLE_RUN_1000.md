# Sample Run for 1000 Companies

This is a reproducible sample execution of the same SignalPost pipeline used by `run.py`.
It is provided so the hackathon evaluator can see that the agent has been run against the
complete 1000-company input set.

## Input

The runner reads all 1000 organisation numbers from:

```text
1000-companies.jsonl
```

The file has a `.jsonl` name for compatibility with the submission, but its current contents
are a JSON array. The runner validates that exactly 1000 companies are present before starting.

## Run

From the repository root:

```powershell
python sample_run_1000.py
```

The script invokes the same `scripts/run_agent.py` pipeline as `run.py`. It keeps intermediate
files in `out/sample-run-1000` while running and removes that temporary directory after a
successful run.

## Output

The final 1000-company envelope output is saved at:

```text
result/sample-output-for-1000.jsonl
```

Each output line is one company envelope. A successful run therefore produces 1000 non-empty
JSON lines. The output is intentionally separate from the normal `result/envelopes.jsonl`
artifact produced by `run.py`.

To verify the output count:

```powershell
(Get-Content result/sample-output-for-1000.jsonl | Where-Object { $_.Trim() -ne '' }).Count
```

The expected result is `1000`.