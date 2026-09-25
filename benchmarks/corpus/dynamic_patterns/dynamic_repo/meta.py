class MetaRegistry:
    subclasses = {}

    def __init_subclass__(cls, key: str, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.subclasses[key] = cls


class ServiceA(MetaRegistry, key="service_a"):
    def perform(self):
        return "A_OK"


class ServiceB(MetaRegistry, key="service_b"):
    def perform(self):
        return "B_OK"
