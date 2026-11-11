"""Portable entry point. Importing this module never launches the application."""
from pathlib import Path
import os, sys, traceback

def main():
    root=Path(__file__).resolve().parent
    required=['numpy','scipy','astropy','photutils','PIL']
    import importlib.util
    missing=[name for name in required if importlib.util.find_spec(name) is None]
    if missing:
        raise RuntimeError('Липсват Python зависимости: '+', '.join(missing)+'. Стартирай Setup.cmd в папката на програмата.')
    os.environ.setdefault('OMP_NUM_THREADS','1');os.environ.setdefault('OPENBLAS_NUM_THREADS','1');os.environ.setdefault('MKL_NUM_THREADS','1')
    sys.path.insert(0,str(root))
    from Bridge.Desktop.host import run
    run()

if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    try:main()
    except Exception as error:
        folder=Path(__file__).resolve().parent/'Backend'/'Cache'/'data';folder.mkdir(parents=True,exist_ok=True)
        (folder/'startup-error.log').write_text(traceback.format_exc(),encoding='utf-8')
        if os.name=='nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(0,str(error),'Astro Archive',16)
        raise
