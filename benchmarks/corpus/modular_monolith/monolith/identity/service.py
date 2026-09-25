from monolith.kernel.events import global_event_bus
from monolith.identity.models import Account

class IdentityService:
    def __init__(self):
        self.accounts = {}

    def register_account(self, email: str, name: str) -> Account:
        account_id = f"acc-{len(self.accounts) + 1}"
        acc = Account(id=account_id, email=email, name=name, verified=False)
        self.accounts[account_id] = acc
        global_event_bus.publish("account_created", {"account_id": account_id, "email": email})
        return acc

    def verify_account(self, account_id: str) -> bool:
        if account_id in self.accounts:
            self.accounts[account_id].verified = True
            global_event_bus.publish("account_verified", {"account_id": account_id})
            return True
        return False

