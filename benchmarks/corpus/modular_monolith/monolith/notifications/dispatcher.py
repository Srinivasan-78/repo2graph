from monolith.kernel.events import global_event_bus

class NotificationDispatcher:
    def __init__(self):
        self.sent_emails = []
        global_event_bus.subscribe("account_created", self.on_account_created)
        global_event_bus.subscribe("invoice_paid", self.on_invoice_paid)

    def on_account_created(self, data: dict):
        email = data.get("email")
        self.sent_emails.append(f"Welcome email sent to {email}")

    def on_invoice_paid(self, data: dict):
        inv_id = data.get("invoice_id")
        amount = data.get("amount")
        self.sent_emails.append(f"Receipt sent for invoice {inv_id} ({amount})")

notification_dispatcher = NotificationDispatcher()

