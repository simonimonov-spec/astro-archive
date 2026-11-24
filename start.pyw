"""Portable entry point. Importing this module never launches the application."""
from pathlib import Path
import json, os, sys, traceback

ROOT = Path(__file__).resolve().parent


def startup_text(key, fallback, *arguments):
    try:
        path = ROOT / 'Backend' / 'Cache' / 'data' / 'profile.json'
        with path.open(encoding='utf-8-sig') as stream:
            language = json.load(stream)['preferences']['languageId']
    except (OSError, ValueError, KeyError, TypeError):
        language = 'en'
    try:
        path = ROOT / 'Bridge' / 'Desktop' / 'startup-locales.json'
        with path.open(encoding='utf-8') as stream:
            catalogs = json.load(stream)
        selected = next((messages for code, messages in catalogs.items()
                         if isinstance(language, str) and code.casefold() == language.casefold()), {})
        message = selected.get(key) or catalogs['en'][key]
    except (OSError, ValueError, KeyError, TypeError):
        message = fallback
    return message.format(*arguments)


class StartupDependencyError(RuntimeError):
    pass

def main():
    required=['numpy','scipy','astropy','photutils','PIL']
    import importlib.util
    missing=[name for name in required if importlib.util.find_spec(name) is None]
    if missing:
        raise StartupDependencyError(startup_text('pythonMissing',
            'Missing Python dependencies: {0}. Open AstroArchive.exe to check and download them.',
            ', '.join(missing)))
    os.environ.setdefault('OMP_NUM_THREADS','1');os.environ.setdefault('OPENBLAS_NUM_THREADS','1');os.environ.setdefault('MKL_NUM_THREADS','1')
    sys.path.insert(0,str(ROOT))
    from Bridge.Desktop.host import run
    run()

if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    try:main()
    except Exception as error:
        folder=ROOT/'Backend'/'Cache'/'data';folder.mkdir(parents=True,exist_ok=True)
        (folder/'startup-error.log').write_text(traceback.format_exc(),encoding='utf-8')
        if os.name=='nt':
            import ctypes
            message = str(error) if isinstance(error, StartupDependencyError) else startup_text(
                'pythonStartupFailed', 'Astro Archive could not start. See startup-error.log for details.')
            ctypes.windll.user32.MessageBoxW(0, message, startup_text('windowTitle', 'Astro Archive — Startup'), 16)
        raise
