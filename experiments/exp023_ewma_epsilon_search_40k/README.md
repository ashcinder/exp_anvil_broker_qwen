# Experiment 23: EWMA epsilon sensitivity

This experiment varies only the EWMA epsilon threshold. It uses the clean ETH
trace and keeps the other EWMA/TDR settings at their experiment 22 baseline.

## Parameters for review

- Epsilon: 0.05 through 0.95, step 0.05 (19 settings, including baseline 0.95).
- Independent repetitions: 10 per epsilon setting.
- Traffic: 40,000 CTX per arm per repetition (50 brokers × 800 CTX).
- Other EWMA/TDR settings fixed: half-life 20 blocks, `q_min=0.10`, trigger
  interval `X=10` blocks, finite window 20 blocks, warm-up 5 blocks.
- Five comparison arms are retained; only the EWMA epsilon dimension is swept.
- Planned target-arm volume: 19 × 10 × 40,000 = 7,600,000 EWMA-arm CTX. The
  four comparison arms are cached across epsilon settings; approximately
  1,600,000 additional CTX are needed for their 10 repetitions.

## Review before starting

From the repository root, inspect the execution plan without running it:

```powershell
python experiments/exp023_ewma_epsilon_search_40k/run.py --dry-run
```

The formal experiment is not started when preparing this configuration.

## Start and resume

After approving the parameters:

```powershell
python experiments/exp023_ewma_epsilon_search_40k/run.py
```

If interrupted, resume from its output run directory:

```powershell
python experiments/exp023_ewma_epsilon_search_40k/run.py --resume experiments/exp023_ewma_epsilon_search_40k/out/<RUN_ID>
```

After successful completion, the runner generates PNG and PDF boxplots under
`<RUN_DIR>/figs/reference_style/fig6a_epsilon_exp023.*`. Each box summarizes
the 10 valid replicate values for TDR transfers and Relay fallbacks.
