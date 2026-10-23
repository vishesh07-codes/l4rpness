import importlib
import pkgutil

_SKILLS = {}

def skill(name=None, description=""):
    def decorator(fn):
        _SKILLS[name or fn.__name__] = {"fn": fn, "description": description or (fn.__doc__ or "").strip()}
        return fn
    return decorator

def discover_skills():
    from . import skills
    for importer, modname, _ in pkgutil.iter_modules(skills.__path__):
        importlib.import_module(f".skills.{modname}", __package__)
    return list(_SKILLS.items())
