"""Reviewable effective parameters and the two user-selected objectives."""
from decimal import Decimal


def evaluation_metrics(relay, adjustment, income, cost_each, brokers):
    relay, adjustment, income, cost_each, brokers = map(int, (relay, adjustment, income, cost_each, brokers))
    if min(relay,adjustment,income,cost_each)<0 or brokers<1:
        raise ValueError("Invalid count, fee or broker number")
    profit=income-adjustment*cost_each
    return dict(relay_ctx=relay,adjustment_ctx=adjustment,
                relay_plus_adjustment_ctx=relay+adjustment,
                broker_total_profit_wei=profit,
                broker_mean_profit_wei=str(Decimal(profit)/brokers))


def review_design(cfg):
    e=cfg.exp; n=cfg.chain.num_shards; b=cfg.scale.num_brokers
    if int(e['comparison_offset_mod'])<1:
        raise ValueError("comparison_offset_mod must be positive")
    fee_total=int(cfg.b2e.gas_units_per_ctx)*int(cfg.b2e.ref_gas_price_wei) if cfg.b2e.enabled else 0
    fee_broker=int(Decimal(fee_total)*Decimal(str(cfg.b2e.fee_share)))
    balance=Decimal(str(e['balances_eth']))
    return dict(design_version=2,
                primary_objectives={"relay_plus_adjustment_ctx":"minimize; one burn+mint transfer is ONE CTX",
                                    "broker_total_profit_wei":"maximize; retained service fees minus common per-adjustment model cost"},
                common=dict(shards=n,brokers=b,initial_eth_per_subaccount=str(balance),
                            total_initial_broker_eth=str(balance*n*b),user_ctx_per_method=b*int(e['ctx_per_broker']),
                            arrival_rate_ctx_per_s=e['rate'],repetitions=e['repetitions'],
                            route_seeds=[int(e['route_seed'])+r*int(e['route_seed_step']) for r in range(int(e['repetitions']))],
                            cooldown_blocks=e['tdr_chi_blocks'],adjustment_timeout_blocks=e['tdr_timeout_blocks'],
                            startup_offset='broker_idx % '+str(e['comparison_offset_mod']),
                            max_transfers_per_event=n-1,one_active_event_per_broker=True,
                            block_time_s=cfg.chain.block_time_s,
                            natural_prefix_fraction=e['shift_start_frac'],hotspot_cycle=e['shift_cycle'],
                            hotspot_period_fraction=e['shift_period_frac'],hotspot_intensity=e['shift_intensity']),
                economics=dict(user_fee_wei=fee_total,broker_retained_fee_per_service_wei=fee_broker,
                               adjustment_cost_per_ctx_wei=int(e['profit_tdr_cost_wei']),
                               adjustment_cost_is_ex_post=True,chain_gas_price_wei=0,
                               cost_scenarios_wei=e['cost_scenarios_wei']),
                methods=dict(brokerchain=dict(active_adjustment=False),
                             horcrux=dict(confirmed_service_compensation=True,reverse_obligations_cancel=True),
                             shaduf=dict(binding='AB all pairs',initial_directional_binding_eth=str(balance/(n-1)),
                                         demand_window_blocks=e['tdr_window_blocks'],demand='maximum observed payment per destination'),
                             lai=dict(gamma=1,curvature=e['lai_curvature'],uniform_floor=e['lai_uniform_floor'],
                                      demand_window_blocks=e['tdr_window_blocks'],demand='sum of observed payments per destination'),
                             tdr=dict(policy='topup',ewma_half_life_blocks=e['tdr_ewma_half_life'],
                                      ewma_min_blocks=e['tdr_ewma_min_blocks'],ewma_window_blocks=e['tdr_ewma_window_blocks'],
                                      epsilon=e['tdr_epsilon'],q_min=e['tdr_q_min'],two_sided=e['tdr_ewma_two_sided'],
                                      shortage_threshold_fraction=1-Decimal(str(e['tdr_epsilon'])),
                                      donor_threshold_fraction=1+Decimal(str(e['tdr_epsilon'])))))
