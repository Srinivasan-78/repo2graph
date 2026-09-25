from monolith.billing.gateway import StripePaymentGateway
from monolith.billing.models import Invoice
from monolith.kernel.events import global_event_bus
from monolith.kernel.config import config

class BillingService:
    def __init__(self, gateway: StripePaymentGateway | None = None):
        self.gateway = gateway or StripePaymentGateway()
        self.invoices = {}

    def issue_invoice(self, account_id: str, subtotal: float) -> Invoice:
        tax = subtotal * config.tax_rate
        total = round(subtotal + tax, 2)
        charge_id = self.gateway.charge_customer(account_id, total, config.currency)

        invoice_id = f"inv-{len(self.invoices) + 1}"
        invoice = Invoice(id=invoice_id, account_id=account_id, amount=total, charge_id=charge_id, paid=True)
        self.invoices[invoice_id] = invoice
        global_event_bus.publish("invoice_paid", {"invoice_id": invoice_id, "account_id": account_id, "amount": total})
        return invoice

