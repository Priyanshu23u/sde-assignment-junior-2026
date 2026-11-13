#!/usr/bin/env python3
"""
Root entry point for the Agentic Universe pipeline.
Forwards to solution.cli.main().
"""
import sys
from solution.cli import main

if __name__ == "__main__":
    sys.exit(main())

