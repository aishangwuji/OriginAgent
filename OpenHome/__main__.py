"""
Entry point for running OpenHome as a module: python -m OpenHome
"""

from OpenHome.cli.commands import app

if __name__ == "__main__":
    app()
