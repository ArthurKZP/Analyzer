import sys

if sys.argv[1:2] == ["app"]:
    from .app.server import main as app_main

    sys.exit(app_main(sys.argv[2:]))

from .cli import main

sys.exit(main())
