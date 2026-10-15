from travel_mode.flows.apply import ask as ask


def test_yes_skips_aggressive_item_without_flag(capsys):
    assert ask("关 Wi-Fi", yes=True, risk="aggressive", aggressive=False, emit=lambda k, m: print(m)) is False
    assert "--aggressive" in capsys.readouterr().out


def test_yes_accepts_aggressive_item_with_flag():
    assert ask("关 Wi-Fi", yes=True, risk="aggressive", aggressive=True) is True


def test_yes_accepts_safe_item():
    assert ask("电源设置", yes=True, risk="safe", aggressive=False) is True
