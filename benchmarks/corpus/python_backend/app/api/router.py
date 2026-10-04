from app.api.orders import handle_create_order, handle_get_order, handle_refund_order


class APIRouter:
    def __init__(self, prefix: str = "/api/v1"):
        self.prefix = prefix
        self.routes = {}

    def register(self, path: str, method: str, handler):
        self.routes[(method, f"{self.prefix}{path}")] = handler


api_router = APIRouter()
api_router.register("/orders", "POST", handle_create_order)
api_router.register("/orders/{id}", "GET", handle_get_order)
api_router.register("/orders/{id}/refund", "POST", handle_refund_order)
