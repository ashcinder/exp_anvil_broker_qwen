"""static_broker 匹配策略的纯函数测试（无需链）。"""
import random

from brokerlab.matching import select_broker


def test_only_eligible_are_candidates():
    rng = random.Random(1)
    # broker1 余额不够 → 绝不能被选中（即使它 id 更小）
    for _ in range(200):
        b = select_broker({0: 5 * 10**18, 1: 1, 2: 5 * 10**18}, 10**18, rng)
        assert b in (0, 2)


def test_none_when_no_eligible():
    assert select_broker({0: 5, 1: 7}, 10, random.Random(0)) is None


def test_same_seed_same_sequence():
    avail = {i: (i + 1) * 10**18 for i in range(5)}
    rng_a, rng_b = random.Random(42), random.Random(42)
    seq_a = [select_broker(avail, 10**18, rng_a) for _ in range(50)]
    seq_b = [select_broker(avail, 10**18, rng_b) for _ in range(50)]
    assert seq_a == seq_b


def test_uniform_hit_all_brokers():
    rng = random.Random(7)
    hits = set()
    for _ in range(500):
        hits.add(select_broker({0: 9, 1: 9, 2: 9}, 5, rng))
    assert hits == {0, 1, 2}          # 每个 broker 都会被选中过（均匀性的粗检）


def test_boundary_eligible_equal_amount():
    assert select_broker({0: 10}, 10, random.Random(0)) == 0
