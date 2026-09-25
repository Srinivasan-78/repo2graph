import importlib


class DynamicFactory:
    @staticmethod
    def load_and_instantiate(module_path: str, class_name: str):
        # Dynamic import and reflection
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        return cls()
