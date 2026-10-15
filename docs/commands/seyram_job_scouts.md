# Seyram Job Scout Commands

Run these commands from the project root. Results are saved under `job_scouts/outputs/seyram_v8_1/` by default.

## First run

```sh
.venv/bin/python job_scouts/japp_v8_1_seyram_multisource.py --fresh
```

## Subsequent runs

The scout keeps existing results and adds new unique jobs:

```sh
.venv/bin/python job_scouts/japp_v8_1_seyram_multisource.py
```

## Include LinkedIn

```sh
.venv/bin/python job_scouts/japp_v8_1_seyram_multisource.py \
	--sites indeed,reed,totaljobs,cvlibrary,findajob,linkedin
```

For a clean first run with LinkedIn, add `--fresh` to the command.
