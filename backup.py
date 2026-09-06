# -*- coding: utf-8 -*-
"""Lightweight backups before destructive chat / workspace edits."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from store import DATA_DIR, SyncStore, ensure_data_dir

BACKUP_ROOT = DATA_DIR / "backups"
MAX_BACKUPS = 40  # rotate old folders


def _stamp(reason: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in reason)[:40]
    return datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{safe or 'op'}"


def _rotate() -> None:
    if not BACKUP_ROOT.is_dir():
        return
    dirs = sorted(
        [p for p in BACKUP_ROOT.iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in dirs[MAX_BACKUPS:]:
        for f in old.rglob("*"):
            if f.is_file():
                try:
                    f.unlink()
                except OSError:
                    pass
        try:
            old.rmdir()
        except OSError:
            pass


def backup_chats(store: SyncStore, composer_ids: list[str], *, reason: str) -> Path | None:
    """Backup composerHeaders rows + composerData (+ ItemTable keys). Not bubbles (too large)."""
    ids = [str(c) for c in composer_ids if c]
    if not ids or not store.db_path.is_file():
        return None
    ensure_data_dir()
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_ROOT / _stamp(reason)
    dest.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "created_at": time.time(),
        "reason": reason,
        "composer_ids": ids,
        "db_path": str(store.db_path),
        "includes": [
            "composerHeaders.rows",
            "composerData",
            "ItemTable.keys_containing_id",
            "glass.localAgentProjects.matching",
        ],
        "excludes": ["bubbleId:* (too large — hard delete permanently removes them)"],
    }

    headers_out: list[dict[str, Any]] = []
    data_out: list[dict[str, Any]] = []
    item_out: list[dict[str, Any]] = []
    glass_out: list[Any] = []

    con = store._connect(readonly=True)
    try:
        use_table = store.headers_use_table()
        for cid in ids:
            if use_table:
                row = con.execute(
                    "SELECT composerId, workspaceId, createdAt, lastUpdatedAt, "
                    "isArchived, value FROM composerHeaders WHERE composerId=?",
                    (cid,),
                ).fetchone()
                if row:
                    headers_out.append(
                        {
                            "composerId": row[0],
                            "workspaceId": row[1],
                            "createdAt": row[2],
                            "lastUpdatedAt": row[3],
                            "isArchived": row[4],
                            "value": row[5],
                        }
                    )
            key = f"composerData:{cid}"
            drow = con.execute(
                "SELECT value FROM cursorDiskKV WHERE key=?", (key,)
            ).fetchone()
            if drow and drow[0] is not None:
                val = drow[0]
                if isinstance(val, bytes):
                    val = val.decode("utf-8", "ignore")
                data_out.append({"key": key, "value": val})

            for ikey, ival in con.execute(
                "SELECT key, value FROM ItemTable WHERE key LIKE ?", (f"%{cid}%",)
            ):
                text = ival if isinstance(ival, str) else (
                    ival.decode("utf-8", "ignore") if isinstance(ival, bytes) else str(ival)
                )
                item_out.append({"key": ikey, "value": text})
    finally:
        con.close()

    glass = store.get_db_json("glass.localAgentProjects.v1", []) or []
    for e in glass:
        if isinstance(e, dict) and str(e.get("id")) in set(ids):
            glass_out.append(e)

    (dest / "composerHeaders.json").write_text(
        json.dumps(headers_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (dest / "composerData.json").write_text(
        json.dumps(data_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (dest / "item_keys.json").write_text(
        json.dumps(item_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (dest / "glass_localAgent.json").write_text(
        json.dumps(glass_out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest["counts"] = {
        "headers": len(headers_out),
        "composerData": len(data_out),
        "item_keys": len(item_out),
        "glass": len(glass_out),
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _rotate()
    return dest


def backup_workspace_paths(store: SyncStore, paths: list[str], *, reason: str) -> Path | None:
    """Snapshot workspace-related ItemTable JSON blobs (small)."""
    if not paths or not store.db_path.is_file():
        return None
    ensure_data_dir()
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_ROOT / _stamp(reason)
    dest.mkdir(parents=True, exist_ok=True)
    keys = [
        "history.recentlyOpenedPathsList",
        "cursor/glass.additionalProjects",
        "glass.localAgentProjects.v1",
        "workspaceMetadata.entries",
        "cursor/glass.removedRecentProjects",
        "cursor/glass.removedProjects",
    ]
    dump: dict[str, Any] = {"paths": paths, "keys": {}}
    for k in keys:
        dump["keys"][k] = store.get_db_json(k, None)
    (dest / "workspace_lists.json").write_text(
        json.dumps(dump, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (dest / "manifest.json").write_text(
        json.dumps(
            {
                "created_at": time.time(),
                "reason": reason,
                "paths": paths,
                "note": "workspace list snapshots only; source folders on disk untouched",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _rotate()
    return dest


def list_recent_backups(limit: int = 12) -> list[dict[str, Any]]:
    if not BACKUP_ROOT.is_dir():
        return []
    out: list[dict[str, Any]] = []
    dirs = sorted(
        [p for p in BACKUP_ROOT.iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in dirs[:limit]:
        man = p / "manifest.json"
        meta: dict[str, Any] = {"path": str(p), "name": p.name}
        if man.is_file():
            try:
                meta.update(json.loads(man.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass
        out.append(meta)
    return out
