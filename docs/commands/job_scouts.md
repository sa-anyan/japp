# Job Scout Commands

Run these commands from the project root. They use the project virtual environment and save workbooks under `job_scouts/outputs/v8_1/`.

## Install dependencies

```sh
.venv/bin/python -m pip install -r requirements.txt
```

## Full UK search

```sh
.venv/bin/python job_scouts/japp_v8_1_multisource.py \
  --sites indeed,reed,totaljobs \
  --fresh \
  --out "job_scouts/outputs/v8_1/uk_jobs_$(date +%Y-%m-%d).xlsx"
```

## LinkedIn search

LinkedIn is capped at 20 results per keyword to reduce request volume.

```sh
.venv/bin/python job_scouts/japp_v8_1_multisource.py \
  --sites linkedin \
  --linkedin-results 20 \
  --fresh \
  --out "job_scouts/outputs/v8_1/linkedin_jobs_$(date +%Y-%m-%d).xlsx"
```

## Quick UK test

```sh
.venv/bin/python job_scouts/japp_v8_1_multisource.py \
  --sites indeed,reed,totaljobs \
  --keyword-limit 3 \
  --fresh \
  --out "job_scouts/outputs/v8_1/test_uk_$(date +%Y-%m-%d).xlsx"
```

For a full sweep, omit `--keyword-limit`. `--fresh` forces every keyword to be searched again. CV-Library and GOV.UK Find a Job are omitted from these commands because previous runs returned HTTP 403 and HTTP 503, respectively.
