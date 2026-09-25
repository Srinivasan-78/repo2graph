from dynamic_repo.registry import get_plugin_instance

class DynamicDispatcher:
    def run_strategy(self, strategy_name: str, payload: dict) -> dict:
        # String lookup dispatch: static call graphs cannot know which plugin runs
        plugin = get_plugin_instance(strategy_name)
        if plugin.validate(payload):
            return plugin.execute(payload)
        return {"status": "INVALID_PAYLOAD"}
