from app.services.order_service import OrderService
from app.services.payment_service import PaymentService
from app.dependencies import get_db_session

payment_service = PaymentService()
order_service = OrderService(payment_service)

def handle_create_order(payload: dict) -> dict:
    session = get_db_session()
    return order_service.create_order(session, payload)

def handle_get_order(order_id: str) -> dict:
    session = get_db_session()
    return order_service.get_order_by_id(session, order_id)

def handle_refund_order(order_id: str) -> dict:
    session = get_db_session()
    return order_service.process_refund(session, order_id)

