import json
import os
from typing import List, Dict, Any

TIME_ENTRIES_DB: List[Dict[str, Any]] = []

def load_database() -> List[Dict[str, Any]]:
    global TIME_ENTRIES_DB
    # Calculate the project root directory
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base_dir, "data", "timeEntries.json")
    try:
        with open(path, "r") as f:
            data = json.load(f)
            TIME_ENTRIES_DB = data
            print(f"Loaded {len(TIME_ENTRIES_DB)} time entries from database at {path}.")
    except Exception as e:
        print(f"timeEntries.json not found in data/ (falling back to empty database): {e}")
        TIME_ENTRIES_DB = []
    return TIME_ENTRIES_DB

# Perform initial load on module import
load_database()

def get_initial_time_entries() -> List[Dict[str, Any]]:
    """Returns a shallow copy of the initial time entries database."""
    return list(TIME_ENTRIES_DB)
