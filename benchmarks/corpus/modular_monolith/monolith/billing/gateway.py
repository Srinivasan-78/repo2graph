class StripePaymentGateway:
    def __init__(self, api_key: str = "sk_live_monolith_key"):
        self.api_key = api_key
        self.ledger = []

    def charge_customer(self, account_id: str, amount: float, currency: str) -> str:
        charge_id = f"chg_{account_id}_{int(amount*100)}"
        self.ledger.append({"charge_id": charge_id, "account": account_id, "amount": amount, "currency": currency})
        return charge_id

