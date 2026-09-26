class BaseHandler:
    def handle_request(self, action: str, data: dict):
        method_name = f"on_{action}"
        # Intentional dynamic reflection pattern: getattr() dispatch
        method = getattr(self, method_name, None)
        if method is None:
            return self.default_action(data)
        return method(data)

    def default_action(self, data: dict):
        return {"error": "unsupported action", "data": data}
