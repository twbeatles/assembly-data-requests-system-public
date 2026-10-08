"""Alias to write_all_bats.py for backward compatibility"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import write_all_bats

if __name__ == "__main__":
    write_all_bats.main()