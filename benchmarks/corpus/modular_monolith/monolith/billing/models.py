from dataclasses import dataclass


@dataclass
class Invoice:
    id: str
    account_id: str
    amount: float
    charge_id: str
    paid: bool = False
