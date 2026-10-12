"""Dashboard web (Flask) per LEGO Mario: menu, scansione, dispositivi, monitor, console, accessori.

`mario.web.db` usa solo la libreria standard; Flask viene importato solo da `mario.web.app`.
"""


def create_app(*args, **kwargs):
    from .app import create_app as _create_app
    return _create_app(*args, **kwargs)
