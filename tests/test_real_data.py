"""Mapping + streaming-extraction tests against a crafted mini-CSV
(the real 328 MB trace is not required for unit tests)."""
from brokerlab.config import ETH
from brokerlab.real_data import extract, map_address

HEADER = ("blockNumber,timestamp,transactionHash,from,to,toCreate,"
          "fromIsContract,toIsContract,value,gasLimit,gasPrice,gasUsed,"
          "callingFunction,isError")

def _row(txh, frm, to, value, *, to_create="None", from_c="0", to_c="0", err="0"):
    return ",".join(["1", "1700000000", txh, frm, to, to_create,
                     from_c, to_c, str(value), "21000", "0", "21000", "0x", err])

ADDR_EVEN0 = "0x" + "0" * 39 + "0"   # n=0  → shard 0, acct 0
ADDR_ODD1 = "0x" + "0" * 39 + "1"    # n=1  → shard 1, acct 1
ADDR_EVEN2 = "0x" + "0" * 39 + "2"   # n=2  → shard 0, acct 2
ADDR_ODD3 = "0x" + "0" * 39 + "3"    # n=3  → shard 1, acct 3


def test_map_address_matches_legacy_gen3_rule():
    # identical rule to legacy real_traffic._map_address
    assert map_address(ADDR_EVEN0, 2, 10, 200) == (0, 200)
    assert map_address(ADDR_ODD1, 2, 10, 200) == (1, 201)


def test_extract_filters(tmp_path):
    csv_path = tmp_path / "mini.csv"
    csv_path.write_text("\n".join([
        HEADER,
        _row("0xaaa1", ADDR_EVEN0, ADDR_ODD1, 2 * ETH),          # keep
        _row("0xaaa2", ADDR_ODD1, ADDR_ODD3, 1 * ETH),           # same shard → skip
        _row("0xaaa3", ADDR_EVEN0, ADDR_ODD1, 11 * ETH),         # over cap → skip
        _row("0xaaa4", ADDR_EVEN0, ADDR_ODD1, 1 * ETH,
             to_create="0x1234"),                                # creation → skip
        _row("0xaaa5", ADDR_EVEN0, ADDR_ODD1, 1 * ETH,
             err="1"),                                           # errored → skip
        _row("0xaaa6", ADDR_EVEN2, ADDR_ODD3, 1 * ETH),          # keep
    ]) + "\n")

    out = extract(str(csv_path), num_shards=2, num_users=10, user_base_index=200,
                  value_floor_wei=ETH // 100, value_cap_wei=10 * ETH, limit=10)
    assert [r.ctx_id for r in out] == ["real_0xaaa1", "real_0xaaa6"]
    r0 = out[0]
    assert (r0.src_shard, r0.dst_shard) == (0, 1)
    assert r0.amount_wei == 2 * ETH
    assert (r0.sender_idx, r0.receiver_idx) == (200, 201)


def test_extract_limit(tmp_path):
    csv_path = tmp_path / "mini.csv"
    rows = [HEADER] + [_row(f"0xb{i:03d}", ADDR_EVEN0, ADDR_ODD1, 1 * ETH)
                       for i in range(5)]
    csv_path.write_text("\n".join(rows) + "\n")
    out = extract(str(csv_path), num_shards=2, num_users=10, user_base_index=200,
                  value_floor_wei=0, value_cap_wei=10 * ETH, limit=3)
    assert len(out) == 3
