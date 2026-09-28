from brokerlab.reporting import arm_display_name


def test_reader_labels_hide_internal_strategy_names():
    tags = [
        "plain@150.0",
        "valve@150.0@1.3",
        "tdr@150.0@0.1",
        "topup@150.0@0.1",
        "topup@150.0@0.95@20.0",
    ]
    english = [arm_display_name(tag) for tag in tags]
    chinese = [arm_display_name(tag, language="cn") for tag in tags]

    assert all("topup" not in label.lower() for label in english + chinese)
    assert english[-1] == "Final adaptive replenishment (epsilon 0.95, EWMA 20)"
    assert chinese[-1] == "定稿自适应补充（epsilon=0.95，EWMA=20）"


def test_multiline_label_wraps_parameters_but_keeps_meaning():
    label = arm_display_name("topup@150.0@0.95@20.0", multiline=True)
    compact = arm_display_name("topup@150.0@0.95@20.0", multiline=True,
                               compact=True)

    assert label == "Final adaptive replenishment\n(epsilon 0.95, EWMA 20)"
    assert compact == "Final adaptive\n(eps 0.95, EWMA 20)"
