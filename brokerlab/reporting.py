"""面向读者的实验方案名称。

配置和 JSON 继续保留稳定的机器标签（plain/valve/tdr/topup）；图片和命令行
使用这里的名称，避免把内部实现名直接暴露给读者。
"""


def arm_display_name(tag: str, *, language: str = "en", multiline: bool = False,
                     compact: bool = False) -> str:
    """返回稳定、可读的方案名称；不改变用于目录和 JSON 的原始 tag。"""
    key = str(tag).lower()
    parts = key.split("@")
    nums = []
    for raw in parts[1:]:
        try:
            nums.append(float(raw))
        except ValueError:
            continue
    # _arm_tag 会省略空占位；对各 policy 按其有效参数顺序解释。
    second = nums[1] if len(nums) > 1 else None
    topup_eps = second
    topup_hl = nums[3] if len(nums) > 3 else None
    legacy_final = (len(nums) == 3 and topup_eps == 0.95 and nums[2] == 20)
    if legacy_final:
        topup_hl = 20.0
    def fmt(v):
        return f"{v:g}" if v is not None else "?"

    if compact and language != "cn":
        if key.startswith("plain"):
            return "No rebalance"
        if key.startswith("valve"):
            label = f"Limited (cap {fmt(second)})"
            return label.replace(" (", "\n(") if multiline else label
        if key.startswith("tdr"):
            label = f"Proportional (eps {fmt(second)})"
            return label.replace(" (", "\n(") if multiline else label
        if key.startswith("topup"):
            detail = f"eps {fmt(topup_eps)}"
            if topup_hl is not None:
                detail += f", EWMA {fmt(topup_hl)}"
            label = (f"Final adaptive ({detail})" if legacy_final
                     else f"Demand-based ({detail})")
            return label.replace(" (", "\n(") if multiline else label
    if key.startswith("plain"):
        en, cn = "No rebalancing", "无再平衡基线"
    elif key.startswith("valve"):
        en = f"Limited rebalancing (cap {fmt(second)})"
        cn = f"限额再平衡（容量系数 {fmt(second)}）"
    elif key.startswith("tdr"):
        en = f"Proportional rebalancing (epsilon {fmt(second)})"
        cn = f"比例再平衡（epsilon={fmt(second)}）"
    elif key.startswith("topup"):
        suffix_en = f"epsilon {fmt(topup_eps)}"
        suffix_cn = f"epsilon={fmt(topup_eps)}"
        if topup_hl is not None:
            suffix_en += f", EWMA {fmt(topup_hl)}"
            suffix_cn += f"，EWMA={fmt(topup_hl)}"
        if legacy_final:
            en = f"Final adaptive replenishment ({suffix_en})"
            cn = f"定稿自适应补充（{suffix_cn}）"
        else:
            en = f"Demand replenishment ({suffix_en})"
            cn = f"需求补充（{suffix_cn}）"
    else:
        en = cn = str(tag)
    label = cn if language == "cn" else en
    if multiline:
        label = label.replace(" (", "\n(").replace("（", "\n（", 1)
    return label
