from monolith.identity.service import IdentityService
from monolith.catalog.service import CatalogService
from monolith.billing.service import BillingService
from monolith.shipping.service import shipping_service
from monolith.notifications.dispatcher import notification_dispatcher

class MonolithApplication:
    def __init__(self):
        self.identity = IdentityService()
        self.catalog = CatalogService()
        self.billing = BillingService()
        self.shipping = shipping_service
        self.notifications = notification_dispatcher

    def checkout_order(self, account_id: str, product_id: str, quantity: int) -> dict:
        product = self.catalog.get_product(product_id)
        if not product or not self.catalog.reserve_stock(product_id, quantity):
            raise ValueError("Insufficient inventory")
        subtotal = product.price * quantity
        invoice = self.billing.issue_invoice(account_id, subtotal)
        return {"invoice_id": invoice.id, "amount": invoice.amount, "status": "COMPLETED"}

app = MonolithApplication()

