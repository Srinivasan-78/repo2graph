class EventBus:
    def __init__(self):
        self.subscribers = {}

    def subscribe(self, event_name: str, handler):
        if event_name not in self.subscribers:
            self.subscribers[event_name] = []
        self.subscribers[event_name].append(handler)

    def publish(self, event_name: str, data: dict):
        for handler in self.subscribers.get(event_name, []):
            handler(data)


global_event_bus = EventBus()
