from dataclasses import dataclass


@dataclass
class OrderCreateRequest:
    customer_id: str
    amount: float
    currency: str = "USD"


@dataclass
class OrderResponse:
    order_id: str
    status: str
    transaction_id: str
