from dynamic_repo.handlers.base_handler import BaseHandler


class UserHandler(BaseHandler):
    def on_create(self, data: dict):
        return {"result": f"user_created_{data.get('username')}"}

    def on_delete(self, data: dict):
        return {"result": f"user_deleted_{data.get('id')}"}

    def validate(self, payload: dict) -> bool:
        # Same-name collision with plugins and order_handler
        return bool(payload.get("username"))

    def execute(self, payload: dict) -> dict:
        # Same-name collision with AlphaPlugin/BetaPlugin/GammaPlugin/OrderHandler
        return self.on_create(payload)
