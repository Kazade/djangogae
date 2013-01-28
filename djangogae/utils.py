import sys
import os
import logging

def find_project_root():
    """
        Go through the path, and look for manage.py
    """
    for path in sys.path:
        abs_path = os.path.join(os.path.abspath(path), "manage.py")
        if os.path.exists(abs_path):
            return os.path.dirname(abs_path)

    raise RuntimeError("Unable to locate manage.py on sys.path")

def have_appserver():
    from google.appengine.api import apiproxy_stub_map
    return bool(apiproxy_stub_map.apiproxy.GetStub('datastore_v3'))

def on_production():
    return have_appserver and (not os.environ.get("SERVER_SOFTWARE").lower().startswith("devel"))

def setup_environment():
    logging.debug("Setting up environment")

    try:
        from google.appengine.api import apiproxy_stub_map
    except ImportError:
        for k in [k for k in sys.modules if k.startswith('google')]:
            del sys.modules[k]

        possible_paths = [
            os.path.join(find_project_root(), "google_appengine"),
            os.environ.get("APP_ENGINE_SDK"),
            "/usr/local/google_appengine",
        ]

        #TODO: Search more locations

        sdk_path = None
        for path in possible_paths:
            if not path.strip(): continue
            path = os.path.realpath(os.path.expanduser(path))
            if os.path.exists(path):
                sdk_path = path
                break
        else:
            logging.error("Unable to find Google App Engine SDK")
            sys.exit(1)

        if sdk_path not in sys.path:
            logging.debug("Adding App Engine SDK to path")
            sys.path.insert(1, sdk_path)

        #Sort out library paths
        from dev_appserver import fix_sys_path
        fix_sys_path()

    if have_appserver():
        os.environ["HOME"] = find_project_root()
    
    if not have_appserver():
        from google.appengine.tools import dev_appserver

        env = dev_appserver.DEFAULT_ENV
        dev_appserver.DEFAULT_ENV = os.environ.copy()
        dev_appserver.DEFAULT_ENV.update(env)

        #Whitelist some C-modules for development #FIXME: make this configurable
        dev_appserver.HardenedModulesHook._WHITE_LIST_C_MODULES.extend(('parser', '_ssl', '_io', '_sqlite3'))

    elif not on_production():
        #Restore the subprocess module
        from google.appengine.api.mail_stub import subprocess
        sys.modules['subprocess'] = subprocess

