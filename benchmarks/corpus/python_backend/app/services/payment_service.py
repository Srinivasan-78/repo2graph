import uuid

class PaymentService:
    def __init__(self, provider: str = "mock-stripe"):
        self.provider = provider
        self.transactions = {}

    def charge(self, amount: float, currency: str) -> str:
        if amount <= 0:
            raise ValueError("Amount must be positive")
        tx_id = f"tx-{uuid.uuid4().hex[:8]}"
        self.transactions[tx_id] = {"amount": amount, "currency": currency, "status": "SETTLED"}
        return tx_id

    def refund(self, tx_id: str, amount: float) -> bool:
        if tx_id not in self.transactions:
            raise KeyError(f"Transaction {tx_id} not found")
        self.transactions[tx_id]["status"] = "REFUNDED"
        return True

