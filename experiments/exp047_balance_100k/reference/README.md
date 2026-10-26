# Exp047 reference input

`workload.csv.gz` decompresses to the exact 100,000-CTX workload used by the formal exp047 balance experiment started on 2026-10-24. The SHA-256 of decompressed bytes is recorded in `manifest.json` and checked before scheduling any tasks. `config.json` contains its base parameters. The frozen historical Fig5c runtime is stored at `experiments/exp045_plain_sensitivity_supplement/runtime/c` and checked against its provenance manifest.

These inputs are shared by all policies, balances and rounds. Per-round route seeds remain 7,17,...,97. No new synthetic workload is substituted.
