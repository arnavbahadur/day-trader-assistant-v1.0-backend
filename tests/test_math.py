import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("server", Path(__file__).parents[1] / "server.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def test_position_size():
    r = mod.calculate_position_size(5000, 5000, 100, 95, 50, 100)
    assert r["shares"] == 10
    assert r["planned_stop_loss"] == 50

def test_risk_pass():
    r = mod.check_trade_risk(100, 95, 110, 5, 30, 100, 0, 1.5)
    assert r["status"] == "PASS"
    assert r["reward_risk"] == 2.0

def test_risk_block():
    r = mod.check_trade_risk(100, 95, 105, 10, 30, 100, 0, 1.5)
    assert r["status"] == "BLOCK"
