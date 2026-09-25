from dynamic_repo.handlers.base_handler import BaseHandler

class OrderHandler(BaseHandler):
    def on_create(self, data: dict):
        return {"result": f"order_created_{data.get('order_id')}"}

    def on_cancel(self, data: dict):
        return {"result": f"order_cancelled_{data.get('order_id')}"}

    def validate(self, payload: dict) -> bool:
        # Same-name collision
        return bool(payload.get("order_id"))

    def execute(self, payload: dict) -> dict:
        # Same-name collision
        return self.on_create(payload)

