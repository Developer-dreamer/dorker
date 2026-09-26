import sys

# Register this module under both 'src' and 'dorker' namespaces
if "dorker" not in sys.modules:
    sys.modules["dorker"] = sys.modules[__name__]
if "src" not in sys.modules:
    sys.modules["src"] = sys.modules[__name__]
