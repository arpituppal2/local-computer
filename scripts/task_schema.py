# scripts/task_schema.py

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class Task:
    id: str
    goal: str
    status: str = "pending"   # pending | running | done | failed
    steps: List[Dict[str, Any]] = field(default_factory=list)
    attempts: int = 0
    max_attempts: int = 3
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
