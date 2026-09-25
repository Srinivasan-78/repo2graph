import os

_GLOBAL_SESSION = {}

def get_db_session() -> dict:
    return _GLOBAL_SESSION

def get_app_settings() -> dict:
    return {
        "environment": os.getenv("APP_ENV", "benchmark-production"),
        "database_url": os.getenv("DATABASE_URL", "sqlite:///:memory:"),
        "payment_api_key": os.getenv("PAYMENT_API_KEY", "pk_live_mock_12345")
    }

