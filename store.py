# -*- coding: utf-8 -*-
"""Codex ↔ Cursor sync state + local inventory helpers."""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CURSOR_HOME = Path(os.path.expandvars(r"%USERPROFILE%\.cursor"))
# Prefer env override, then standard Roaming profile (works for most installs).
FALLBACK_CURSOR_USER_DATA = Path(os.path.expandvars(r"%APPDATA%\Cursor"))
DEFAULT_CURSOR_USER_DATA = FALLBACK_CURSOR_USER_DATA
DEFAULT_CODEX_HOME = Path(os.path.expandvars(r"%USERPROFILE%\.codex"))
DEFAULT_AGENTS_SKILLS = Path(os.path.expandvars(r"%USERPROFILE%\.agents\skills"))

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
ALLOWLIST_PATH = DATA_DIR / "chat_allowlist.json"
TITLE_OVERRIDES_PATH = DATA_DIR / "title_overrides.json"
PROJECT_EXCLUDES_PATH = DATA_DIR / "project_excludes.json"


def detect_user_data() -> Path:
    """Resolve Cursor user-data dir that actually contains state.vscdb."""
    env = os.environ.get("CURSOR_USER_DATA_DIR")
    if env and (Path(env) / "User" / "globalStorage" / "state.vscdb").is_file():
        return Path(env)
    candidates = [
        FALLBACK_CURSOR_USER_DATA,
        Path(os.path.expandvars(r"%USERPROFILE%\.config\Cursor")),  # rare/custom
    ]
    # Optional machine-local override file (not committed): data/user_data_path.txt
    local = DATA_DIR / "user_data_path.txt"
    if local.is_file():
        try:
            line = local.read_text(encoding="utf-8").strip().splitlines()[0].strip()
            if line:
                candidates.insert(0, Path(line))
        except OSError:
            pass
    for cand in candidates:
        if (cand / "User" / "globalStorage" / "state.vscdb").is_file():
            return cand
    return FALLBACK_CURSOR_USER_DATA


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, value: Any) -> None:
    ensure_data_dir()
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def process_running(image: str) -> bool:
    """Check if a Windows process is running without flashing a console."""
    try:
        flags = 0
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        out = subprocess.check_output(
            ["tasklist", "/FI", f"IMAGENAME eq {image}"],
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=flags,
        )
        return image.lower() in out.lower()
    except Exception:
        return False


@dataclass
class SyncState:
    provider_ids: list[str] = field(default_factory=list)
    selection: dict[str, bool] = field(default_factory=dict)
    version: int = 1
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def keep_cursor(self) -> bool:
        return "cursor" in self.provider_ids

    @property
    def keep_claude(self) -> bool:
        return "claude-code" in self.provider_ids


class SyncStore:
    def __init__(
        self,
        codex_home: Path | None = None,
        cursor_home: Path | None = None,
        cursor_user_data: Path | None = None,
    ):
        self.codex_home = codex_home or DEFAULT_CODEX_HOME
        self.cursor_home = cursor_home or DEFAULT_CURSOR_HOME
        self.cursor_user_data = cursor_user_data or detect_user_data()
        self.global_state_path = self.codex_home / ".codex-global-state.json"
        self.mcp_json = self.cursor_home / "mcp.json"
        self.config_toml = self.codex_home / "config.toml"
        self.skills_cursor = self.cursor_home / "skills-cursor"
        self.agents_skills = DEFAULT_AGENTS_SKILLS
        self.projects_root = self.cursor_home / "projects"
        self.db_path = (
            self.cursor_user_data / "User" / "globalStorage" / "state.vscdb"
        )

    def ok(self) -> bool:
        return self.global_state_path.is_file()

    def _read_global(self) -> dict[str, Any]:
        return json.loads(self.global_state_path.read_text(encoding="utf-8"))

    def _write_global(self, obj: dict[str, Any]) -> None:
        # tiny safety copy next to file (not full disk-eating backup)
        bak = self.global_state_path.with_suffix(".json.prev")
        try:
            shutil.copy2(self.global_state_path, bak)
        except OSError:
            pass
        self.global_state_path.write_text(
            json.dumps(obj, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def get_sync_state(self) -> SyncState:
        obj = self._read_global()
        atom = obj.get("electron-persisted-atom-state") or {}
        raw = atom.get("external-agent-import-sync-state") or {
            "providerIds": [],
            "selection": {},
            "version": 1,
        }
        return SyncState(
            provider_ids=list(raw.get("providerIds") or []),
            selection=dict(raw.get("selection") or {}),
            version=int(raw.get("version") or 1),
            raw=raw,
        )

    def set_sync_state(self, state: SyncState) -> None:
        obj = self._read_global()
        atom = obj.setdefault("electron-persisted-atom-state", {})
        atom["external-agent-import-sync-state"] = {
            "providerIds": list(state.provider_ids),
            "selection": dict(state.selection),
            "version": state.version,
        }
        self._write_global(obj)

    def categorize_selection(self, selection: dict[str, bool]) -> dict[str, list[tuple[str, bool]]]:
        """Group selection keys by coarse type."""
        groups: dict[str, list[tuple[str, bool]]] = {
            "MCP": [],
            "SKILLS": [],
            "CONFIG": [],
            "OTHER_KEYS": [],
            "flags": [],
        }
        for k, v in selection.items():
            if k in ("projects", "chats"):
                groups["flags"].append((k, bool(v)))
            elif k.startswith("MCP_SERVER_CONFIG"):
                groups["MCP"].append((k, bool(v)))
            elif k.startswith("SKILLS"):
                groups["SKILLS"].append((k, bool(v)))
            elif k.startswith("CONFIG"):
                groups["CONFIG"].append((k, bool(v)))
            else:
                groups["OTHER_KEYS"].append((k, bool(v)))
        return groups

    def set_flag(self, state: SyncState, key: str, enabled: bool) -> SyncState:
        state.selection[key] = enabled
        return state

    def set_provider(self, state: SyncState, provider: str, enabled: bool) -> SyncState:
        ids = set(state.provider_ids)
        if enabled:
            ids.add(provider)
        else:
            ids.discard(provider)
        state.provider_ids = sorted(ids)
        return state

    def set_category_prefix(self, state: SyncState, prefix: str, enabled: bool) -> SyncState:
        for k in list(state.selection.keys()):
            if k.startswith(prefix):
                state.selection[k] = enabled
        return state

    def read_mcp_servers(self) -> dict[str, Any]:
        if not self.mcp_json.is_file():
            return {}
        try:
            data = json.loads(self.mcp_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return dict(data.get("mcpServers") or {})

    def read_codex_mcp_names(self) -> list[str]:
        if not self.config_toml.is_file():
            return []
        text = self.config_toml.read_text(encoding="utf-8", errors="ignore")
        return re.findall(r"^\[mcp_servers\.([^\]]+)\]", text, flags=re.M)

    def list_skills(self, root: Path) -> list[str]:
        if not root.is_dir():
            return []
        return sorted(
            p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
        )

    def list_project_dirs(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not self.projects_root.is_dir():
            return out
        excludes = set(load_json(PROJECT_EXCLUDES_PATH, []))
        for p in sorted(self.projects_root.iterdir(), key=lambda x: x.name.lower()):
            if not p.is_dir():
                continue
            transcripts = p / "agent-transcripts"
            n_chats = 0
            if transcripts.is_dir():
                n_chats = sum(1 for c in transcripts.iterdir() if c.is_dir())
            out.append(
                {
                    "id": p.name,
                    "path": str(p),
                    "chat_count": n_chats,
                    "excluded": p.name in excludes or str(p) in excludes,
                }
            )
        return out

    def _connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            uri = self.db_path.resolve().as_uri() + "?mode=ro"
            con = sqlite3.connect(uri, uri=True, timeout=30)
        else:
            con = sqlite3.connect(str(self.db_path), timeout=30)
        con.execute("PRAGMA busy_timeout=30000")
        return con

    def cursor_db_ok(self) -> bool:
        return self.db_path.is_file()

    def headers_use_table(self) -> bool:
        """Cursor newer builds migrate composerHeaders out of ItemTable."""
        if not self.db_path.is_file():
            return False
        try:
            con = self._connect(readonly=True)
            try:
                row = con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='composerHeaders'"
                ).fetchone()
                if not row:
                    return False
                # prefer table when migration flag set OR ItemTable blob missing
                mig = con.execute(
                    "SELECT value FROM cursorDiskKV WHERE key=?",
                    ("composer.composerHeaders.migratedToTable",),
                ).fetchone()
                if mig and str(mig[0]).strip() in {"1", "true", "True"}:
                    return True
                legacy = con.execute(
                    "SELECT 1 FROM ItemTable WHERE key=?",
                    ("composer.composerHeaders",),
                ).fetchone()
                return legacy is None
            finally:
                con.close()
        except sqlite3.Error:
            return False

    def list_composer_header_entries(self) -> list[dict[str, Any]]:
        """Unified allComposers-like list (table or legacy ItemTable JSON)."""
        if not self.db_path.is_file():
            return []
        if self.headers_use_table():
            con = self._connect(readonly=True)
            try:
                rows = con.execute(
                    "SELECT composerId, workspaceId, createdAt, lastUpdatedAt, "
                    "isArchived, value FROM composerHeaders"
                ).fetchall()
            finally:
                con.close()
            out: list[dict[str, Any]] = []
            for cid, wid, created, updated, archived, val in rows:
                obj: dict[str, Any] = {}
                if val:
                    try:
                        text = val if isinstance(val, str) else val.decode("utf-8", "ignore")
                        parsed = json.loads(text)
                        if isinstance(parsed, dict):
                            obj = parsed
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        obj = {}
                obj.setdefault("composerId", str(cid))
                if wid and "workspaceIdentifier" not in obj:
                    obj["workspaceIdentifier"] = {"id": str(wid)}
                obj["createdAt"] = int(obj.get("createdAt") or created or 0)
                obj["lastUpdatedAt"] = int(obj.get("lastUpdatedAt") or updated or 0)
                obj["isArchived"] = bool(obj.get("isArchived") or archived)
                out.append(obj)
            return out
        headers = self.get_db_json("composer.composerHeaders", {}) or {}
        return [e for e in (headers.get("allComposers") or []) if isinstance(e, dict)]

    def _update_composer_header_entry(
        self, composer_id: str, mutator
    ) -> bool:
        """Apply mutator(dict) to one header; persist. Returns True if found."""
        if not self.db_path.is_file():
            return False
        if self.headers_use_table():
            con = self._connect()
            try:
                row = con.execute(
                    "SELECT value FROM composerHeaders WHERE composerId=?",
                    (composer_id,),
                ).fetchone()
                if not row:
                    return False
                val = row[0]
                obj: dict[str, Any] = {}
                if val:
                    text = val if isinstance(val, str) else val.decode("utf-8", "ignore")
                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, dict):
                            obj = parsed
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        obj = {}
                obj.setdefault("composerId", composer_id)
                mutator(obj)
                con.execute(
                    "UPDATE composerHeaders SET value=?, isArchived=?, lastUpdatedAt=? "
                    "WHERE composerId=?",
                    (
                        json.dumps(obj, ensure_ascii=False),
                        1 if obj.get("isArchived") else 0,
                        int(obj.get("lastUpdatedAt") or 0),
                        composer_id,
                    ),
                )
                con.commit()
                return True
            except (sqlite3.Error, json.JSONDecodeError, UnicodeDecodeError):
                return False
            finally:
                con.close()

        headers = self.get_db_json("composer.composerHeaders", {}) or {}
        found = False
        for e in headers.get("allComposers") or []:
            if isinstance(e, dict) and str(e.get("composerId")) == composer_id:
                mutator(e)
                found = True
                break
        if found:
            self.set_db_json("composer.composerHeaders", headers)
        return found

    def _delete_composer_header_entry(self, composer_id: str) -> bool:
        if not self.db_path.is_file():
            return False
        if self.headers_use_table():
            con = self._connect()
            try:
                cur = con.execute(
                    "DELETE FROM composerHeaders WHERE composerId=?", (composer_id,)
                )
                con.commit()
                return cur.rowcount > 0
            except sqlite3.Error:
                return False
            finally:
                con.close()
        headers = self.get_db_json("composer.composerHeaders", {}) or {}
        before = headers.get("allComposers") or []
        after = [e for e in before if str(e.get("composerId")) != composer_id]
        if len(after) == len(before):
            return False
        headers["allComposers"] = after
        self.set_db_json("composer.composerHeaders", headers)
        return True

    def get_db_json(self, key: str, default: Any = None) -> Any:
        if not self.db_path.is_file():
            return default
        con = self._connect(readonly=True)
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

    def set_db_json(self, key: str, value: Any) -> None:
        con = self._connect()
        try:
            payload = json.dumps(value, ensure_ascii=False)
            exists = con.execute(
                "SELECT 1 FROM ItemTable WHERE key=?", (key,)
            ).fetchone()
            if exists:
                con.execute(
                    "UPDATE ItemTable SET value=? WHERE key=?", (payload, key)
                )
            else:
                con.execute(
                    "INSERT INTO ItemTable(key, value) VALUES(?, ?)", (key, payload)
                )
            con.commit()
        finally:
            con.close()

    def get_composer_data(self, composer_id: str) -> dict[str, Any] | None:
        if not self.db_path.is_file():
            return None
        key = f"composerData:{composer_id}"
        con = self._connect(readonly=True)
        try:
            row = con.execute(
                "SELECT value FROM cursorDiskKV WHERE key=?", (key,)
            ).fetchone()
            if not row or row[0] is None:
                return None
            val = row[0]
            if isinstance(val, bytes):
                val = val.decode("utf-8", "ignore")
            return json.loads(val)
        except (sqlite3.Error, json.JSONDecodeError):
            return None
        finally:
            con.close()

    def map_composer_data_names(self) -> dict[str, dict[str, Any]]:
        """composerId -> {name, subtitle, ...} from composerData:* (Agents Rename 落点)."""
        out: dict[str, dict[str, Any]] = {}
        if not self.db_path.is_file():
            return out
        con = self._connect(readonly=True)
        try:
            rows = con.execute(
                "SELECT key, value FROM cursorDiskKV WHERE key LIKE 'composerData:%'"
            ).fetchall()
            for key, val in rows:
                if not key or val is None:
                    continue
                cid = str(key).split(":", 1)[-1]
                if isinstance(val, bytes):
                    val = val.decode("utf-8", "ignore")
                try:
                    o = json.loads(val)
                except json.JSONDecodeError:
                    continue
                if not isinstance(o, dict):
                    continue
                out[cid] = {
                    "name": str(o.get("name") or "").strip(),
                    "subtitle": str(o.get("subtitle") or "").strip(),
                    "unifiedMode": str(o.get("unifiedMode") or "").strip(),
                    "lastUpdatedAt": int(o.get("lastUpdatedAt") or 0),
                    "createdAt": int(o.get("createdAt") or 0),
                    "isArchived": bool(o.get("isArchived")),
                    "workspaceIdentifier": o.get("workspaceIdentifier"),
                }
        except sqlite3.Error:
            return out
        finally:
            con.close()
        return out

    def set_composer_data_name(self, composer_id: str, name: str) -> bool:
        if not self.db_path.is_file():
            return False
        key = f"composerData:{composer_id}"
        con = self._connect()
        try:
            row = con.execute(
                "SELECT value FROM cursorDiskKV WHERE key=?", (key,)
            ).fetchone()
            if not row or row[0] is None:
                return False
            val = row[0]
            if isinstance(val, bytes):
                val = val.decode("utf-8", "ignore")
            obj = json.loads(val)
            obj["name"] = name
            con.execute(
                "UPDATE cursorDiskKV SET value=? WHERE key=?",
                (json.dumps(obj, ensure_ascii=False), key),
            )
            con.commit()
            return True
        except (sqlite3.Error, json.JSONDecodeError):
            return False
        finally:
            con.close()

    def _patch_composer_data_flags(
        self, composer_id: str, *, archived: bool | None = None
    ) -> bool:
        if not self.db_path.is_file():
            return False
        key = f"composerData:{composer_id}"
        con = self._connect()
        try:
            row = con.execute(
                "SELECT value FROM cursorDiskKV WHERE key=?", (key,)
            ).fetchone()
            if not row or row[0] is None:
                return False
            val = row[0]
            if isinstance(val, bytes):
                val = val.decode("utf-8", "ignore")
            obj = json.loads(val)
            if archived is not None:
                obj["isArchived"] = archived
            con.execute(
                "UPDATE cursorDiskKV SET value=? WHERE key=?",
                (json.dumps(obj, ensure_ascii=False), key),
            )
            con.commit()
            return True
        except (sqlite3.Error, json.JSONDecodeError):
            return False
        finally:
            con.close()

    def archive_chat(self, composer_id: str) -> list[str]:
        """Hide from Agents list (isArchived). Does not delete transcript files."""
        actions: list[str] = []

        def mark_archived(e: dict[str, Any]) -> None:
            e["isArchived"] = True

        if self._update_composer_header_entry(composer_id, mark_archived):
            actions.append("composerHeaders")
        if self._patch_composer_data_flags(composer_id, archived=True):
            actions.append("composerData")
        items = self.get_db_json("glass.localAgentProjects.v1", []) or []
        g_changed = False
        for e in items:
            if isinstance(e, dict) and str(e.get("id")) == composer_id:
                e["isArchived"] = True
                g_changed = True
        if g_changed:
            self.set_db_json("glass.localAgentProjects.v1", items)
            actions.append("glass.localAgentProjects")
        return actions

    def hard_delete_chat(
        self, composer_id: str, *, remove_transcript: bool = False
    ) -> list[str]:
        """Remove from Composer/Agents lists. Optionally delete agent-transcripts folder."""
        actions: list[str] = []
        if self._delete_composer_header_entry(composer_id):
            actions.append("composerHeaders")

        items = self.get_db_json("glass.localAgentProjects.v1", []) or []
        new_items = [
            e
            for e in items
            if not (isinstance(e, dict) and str(e.get("id")) == composer_id)
        ]
        if len(new_items) != len(items):
            self.set_db_json("glass.localAgentProjects.v1", new_items)
            actions.append("glass.localAgentProjects")

        if self.db_path.is_file():
            con = self._connect()
            try:
                # All KV rows for this composer (bubbles / checkpoints / diffs / composerData)
                cur = con.execute(
                    "DELETE FROM cursorDiskKV WHERE key LIKE ?",
                    (f"%{composer_id}%",),
                )
                if cur.rowcount:
                    actions.append(f"cursorDiskKV:{cur.rowcount}")
                cur2 = con.execute(
                    "DELETE FROM ItemTable WHERE key LIKE ?",
                    (f"%{composer_id}%",),
                )
                if cur2.rowcount:
                    actions.append(f"ItemTable:{cur2.rowcount}")
                con.commit()
            except sqlite3.Error:
                pass
            finally:
                con.close()

        if remove_transcript and self.projects_root.is_dir():
            for proj in self.projects_root.iterdir():
                tdir = proj / "agent-transcripts" / composer_id
                if tdir.is_dir():
                    shutil.rmtree(tdir, ignore_errors=True)
                    actions.append(f"transcript:{tdir}")
        return actions
