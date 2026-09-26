# String-keyed plugin registry
PLUGIN_REGISTRY = {}


def register_plugin(name: str):
    def decorator(cls):
        PLUGIN_REGISTRY[name] = cls
        return cls

    return decorator


def get_plugin_instance(name: str):
    cls = PLUGIN_REGISTRY.get(name)
    if not cls:
        raise KeyError(f"Plugin {name} not found in registry")
    return cls()
