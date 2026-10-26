"""Fixed, matched-condition protocol for the single trade-off figure."""
PROTOCOL="exp043-unified-tradeoff-v1"
ALIASES={"prop_hard":"proportional","prop_ewma":"proportional","topup_hard":"topup"}
METHODS={
 "plain":("Plain","plain@45"),
 "prop_hard":("Proportional + Hard window","prop_hard@45@@0.95@10@0@0.3"),
 "prop_ewma":("Proportional + EWMA","prop_ewma@45@@0.95@10@20@0.3"),
 "topup_hard":("Top-up + Hard window","topup_hard@45@@0.95@10@0@0.3"),
 "topup":("Top-up + EWMA","topup@45@@0.95@10@20@0.3"),
 "topup_e0":("Top-up + EWMA (epsilon=0)","topup_e0@45@@0@10@20@0.3"),
 "flexichain":("Flexichain","flexichain@45"),
 "loopmax":("Loopmax","loopmax@45@@0@10@20@0"),
 "spider":("Spider-rebalance","spider@45"),
 "pnps":("PnP-static","pnps@45"),
 "autoloop":("Autoloop","autoloop@45"),
 "overpass_u":("Overpass-uniform","overpass_u@45@@0"),
 "overpass_d":("Overpass-demand","overpass_d@45@@0"),
 "ucrb":("UCRB","ucrb@45")}

def register(pipeline):pipeline._ARM_ENGINE.update(ALIASES)

def validate_protocol(cfg,smoke=False,analysis=False):
 required={"tdr_window_blocks":20,"tdr_epsilon":.95,"tdr_q_min":.3,"tdr_chi_blocks":10,
  "tdr_timeout_blocks":20,"tdr_ewma_half_life":20.,"tdr_ewma_min_blocks":20,
  "tdr_ewma_window_blocks":0,"tdr_ewma_two_sided":False,"sota_training_ctx":12000,
  "model_income_per_service":.1,"model_cost_per_transfer":1.0,"route_seed":7,"route_seed_step":10}
 for k,v in required.items():
  if cfg.exp.get(k)!=v:raise ValueError(f"Protocol mismatch {k}: expected {v}, got {cfg.exp.get(k)}")
 if cfg.label!='exp043_unified_tradeoff':raise ValueError('Not an exp043 config')
 if cfg.chain.num_shards!=16 or cfg.exp['balances_eth']!=45 or cfg.broker.initial_balance_eth!=45:
  raise ValueError('Requires 16 shards and 45 ETH per subaccount')
 if cfg.b2e.enabled:raise ValueError('Protocol uses unchanged traffic and an explicit post-hoc economic model')
 if cfg.exp['arms'].split(',')!=[v[1] for v in METHODS.values()]:raise ValueError('Requires the fixed 14-arm order and exact matched parameters')
 if not smoke and (cfg.scale.num_brokers!=50 or cfg.scale.num_users!=200 or cfg.exp['ctx_per_broker']!=800):
  raise ValueError('Formal/analysis requires 50 brokers x 800 = 40000 CTX and 200 users')
 if not smoke and not analysis and cfg.exp['repetitions']!=3:raise ValueError('Formal requires three paired rounds')
