"""Persistent local conversation history with deterministic context compaction."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

STATE_DIR = Path.home() / ".local-computer"
DB_PATH = STATE_DIR / "conversations.db"

ACTIVE_MESSAGE_LIMIT = 24
ACTIVE_TOKEN_LIMIT = 12000
THINKING_CHAR_LIMIT = 16000
RECENT_MESSAGE_LIMIT = 10
SUMMARY_CHAR_LIMIT = 7000
PREVIEW_CHARS = 240
MAX_STORED_THINKING_LINES = 80
THINKING_EDGE_KEEP = 24


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _workspace_root(workspace: str | Path | None = None) -> str:
    if workspace is not None:
        return str(Path(workspace).expanduser())
    try:
        from scripts.runtime_policy import workspace_root

        return str(workspace_root())
    except Exception:
        return str(Path.cwd())


def _connect() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_root TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            compressed_until_message_id INTEGER NOT NULL DEFAULT 0,
            message_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            thinking_json TEXT NOT NULL DEFAULT '[]',
            sources_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            token_estimate INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES conversation_sessions(id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_workspace ON conversation_sessions(workspace_root, updated_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_messages ON conversation_messages(session_id, id)")
    conn.commit()


def _json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return json.dumps(str(value), ensure_ascii=False)


def _json_loads(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."


def _estimate_tokens(text: str) -> int:
    return max(1, len(str(text or "")) // 4)


def _compact_thinking(thinking: list[str]) -> tuple[list[str], dict[str, Any]]:
    cleaned = [str(item).strip() for item in thinking if str(item).strip()]
    total_chars = sum(len(item) for item in cleaned)
    if len(cleaned) <= MAX_STORED_THINKING_LINES and total_chars <= THINKING_CHAR_LIMIT:
        return cleaned, {"thinking_compacted": False, "thinking_original_count": len(cleaned)}

    keep_each = min(THINKING_EDGE_KEEP, max(1, MAX_STORED_THINKING_LINES // 2))
    head = cleaned[:keep_each]
    tail = cleaned[-keep_each:] if len(cleaned) > keep_each else []
    omitted = max(0, len(cleaned) - len(head) - len(tail))
    compacted = [
        *head,
        f"[{omitted} thinking step(s) compacted locally; original trace had {len(cleaned)} step(s) and {total_chars} characters]",
        *tail,
    ]
    return compacted, {
        "thinking_compacted": True,
        "thinking_original_count": len(cleaned),
        "thinking_stored_count": len(compacted),
        "thinking_original_chars": total_chars,
    }


def _row_to_message(row: sqlite3.Row, *, include_content: bool = False) -> dict[str, Any]:
    content = str(row["content"] or "")
    message = {
        "id": int(row["id"]),
        "session_id": int(row["session_id"]),
        "role": str(row["role"]),
        "preview": _preview(content),
        "token_estimate": int(row["token_estimate"] or 0),
        "created_at": str(row["created_at"]),
    }
    if include_content:
        message["content"] = content
        message["thinking"] = _json_loads(str(row["thinking_json"] or "[]"), [])
        message["sources"] = _json_loads(str(row["sources_json"] or "[]"), [])
        message["metadata"] = _json_loads(str(row["metadata_json"] or "{}"), {})
    return message


def active_session_id(workspace: str | Path | None = None) -> int:
    root = _workspace_root(workspace)
    now = _now()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM conversation_sessions
            WHERE workspace_root = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (root,),
        ).fetchone()
        if row:
            return int(row["id"])
        cur = conn.execute(
            """
            INSERT INTO conversation_sessions(workspace_root, title, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (root, "New conversation", now, now),
        )
        return int(cur.lastrowid)


def _session_row(conn: sqlite3.Connection, session_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM conversation_sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise ValueError(f"conversation session not found: {session_id}")
    return row


def _all_messages(conn: sqlite3.Connection, session_id: int) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM conversation_messages WHERE session_id = ? ORDER BY id ASC", (session_id,)))


def _summarize_messages(messages: list[sqlite3.Row]) -> str:
    if not messages:
        return ""
    lines = [f"Compressed locally at {_now()} from {len(messages)} older message(s)."]
    pending_user: str | None = None
    for row in messages:
        role = str(row["role"])
        content = str(row["content"] or "")
        if role == "user":
            pending_user = _preview(content, 190)
            continue
        answer = _preview(content, 230)
        if pending_user:
            lines.append(f"- User: {pending_user}")
            lines.append(f"  Locus: {answer}")
            pending_user = None
        else:
            lines.append(f"- Locus: {answer}")
        thinking = _json_loads(str(row["thinking_json"] or "[]"), [])
        if isinstance(thinking, list) and thinking:
            thinking_preview = _preview(" / ".join(str(item) for item in thinking[:4]), 180)
            lines.append(f"  Thinking: {thinking_preview}")
    if pending_user:
        lines.append(f"- User: {pending_user}")
    return "\n".join(lines)


def _merge_summary(existing: str, addition: str) -> str:
    merged = "\n\n".join(part for part in [existing.strip(), addition.strip()] if part)
    if len(merged) <= SUMMARY_CHAR_LIMIT:
        return merged
    head = merged[:1200].rstrip()
    tail = merged[-(SUMMARY_CHAR_LIMIT - 1280) :].lstrip()
    return f"{head}\n\n[Older local summary compacted]\n\n{tail}"


def maybe_compress_session(session_id: int) -> bool:
    compressed_now = False
    with _connect() as conn:
        session = _session_row(conn, session_id)
        messages = _all_messages(conn, session_id)
        if not messages:
            return False
        compressed_until = int(session["compressed_until_message_id"] or 0)
        active = [row for row in messages if int(row["id"]) > compressed_until]
        active_tokens = sum(int(row["token_estimate"] or 0) for row in active)
        thinking_chars = sum(len(str(row["thinking_json"] or "")) for row in active)
        should_compress = (
            len(active) > ACTIVE_MESSAGE_LIMIT
            or active_tokens > ACTIVE_TOKEN_LIMIT
            or thinking_chars > THINKING_CHAR_LIMIT
        )
        if not should_compress or len(messages) <= RECENT_MESSAGE_LIMIT:
            return False

        cutoff_row = messages[-RECENT_MESSAGE_LIMIT - 1]
        cutoff_id = int(cutoff_row["id"])
        to_summarize = [row for row in messages if compressed_until < int(row["id"]) <= cutoff_id]
        if not to_summarize:
            return False

        summary_addition = _summarize_messages(to_summarize)
        merged_summary = _merge_summary(str(session["summary"] or ""), summary_addition)
        now = _now()
        conn.execute(
            """
            UPDATE conversation_sessions
            SET summary = ?, compressed_until_message_id = ?, message_count = ?, updated_at = ?
            WHERE id = ?
            """,
            (merged_summary, cutoff_id, len(messages), now, session_id),
        )
        compressed_now = True
    return compressed_now


def store_turn(
    query: str,
    answer: str,
    *,
    thinking: list[str] | None = None,
    sources: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    workspace: str | Path | None = None,
    session_id: int | None = None,
) -> dict[str, Any]:
    session_id = session_id or active_session_id(workspace)
    root = _workspace_root(workspace)
    now = _now()
    thinking_items = [str(item) for item in (thinking or []) if str(item).strip()]
    thinking_items, thinking_meta = _compact_thinking(thinking_items)
    sources_items = sources or []
    metadata_items = {**thinking_meta, **(metadata or {})}
    with _connect() as conn:
        session = _session_row(conn, session_id)
        title = str(session["title"] or "")
        if title in {"", "New conversation"}:
            title = _preview(query, 80) or "Conversation"
        conn.execute(
            """
            INSERT INTO conversation_messages(session_id, role, content, token_estimate, created_at)
            VALUES (?, 'user', ?, ?, ?)
            """,
            (session_id, str(query or ""), _estimate_tokens(query), now),
        )
        answer_tokens = _estimate_tokens(answer) + sum(_estimate_tokens(item) for item in thinking_items)
        conn.execute(
            """
            INSERT INTO conversation_messages(
                session_id, role, content, thinking_json, sources_json, metadata_json, token_estimate, created_at
            )
            VALUES (?, 'assistant', ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                str(answer or ""),
                _json_dumps(thinking_items),
                _json_dumps(sources_items),
                _json_dumps(metadata_items),
                answer_tokens,
                now,
            ),
        )
        count = conn.execute(
            "SELECT COUNT(*) AS count FROM conversation_messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()["count"]
        conn.execute(
            """
            UPDATE conversation_sessions
            SET workspace_root = ?, title = ?, message_count = ?, updated_at = ?
            WHERE id = ?
            """,
            (root, title, int(count), now, session_id),
        )
    compressed_now = maybe_compress_session(session_id)
    snapshot = get_conversation_snapshot(workspace=workspace)
    snapshot["compressed_now"] = compressed_now
    snapshot["session_id"] = session_id
    return snapshot


def get_context_bundle(
    *,
    session_id: int | None = None,
    workspace: str | Path | None = None,
    recent_limit: int = RECENT_MESSAGE_LIMIT,
) -> dict[str, Any]:
    session_id = session_id or active_session_id(workspace)
    with _connect() as conn:
        session = _session_row(conn, session_id)
        compressed_until = int(session["compressed_until_message_id"] or 0)
        active_rows = list(
            conn.execute(
                """
                SELECT * FROM conversation_messages
                WHERE session_id = ? AND id > ?
                ORDER BY id ASC
                """,
                (session_id, compressed_until),
            )
        )
        active_tokens = sum(int(row["token_estimate"] or 0) for row in active_rows)
        if len(active_rows) <= ACTIVE_MESSAGE_LIMIT and active_tokens <= ACTIVE_TOKEN_LIMIT:
            context_rows = active_rows
        else:
            context_rows = active_rows[-max(1, int(recent_limit)) :]
        messages = [_row_to_message(row, include_content=True) for row in context_rows]
        summary = str(session["summary"] or "")
        context_parts: list[str] = []
        if summary:
            context_parts.append("Compressed conversation summary:\n" + summary)
        if messages:
            lines = ["Recent conversation:"]
            for message in messages:
                role = "User" if message["role"] == "user" else "Locus"
                lines.append(f"- {role}: {_preview(str(message.get('content') or ''), 360)}")
            context_parts.append("\n".join(lines))
        context_text = "\n\n".join(context_parts)
        return {
            "ok": True,
            "session_id": int(session["id"]),
            "workspace_root": str(session["workspace_root"]),
            "title": str(session["title"]),
            "summary": summary,
            "compressed_until_message_id": compressed_until,
            "compression_active": bool(summary),
            "message_count": int(session["message_count"] or 0),
            "active_message_count": int(len(active_rows)),
            "context_message_count": int(len(context_rows)),
            "recent_messages": messages,
            "context_text": context_text,
            "estimated_context_tokens": _estimate_tokens(context_text) if context_text else 0,
            "thresholds": {
                "active_message_limit": ACTIVE_MESSAGE_LIMIT,
                "active_token_limit": ACTIVE_TOKEN_LIMIT,
                "thinking_char_limit": THINKING_CHAR_LIMIT,
                "recent_message_limit": RECENT_MESSAGE_LIMIT,
            },
        }


def list_conversation_history(*, limit: int = 12, workspace: str | Path | None = None) -> list[dict[str, Any]]:
    root = _workspace_root(workspace)
    with _connect() as conn:
        sessions = list(
            conn.execute(
                """
                SELECT * FROM conversation_sessions
                WHERE workspace_root = ? AND message_count > 0
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (root, max(1, int(limit))),
            )
        )
        items: list[dict[str, Any]] = []
        for session in sessions:
            recent = list(
                conn.execute(
                    """
                    SELECT * FROM conversation_messages
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT 8
                    """,
                    (int(session["id"]),),
                )
            )
            last_user = next((row for row in recent if row["role"] == "user"), None)
            last_assistant = next((row for row in recent if row["role"] == "assistant"), None)
            items.append(
                {
                    "id": int(session["id"]),
                    "workspace_root": str(session["workspace_root"]),
                    "title": str(session["title"]),
                    "message_count": int(session["message_count"] or 0),
                    "compression_active": bool(str(session["summary"] or "")),
                    "compressed_until_message_id": int(session["compressed_until_message_id"] or 0),
                    "summary_preview": _preview(str(session["summary"] or ""), 320),
                    "last_user": _preview(str(last_user["content"] or ""), 180) if last_user else "",
                    "last_answer_preview": _preview(str(last_assistant["content"] or ""), 260) if last_assistant else "",
                    "updated_at": str(session["updated_at"]),
                    "created_at": str(session["created_at"]),
                }
            )
        return items


def get_session(session_id: int, *, recent_limit: int = 30) -> dict[str, Any]:
    with _connect() as conn:
        session = _session_row(conn, session_id)
        rows = list(
            conn.execute(
                """
                SELECT * FROM conversation_messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, max(1, int(recent_limit))),
            )
        )
        rows.reverse()
        return {
            "ok": True,
            "session": {
                "id": int(session["id"]),
                "workspace_root": str(session["workspace_root"]),
                "title": str(session["title"]),
                "summary": str(session["summary"] or ""),
                "compression_active": bool(str(session["summary"] or "")),
                "message_count": int(session["message_count"] or 0),
                "updated_at": str(session["updated_at"]),
            },
            "messages": [_row_to_message(row, include_content=True) for row in rows],
        }


def force_compress_session(session_id: int | None = None, workspace: str | Path | None = None) -> dict[str, Any]:
    session_id = session_id or active_session_id(workspace)
    with _connect() as conn:
        session = _session_row(conn, session_id)
        messages = _all_messages(conn, session_id)
        if len(messages) <= RECENT_MESSAGE_LIMIT:
            return {"ok": True, "compressed_now": False, "reason": "not enough messages", "context": get_context_bundle(session_id=session_id)}
        cutoff_row = messages[-RECENT_MESSAGE_LIMIT - 1]
        compressed_until = int(session["compressed_until_message_id"] or 0)
        to_summarize = [row for row in messages if compressed_until < int(row["id"]) <= int(cutoff_row["id"])]
        if not to_summarize:
            return {"ok": True, "compressed_now": False, "reason": "already compacted", "context": get_context_bundle(session_id=session_id)}
        summary = _merge_summary(str(session["summary"] or ""), _summarize_messages(to_summarize))
        conn.execute(
            """
            UPDATE conversation_sessions
            SET summary = ?, compressed_until_message_id = ?, message_count = ?, updated_at = ?
            WHERE id = ?
            """,
            (summary, int(cutoff_row["id"]), len(messages), _now(), session_id),
        )
    return {"ok": True, "compressed_now": True, "context": get_context_bundle(session_id=session_id)}


def get_conversation_snapshot(*, workspace: str | Path | None = None, limit: int = 12) -> dict[str, Any]:
    context = get_context_bundle(workspace=workspace)
    return {
        "ok": True,
        "sessions": list_conversation_history(limit=limit, workspace=workspace),
        "context": context,
    }


def main() -> None:
    snapshot = get_conversation_snapshot()
    print(json.dumps(snapshot, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
