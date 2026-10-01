import sys

if sys.argv[1:2] == ["app"]:
    from .app.server import main as app_main

    sys.exit(app_main(sys.argv[2:]))

if sys.argv[1:2] == ["gtopen"]:
    from .theory.solve_cli import main as gtopen_main

    sys.exit(gtopen_main(sys.argv[2:]))

from .cli import main

sys.exit(main())
