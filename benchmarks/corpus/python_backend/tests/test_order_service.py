from app.services.order_service import OrderService
from app.services.payment_service import PaymentService


def test_order_creation_flow():
    payment_service = PaymentService()
    order_service = OrderService(payment_service)
    session = {}

    payload = {"id": "ord-999", "customer_id": "cust-1", "amount": 150.0}
    res = order_service.create_order(session, payload)

    assert res["order_id"] == "ord-999"
    assert res["status"] == "PAID"
    assert res["transaction_id"].startswith("tx-")
    assert "ord-999" in session


def test_order_refund_flow():
    payment_service = PaymentService()
    order_service = OrderService(payment_service)
    session = {}

    payload = {"id": "ord-888", "customer_id": "cust-2", "amount": 75.0}
    order_service.create_order(session, payload)

    refunded = order_service.process_refund(session, "ord-888")
    assert refunded["status"] == "REFUNDED"
