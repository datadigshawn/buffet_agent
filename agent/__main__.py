"""enables `python -m agent ARGS`."""
from .cli import main
import sys
sys.exit(main())
