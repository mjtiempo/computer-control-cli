"""Run the CLI as a module: ``python -m computer_control_cli``."""

from computer_control_cli.cli import Cli  # pi-lens-ignore: reportMissingImports

raise SystemExit(Cli.main())
