import os


class MonolithConfig:
    def __init__(self):
        self.app_name = "ModularMonolithSuite"
        self.secret_salt = os.getenv("MONOLITH_SALT", "default-salt-value")
        self.currency = "USD"
        self.tax_rate = 0.08


config = MonolithConfig()
