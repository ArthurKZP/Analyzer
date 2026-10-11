import sys

if sys.argv[1:2] == ["app"]:
    from .app.server import main as app_main

    sys.exit(app_main(sys.argv[2:]))

if sys.argv[1:2] == ["mcp"]:
    from .app.mcp_server import main as mcp_main

    sys.exit(mcp_main(sys.argv[2:]))

if sys.argv[1:2] == ["sauvegarde"]:
    from .backup import main as backup_main

    sys.exit(backup_main(sys.argv[2:]))

if sys.argv[1:2] == ["base"]:
    from .db.cli import main as db_main

    sys.exit(db_main(sys.argv[2:]))

if sys.argv[1:2] == ["ranges"]:
    from .theory.ring_ranges import main as ranges_main

    sys.exit(ranges_main(sys.argv[2:]))

if sys.argv[1:2] == ["gtopen"]:
    from .theory.solve_cli import main as gtopen_main

    sys.exit(gtopen_main(sys.argv[2:]))

from .cli import main

sys.exit(main())
