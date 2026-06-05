"""Debug get_skill_for_error"""
import sys
sys.path.insert(0, '/home/asus/kuafu')
from pathlib import Path
import tempfile

with tempfile.TemporaryDirectory() as d:
    from core.evolution_tracker import JSONCompatibleTracker
    db = Path(d) / "ev.db"
    tr = JSONCompatibleTracker(db_path=db, reuse_conn=False)
    
    tr.record_error("ModuleNotFoundError: flask", skill_name="flask_skill")
    
    # Check stored value
    rows = tr._execute("SELECT * FROM evolution_errors").fetchall()
    for r in rows:
        print(dict(r))
    
    result = tr.get_skill_for_error("flask ModuleNotFoundError")
    print(f"Result: {result}")
    
    tr.close()
