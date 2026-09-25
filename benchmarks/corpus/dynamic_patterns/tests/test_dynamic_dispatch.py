from dynamic_repo.dispatcher import DynamicDispatcher


def test_dynamic_strategy_execution():
    dispatcher = DynamicDispatcher()
    res = dispatcher.run_strategy("alpha_strategy", {"alpha_key": 21})
    assert res["status"] == "SUCCESS"
    assert res["result"] == 42
    assert res["engine"] == "AlphaEngine"
