from monolith.billing.service import BillingService
from monolith.billing.gateway import StripePaymentGateway


def test_billing_tax_calculation_and_charge():
    gateway = StripePaymentGateway()
    billing = BillingService(gateway)

    # Subtotal 100 with 0.08 tax rate should issue invoice for 108.00
    inv = billing.issue_invoice("acc-test", 100.0)
    assert inv.amount == 108.0
    assert inv.paid is True
    assert inv.charge_id.startswith("chg_acc-test_")
