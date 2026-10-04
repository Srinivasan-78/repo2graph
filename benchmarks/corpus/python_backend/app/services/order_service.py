from app.services.payment_service import PaymentService
from app.models.order import OrderModel


class OrderService:
    def __init__(self, payment_service: PaymentService):
        self.payment_service = payment_service

    def create_order(self, session: dict, payload: dict) -> dict:
        total_amount = float(payload.get("amount", 0.0))
        currency = payload.get("currency", "USD")

        # Charge payment before persisting order
        transaction_id = self.payment_service.charge(total_amount, currency)

        order = OrderModel(
            order_id=payload.get("id", "ord-default"),
            customer_id=payload.get("customer_id", "cust-anon"),
            amount=total_amount,
            status="PAID",
            transaction_id=transaction_id,
        )
        session[order.order_id] = order
        return order.to_dict()

    def get_order_by_id(self, session: dict, order_id: str) -> dict:
        order = session.get(order_id)
        if not order:
            raise KeyError(f"Order {order_id} not found")
        return order.to_dict()

    def process_refund(self, session: dict, order_id: str) -> dict:
        order = session.get(order_id)
        if not order:
            raise KeyError(f"Order {order_id} not found")

        self.payment_service.refund(order.transaction_id, order.amount)
        order.status = "REFUNDED"
        return order.to_dict()
