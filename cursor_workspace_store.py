# -*- coding: utf-8 -*-
"""Safe read/write helpers for Cursor user-data (workspaces + chats)."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from store import detect_user_data, process_running


def cursor_running() -> bool:
    return process_running("Cursor.exe")


def uri_to_path(uri: str) -> str | None:
    if not uri:
        return None
    if not uri.startswith("file:"):
        return None
    # file://d:/Flutter — drive letter wrongly parsed as host by urlparse
    if uri.startswith("file://") and not uri.startswith("file:///"):
        rest = unquote(uri[len("file://") :])
        if len(rest) >= 2 and rest[1] == ":":
            return str(Path(rest))
    p = unquote(urlparse(uri).path)
    # Windows: /d:/foo -> d:/foo
    if len(p) >= 3 and p[0] == "/" and p[2] == ":":
        p = p[1:]
    if not p:
        return None
    return str(Path(p))


def path_to_file_uri(path: str) -> str:
    p = Path(path).resolve()
    # file:///d%3A/foo
    drive = p.drive  # D:
    rest = p.as_posix()
    if drive:
        rest = rest[len(drive) :]
        return f"file:///{drive[0].lower()}%3A{rest}"
    return p.as_uri()


def normalize_path_key(s: str) -> str:
    return s.replace("\\", "/").lower().rstrip("/")


@dataclass
class WorkspaceItem:
    path: str
    display_name: str
    sources: list[str] = field(default_factory=list)
    exists: bool = True
    uri: str = ""
    workspace_id: str = ""
    kind: str = "folder"  # folder | repo


@dataclass
class ChatItem:
    id: str
    name: str
    source: str  # composer | localAgent
    workspace_path: str = ""
    last_updated: int = 0
    is_archived: bool = False
    subtitle: str = ""


class CursorStore:
    def __init__(self, user_data: Path | None = None):
        self.user_data = user_data or detect_user_data()
        self.user = self.user_data / "User"
        self.db_path = self.user / "globalStorage" / "state.vscdb"
        self.storage_json = self.user / "globalStorage" / "storage.json"

    def ok(self) -> bool:
        return self.db_path.is_file()

    def _conn(self, *, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            uri = self.db_path.resolve().as_uri() + "?mode=ro"
            con = sqlite3.connect(uri, uri=True, timeout=30)
        else:
            con = sqlite3.connect(str(self.db_path), timeout=30)
        con.execute("PRAGMA busy_timeout=30000")
        return con

    def get_json(self, key: str, default: Any = None) -> Any:
        con = self._conn(readonly=True)
        try:
            row = con.execute(
                "SELECT value FROM ItemTable WHERE key=?", (key,)
            ).fetchone()
            if not row or row[0] is None:
                return default
            text = row[0] if isinstance(row[0], str) else row[0].decode("utf-8")
            return json.loads(text)
        except (sqlite3.Error, json.JSONDecodeError, UnicodeDecodeError):
            return default
        finally:
            con.close()

    def set_json(self, key: str, value: Any) -> None:
        con = self._conn()
        try:
            payload = json.dumps(value, ensure_ascii=False)
            con.execute(
                "INSERT OR REPLACE INTO ItemTable (key, value) VALUES (?, ?)",
                (key, payload),
            )
            con.commit()
        finally:
            con.close()

    def delete_key(self, key: str) -> None:
        con = self._conn()
        try:
            con.execute("DELETE FROM ItemTable WHERE key=?", (key,))
            con.commit()
        finally:
            con.close()

    def list_keys_containing(self, needle: str) -> list[str]:
        con = self._conn()
        try:
            rows = con.execute("SELECT key FROM ItemTable").fetchall()
            return [r[0] for r in rows if needle in (r[0] or "")]
        finally:
            con.close()

    # ---- workspaces ----
    def list_workspaces(self) -> list[WorkspaceItem]:
        by_path: dict[str, WorkspaceItem] = {}

        def upsert(path: str, name: str, source: str, uri: str = "", wid: str = "", kind: str = "folder"):
            if not path and uri:
                path = uri_to_path(uri) or uri
            if not path:
                return
            key = normalize_path_key(path)
            item = by_path.get(key)
            if not item:
                exists = True
                if kind == "folder" and path and not path.startswith("github.com") and "://" not in path:
                    exists = Path(path).exists()
                item = WorkspaceItem(
                    path=path,
                    display_name=name or Path(path).name,
                    sources=[source],
                    exists=exists,
                    uri=uri or (path_to_file_uri(path) if Path(path).drive or (len(path) > 2 and path[1] == ":") else ""),
                    workspace_id=wid,
                    kind=kind,
                )
                by_path[key] = item
            else:
                if source not in item.sources:
                    item.sources.append(source)
                if name and name != item.display_name and source in (
                    "glass.additionalProjects",
                    "glass.localAgentProjects",
                ):
                    item.display_name = name
                if wid and not item.workspace_id:
                    item.workspace_id = wid
                if uri and not item.uri:
                    item.uri = uri

        # recently opened
        recent = self.get_json("history.recentlyOpenedPathsList", {}) or {}
        for e in recent.get("entries") or []:
            uri = e.get("folderUri") or ""
            if not uri and isinstance(e.get("workspace"), dict):
                continue  # skip remote workspace configs for now unless path-like
            path = uri_to_path(uri)
            if path:
                upsert(path, Path(path).name, "recentlyOpened", uri=uri)

        # additional projects
        for e in self.get_json("cursor/glass.additionalProjects", []) or []:
            if not isinstance(e, dict):
                continue
            if e.get("type") == "repo":
                name = e.get("name") or e.get("id") or "repo"
                upsert(name, name, "additionalProjects", kind="repo", wid=str(e.get("id") or ""))
                continue
            uri = ""
            wid = ""
            wi = e.get("workspaceIdentifier") or {}
            if isinstance(wi, dict):
                uri_obj = wi.get("uri") or {}
                if isinstance(uri_obj, dict):
                    path = uri_obj.get("fsPath") or uri_to_path(uri_obj.get("external") or "")
                    uri = uri_obj.get("external") or ""
                else:
                    path = ""
                wid = str(wi.get("id") or e.get("id") or "")
            else:
                path = e.get("path") or ""
            if path:
                upsert(str(path), e.get("name") or Path(str(path)).name, "additionalProjects", uri=uri, wid=wid)

        # local agent projects (workspace side)
        for e in self.get_json("glass.localAgentProjects.v1", []) or []:
            if not isinstance(e, dict):
                continue
            ws = e.get("workspace") or {}
            uri_obj = (ws.get("uri") or {}) if isinstance(ws, dict) else {}
            path = ""
            uri = ""
            if isinstance(uri_obj, dict):
                path = uri_obj.get("fsPath") or uri_to_path(uri_obj.get("external") or "") or ""
                uri = uri_obj.get("external") or ""
            wid = str((ws.get("id") if isinstance(ws, dict) else "") or "")
            if path:
                upsert(str(path), Path(str(path)).name, "localAgentProjects", uri=uri, wid=wid)

        # workspace metadata
        meta = self.get_json("workspaceMetadata.entries", {}) or {}
        for e in meta.get("entries") or []:
            path = e.get("displayPath") or ""
            uri = e.get("folderUri") or ""
            if not path and uri:
                path = uri_to_path(uri) or ""
            if path:
                upsert(str(path), Path(str(path)).name, "workspaceMetadata", uri=uri, wid=str(e.get("workspaceId") or ""))

        # profileAssociations
        if self.storage_json.is_file():
            data = json.loads(self.storage_json.read_text(encoding="utf-8"))
            wsmap = ((data.get("profileAssociations") or {}).get("workspaces")) or {}
            for uri in wsmap:
                path = uri_to_path(uri)
                if path:
                    upsert(path, Path(path).name, "profileAssociations", uri=uri)

        # workspaceStorage/*/workspace.json — Cursor「On This PC」真实来源之一
        # Recents 可能还在内存未落盘，但这里只要开过文件夹就会有
        ws_root = self.user / "workspaceStorage"
        if ws_root.is_dir():
            for d in ws_root.iterdir():
                if not d.is_dir():
                    continue
                wj = d / "workspace.json"
                if not wj.is_file():
                    continue
                try:
                    data = json.loads(wj.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                uri = data.get("folder") or data.get("workspace") or ""
                if not uri:
                    continue
                path = uri_to_path(uri) if str(uri).startswith("file:") else str(uri)
                if path:
                    upsert(
                        path,
                        Path(path).name,
                        "workspaceStorage",
                        uri=uri if str(uri).startswith("file:") else "",
                        wid=d.name,
                    )

        items = list(by_path.values())
        # 幽灵路径（磁盘不存在）排前面，方便清理
        items.sort(key=lambda x: (x.exists, x.path.lower()))
        return items

    def remove_workspace(self, path: str, also_mark_removed: bool = True) -> None:
        """Remove path from all known lists. No backup."""
        path_norm = normalize_path_key(path)
        uri = path_to_file_uri(path) if Path(path).drive or path[1:3] == ":\\" or (len(path) > 2 and path[1] == ":") else path

        def match_path(s: str) -> bool:
            """Exact path match only — never substring (D:\\Flutter ≠ D:\\FlutterProjects)."""
            if not s:
                return False
            sn = normalize_path_key(s)
            if sn == path_norm:
                return True
            u = uri_to_path(s) if s.startswith("file:") else None
            if u and normalize_path_key(u) == path_norm:
                return True
            # JSON entry blob: parse lightly for fsPath / folderUri / displayPath
            if "{" in s or "file:" in s or "fsPath" in s:
                try:
                    # whole value may be a JSON object/array string
                    if s.strip()[:1] in "[{":
                        blob = json.loads(s)
                    else:
                        blob = None
                except (json.JSONDecodeError, TypeError):
                    blob = None
                if isinstance(blob, dict):
                    candidates = [
                        blob.get("fsPath"),
                        blob.get("displayPath"),
                        blob.get("path"),
                        blob.get("folderUri"),
                    ]
                    wi = blob.get("workspaceIdentifier") or {}
                    if isinstance(wi, dict):
                        uri_obj = wi.get("uri") or {}
                        if isinstance(uri_obj, dict):
                            candidates.append(uri_obj.get("fsPath"))
                            candidates.append(uri_obj.get("external"))
                    ws = blob.get("workspace") or {}
                    if isinstance(ws, dict):
                        uri_obj = ws.get("uri") or {}
                        if isinstance(uri_obj, dict):
                            candidates.append(uri_obj.get("fsPath"))
                            candidates.append(uri_obj.get("external"))
                    for c in candidates:
                        if not c:
                            continue
                        if normalize_path_key(str(c)) == path_norm:
                            return True
                        up = uri_to_path(str(c)) if str(c).startswith("file:") else None
                        if up and normalize_path_key(up) == path_norm:
                            return True
                # fallback: exact normalized path appears as JSON string value
                for form in (
                    path_norm,
                    path.replace("\\", "/").lower(),
                    path.replace("\\", "\\\\").lower(),
                ):
                    if f'"{form}"' in sn or f"'{form}'" in sn:
                        # ensure not a longer path: next char not path continuation
                        return True
            return False

        def scrub(obj: Any) -> Any:
            if isinstance(obj, dict):
                out = {}
                for k, v in obj.items():
                    if match_path(str(k)):
                        continue
                    if isinstance(v, str) and match_path(v):
                        continue
                    out[k] = scrub(v)
                return out
            if isinstance(obj, list):
                out = []
                for item in obj:
                    blob = json.dumps(item, ensure_ascii=False) if not isinstance(item, str) else item
                    if match_path(blob):
                        continue
                    out.append(scrub(item))
                return out
            return obj

        # scrub important keys
        for key in [
            "history.recentlyOpenedPathsList",
            "cursor/glass.additionalProjects",
            "glass.localAgentProjects.v1",
            "workspaceMetadata.entries",
            "__$__targetStorageMarker",
            "terminal.history.entries.dirs",
        ]:
            val = self.get_json(key, None)
            if val is not None:
                self.set_json(key, scrub(val))

        # delete ItemTable keys that embed this workspace URI/path
        file_uri = uri if str(uri).startswith("file:") else path_to_file_uri(path)
        con = self._conn()
        try:
            keys = [r[0] for r in con.execute("SELECT key FROM ItemTable")]
        finally:
            con.close()
        for key in keys:
            if not key:
                continue
            if match_path(key) or (file_uri and file_uri in key):
                self.delete_key(key)

        # storage.json
        if self.storage_json.is_file():
            data = json.loads(self.storage_json.read_text(encoding="utf-8"))
            data = scrub(data)
            self.storage_json.write_text(
                json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8"
            )

        # 删掉匹配的 workspaceStorage 目录，否则 On This PC 仍会显示
        ws_root = self.user / "workspaceStorage"
        if ws_root.is_dir():
            for d in list(ws_root.iterdir()):
                if not d.is_dir():
                    continue
                wj = d / "workspace.json"
                if not wj.is_file():
                    continue
                try:
                    data = json.loads(wj.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                uri = data.get("folder") or data.get("workspace") or ""
                p = uri_to_path(uri) if str(uri).startswith("file:") else str(uri or "")
                if p and normalize_path_key(p) == path_norm:
                    shutil.rmtree(d, ignore_errors=True)

        if also_mark_removed:
            removed = self.get_json("cursor/glass.removedRecentProjects", []) or []
            if not isinstance(removed, list):
                removed = []
            entry = {
                "type": "workspace",
                "id": f"workspace:{path_norm}",
                "name": Path(path).name,
                "workspaceIdentifier": {
                    "id": path_norm,
                    "uri": {
                        "$mid": 1,
                        "fsPath": path,
                        "external": uri if uri.startswith("file:") else path_to_file_uri(path),
                        "path": "/" + path.replace("\\", "/"),
                        "scheme": "file",
                    },
                },
            }
            # avoid dup
            if not any(match_path(json.dumps(e, ensure_ascii=False)) for e in removed):
                removed.append(entry)
            self.set_json("cursor/glass.removedRecentProjects", removed)
            removed2 = self.get_json("cursor/glass.removedProjects", []) or []
            if not isinstance(removed2, list):
                removed2 = []
            if not any(match_path(json.dumps(e, ensure_ascii=False)) for e in removed2):
                removed2.append(entry)
            self.set_json("cursor/glass.removedProjects", removed2)

    def rename_workspace_display(self, path: str, new_name: str) -> None:
        path_norm = normalize_path_key(path)

        def touch_list(key: str):
            items = self.get_json(key, []) or []
            if not isinstance(items, list):
                return
            changed = False
            for e in items:
                if not isinstance(e, dict):
                    continue
                blob = json.dumps(e, ensure_ascii=False)
                if path_norm not in normalize_path_key(blob):
                    # also check fsPath
                    wi = e.get("workspaceIdentifier") or {}
                    uri = (wi.get("uri") or {}) if isinstance(wi, dict) else {}
                    fs = str(uri.get("fsPath") or "")
                    ws = e.get("workspace") or {}
                    uri2 = (ws.get("uri") or {}) if isinstance(ws, dict) else {}
                    fs2 = str(uri2.get("fsPath") or "")
                    if normalize_path_key(fs) != path_norm and normalize_path_key(fs2) != path_norm:
                        continue
                e["name"] = new_name
                changed = True
            if changed:
                self.set_json(key, items)

        touch_list("cursor/glass.additionalProjects")
        touch_list("glass.localAgentProjects.v1")

    # ---- chats ----
    def list_chats(self) -> list[ChatItem]:
        out: list[ChatItem] = []

        headers = self.get_json("composer.composerHeaders", {}) or {}
        for e in headers.get("allComposers") or []:
            if not isinstance(e, dict):
                continue
            out.append(
                ChatItem(
                    id=str(e.get("composerId") or ""),
                    name=str(e.get("name") or "(untitled)"),
                    source="composer",
                    last_updated=int(e.get("lastUpdatedAt") or e.get("createdAt") or 0),
                    is_archived=bool(e.get("isArchived")),
                    subtitle=str(e.get("subtitle") or e.get("unifiedMode") or ""),
                )
            )

        for e in self.get_json("glass.localAgentProjects.v1", []) or []:
            if not isinstance(e, dict):
                continue
            ws = e.get("workspace") or {}
            uri = (ws.get("uri") or {}) if isinstance(ws, dict) else {}
            path = str(uri.get("fsPath") or "")
            out.append(
                ChatItem(
                    id=str(e.get("id") or ""),
                    name=str(e.get("name") or "(untitled)"),
                    source="localAgent",
                    workspace_path=path,
                    last_updated=int(e.get("lastUpdatedAt") or e.get("createdAt") or 0),
                    is_archived=bool(e.get("isArchived")),
                )
            )

        out.sort(key=lambda c: c.last_updated, reverse=True)
        return out

    def rename_chat(self, chat_id: str, new_name: str, source: str) -> None:
        if source == "composer":
            headers = self.get_json("composer.composerHeaders", {}) or {}
            for e in headers.get("allComposers") or []:
                if str(e.get("composerId")) == chat_id:
                    e["name"] = new_name
            self.set_json("composer.composerHeaders", headers)
        else:
            items = self.get_json("glass.localAgentProjects.v1", []) or []
            for e in items:
                if str(e.get("id")) == chat_id:
                    e["name"] = new_name
            self.set_json("glass.localAgentProjects.v1", items)

    def delete_chat(self, chat_id: str, source: str, hard: bool = False) -> None:
        if source == "composer":
            headers = self.get_json("composer.composerHeaders", {}) or {}
            composers = headers.get("allComposers") or []
            if hard:
                headers["allComposers"] = [
                    e for e in composers if str(e.get("composerId")) != chat_id
                ]
            else:
                for e in composers:
                    if str(e.get("composerId")) == chat_id:
                        e["isArchived"] = True
            self.set_json("composer.composerHeaders", headers)
        else:
            items = self.get_json("glass.localAgentProjects.v1", []) or []
            if hard:
                items = [e for e in items if str(e.get("id")) != chat_id]
            else:
                for e in items:
                    if str(e.get("id")) == chat_id:
                        e["isArchived"] = True
            self.set_json("glass.localAgentProjects.v1", items)
