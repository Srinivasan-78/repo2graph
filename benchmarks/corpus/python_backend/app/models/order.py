from dataclasses import dataclass, asdict

@dataclass
class OrderModel:
    order_id: str
    customer_id: str
    amount: float
    status: str
    transaction_id: str

    def to_dict(self) -> dict:
        return asdict(self)

