from app.api.router import api_router
from app.dependencies import get_app_settings

def create_application():
    settings = get_app_settings()
    print(f"Initializing service in environment: {settings['environment']}")
    return api_router

app = create_application()

