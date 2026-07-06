from pathlib import Path

def read(p: Path) -> str:
    return p.read_text()
