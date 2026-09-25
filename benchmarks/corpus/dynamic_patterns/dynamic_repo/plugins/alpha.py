from dynamic_repo.registry import register_plugin

@register_plugin("alpha_strategy")
class AlphaPlugin:
    def validate(self, payload: dict) -> bool:
        return "alpha_key" in payload

    def execute(self, payload: dict) -> dict:
        return {"status": "SUCCESS", "engine": "AlphaEngine", "result": payload.get("alpha_key", 0) * 2}

    def cleanup(self) -> None:
        pass
