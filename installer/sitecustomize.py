"""Shipped as Lib/sitecustomize.py in the Python that the Iris installer bundles.

The bundled Python is a normal CPython installation in the app folder. A normal installation also reads the user's own
site-packages (%APPDATA%/Python/Python312/site-packages), where packages from someone's own `pip install --user` would
take precedence over the ones Iris was built and tested with. This file takes that folder off sys.path and sets
PYTHONNOUSERSITE, so the voice loop, the overlay, skill runs and voice pack installs (all started with this Python)
never read it at all.
"""

import os
import site
import sys


def keep_to_the_bundle() -> None:
    os.environ["PYTHONNOUSERSITE"] = "1"
    site.ENABLE_USER_SITE = False
    user_site = getattr(site, "USER_SITE", None)
    if user_site:
        sys.path[:] = [p for p in sys.path if os.path.normcase(p) != os.path.normcase(user_site)]


keep_to_the_bundle()
