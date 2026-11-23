"""Load the plugin as a namespace package without the host (no __init__.py by design)."""
import pathlib,sys,types
PLUGIN=pathlib.Path(__file__).resolve().parents[1]
NAME='siril_auto_test'
if NAME not in sys.modules:
    package=types.ModuleType(NAME);package.__path__=[str(PLUGIN)];sys.modules[NAME]=package
import importlib
def load(module):return importlib.import_module(NAME+'.'+module)
