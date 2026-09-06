# -*- coding: utf-8 -*-
"""Cursor chat / transcript inventory + title heuristics."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from store import (
    ALLOWLIST_PATH,
    TITLE_OVERRIDES_PATH,
    SyncStore,
    load_json,
    save_json,
)

USER_QUERY_RE = re.compile(
    r"<user_query>\s*(.*?)\s*</user_query>",
    re.DOTALL | re.IGNORECASE,
)
TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class ChatRecord:
    composer_id: str
    cursor_name: str
    suggested_title: str
    subtitle: str = ""
    unified_mode: str = ""
    last_updated: int = 0
    created_at: int = 0
    is_archived: bool = False
    workspace_path: str = ""
    project_slug: str = ""
    transcript_path: str = ""
    first_user_preview: str = ""
    has_official_name: bool = False
    title_quality: str = "ok"  # ok | missing | weak | garbled
    allow_sync: bool = False
    override_title: str = ""


def fmt_ts(ms: int) -> str:
    if not ms:
        return "-"
    try:
        return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError, OverflowError):
        return str(ms)


def clean_title(text: str, max_len: int = 60) -> str:
    t = (text or "").strip()
    t = USER_QUERY_RE.search(t).group(1).strip() if USER_QUERY_RE.search(t) else t
    t = TAG_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    # drop leading timestamp lines leftovers
    t = re.sub(r"^(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday).{0,40}\d{4}[, ].*?(?=\s|$)", "", t, flags=re.I)
    t = t.strip(" \n\t-—:|")
    if len(t) > max_len:
        t = t[: max_len - 1].rstrip() + "…"
    return t or "(untitled)"


def score_title(name: str) -> str:
    n = (name or "").strip()
    if not n or n == "(untitled)":
        return "missing"
    # path-like short slugs seen in Documents/Codex
    if re.fullmatch(r"[a-z0-9]{1,3}(-[a-z0-9]{1,12}){0,4}", n.lower()) and not re.search(
        r"[\u4e00-\u9fff]", n
    ):
        return "garbled"
    if n.lower() in {"agent", "image", "ease", "c-c", "work", "outputs"}:
        return "garbled"
    # mostly replacement chars / question marks
    if n.count("?") >= max(2, len(n) // 3) or "\ufffd" in n:
        return "garbled"
    # very short ascii only
    if len(n) <= 3 and n.isascii():
        return "weak"
    # looks like raw XML
    if "<user_query>" in n.lower() or n.startswith("<"):
        return "garbled"
    return "ok"


def extract_first_user_text(transcript: Path) -> str:
    if not transcript.is_file():
        return ""
    try:
        with transcript.open(encoding="utf-8", errors="ignore") as f:
            for _ in range(30):
                line = f.readline()
                if not line:
                    break
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("role") != "user":
                    continue
                msg = obj.get("message") or {}
                content = msg.get("content")
                if isinstance(content, list):
                    parts = []
                    for c in content:
                        if isinstance(c, dict) and c.get("type") == "text":
                            parts.append(str(c.get("text") or ""))
                    return "\n".join(parts)
                if isinstance(content, str):
                    return content
    except OSError:
        return ""
    return ""


def find_transcript_for_composer(
    projects_root: Path, composer_id: str
) -> tuple[str, str, str]:
    """Return (project_slug, transcript_file, workspace_guess)."""
    index = build_transcript_index(projects_root)
    hit = index.get(composer_id)
    if hit:
        return hit[0], hit[1], ""
    return "", "", ""


def build_transcript_index(projects_root: Path) -> dict[str, tuple[str, str]]:
    """composer_id -> (project_slug, transcript_path). Built once per scan."""
    index: dict[str, tuple[str, str]] = {}
    if not projects_root.is_dir():
        return index
    for proj in projects_root.iterdir():
        if not proj.is_dir():
            continue
        base = proj / "agent-transcripts"
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if not child.is_dir():
                continue
            cid = child.name
            if cid in index:
                continue
            cand = child / f"{cid}.jsonl"
            if cand.is_file():
                index[cid] = (proj.name, str(cand))
                continue
            files = list(child.glob("*.jsonl"))
            if files:
                index[cid] = (proj.name, str(files[0]))
    return index


def workspace_from_header(e: dict[str, Any]) -> str:
    wi = e.get("workspaceIdentifier") or {}
    if isinstance(wi, dict):
        uri = wi.get("uri") or {}
        if isinstance(uri, dict):
            fs = str(uri.get("fsPath") or "").strip()
            if fs:
                return fs
        wid = str(wi.get("id") or "").strip()
        if wid == "empty-window":
            return "(空窗口)"
        # 只有 hash/数字 id、没有路径时不要当成独立工作区（后面用 transcript slug）
    return ""


def workspace_from_data_meta(meta: dict[str, Any] | None) -> str:
    if not meta:
        return ""
    wi = meta.get("workspaceIdentifier")
    if isinstance(wi, dict):
        return workspace_from_header({"workspaceIdentifier": wi})
    return ""


def decode_project_slug(slug: str) -> str:
    """把 Cursor projects 目录名尽量还原成路径，如 d-Document-front → d:\\Document\\front。"""
    s = (slug or "").strip()
    if not s or s.startswith("."):
        return ""
    if s == "empty-window":
        return "(空窗口)"
    if len(s) >= 3 and s[1] == "-" and s[0].isalpha():
        candidate = f"{s[0]}:\\{s[2:].replace('-', '\\')}"
        # 只有磁盘上真有这个目录才采用，避免把 workspace.json 等 slug 解错
        try:
            if Path(candidate).exists():
                return candidate
        except OSError:
            pass
    return ""


class ChatInventory:
    def __init__(self, store: SyncStore):
        self.store = store

    def allowlist(self) -> dict[str, bool]:
        data = load_json(ALLOWLIST_PATH, {"chats": {}})
        chats = data.get("chats") if isinstance(data, dict) else {}
        return {str(k): bool(v) for k, v in (chats or {}).items()}

    def set_allowlist(self, mapping: dict[str, bool]) -> None:
        save_json(ALLOWLIST_PATH, {"chats": mapping, "version": 1})

    def title_overrides(self) -> dict[str, str]:
        data = load_json(TITLE_OVERRIDES_PATH, {})
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if v}
        return {}

    def set_title_override(self, composer_id: str, title: str) -> None:
        m = self.title_overrides()
        if title.strip():
            m[composer_id] = title.strip()
        else:
            m.pop(composer_id, None)
        save_json(TITLE_OVERRIDES_PATH, m)

    def list_chats(self, *, light: bool = True) -> list[ChatRecord]:
        """List chats.

        light=True（默认，列表页）：不读 transcript 首条消息，显著加快。
        light=False：标题修复页用，补齐预览与弱标题建议。
        """
        headers_list = self.store.list_composer_header_entries()
        composers = headers_list
        allow = self.allowlist()
        overrides = self.title_overrides()
        data_map = self.store.map_composer_data_names()
        # 轻量模式也建 transcript 索引（只扫目录、不读正文），用来恢复项目归属
        tx_index = build_transcript_index(self.store.projects_root)
        out: list[ChatRecord] = []
        seen_ids: set[str] = set()

        for e in composers:
            if not isinstance(e, dict):
                continue
            cid = str(e.get("composerId") or "")
            if not cid or cid in seen_ids:
                continue
            seen_ids.add(cid)
            # Agents 侧边栏 Rename → composerData.name（优先）
            # composerHeaders.name 经常仍为空
            meta = data_map.get(cid) or {}
            header_name = str(e.get("name") or "").strip()
            data_name = str(meta.get("name") or "").strip()
            official = data_name or header_name
            hit = tx_index.get(cid)
            proj = hit[0] if hit else ""
            tpath = hit[1] if hit else ""
            first = ""
            if not light and tpath:
                first = extract_first_user_text(Path(tpath))
            suggested = clean_title(first) if first else (official or "(untitled)")
            override = overrides.get(cid, "")
            display_cursor = official or "(untitled)"
            quality = score_title(official) if official else "missing"
            wp = (
                workspace_from_header(e)
                or workspace_from_data_meta(meta)
                or decode_project_slug(proj)
            )
            rec = ChatRecord(
                composer_id=cid,
                cursor_name=display_cursor,
                suggested_title=override or (official if official else suggested),
                subtitle=str(e.get("subtitle") or meta.get("subtitle") or ""),
                unified_mode=str(
                    e.get("unifiedMode") or meta.get("unifiedMode") or ""
                ),
                last_updated=int(
                    e.get("lastUpdatedAt")
                    or meta.get("lastUpdatedAt")
                    or e.get("createdAt")
                    or meta.get("createdAt")
                    or 0
                ),
                created_at=int(e.get("createdAt") or meta.get("createdAt") or 0),
                is_archived=bool(e.get("isArchived") or meta.get("isArchived")),
                workspace_path=wp,
                project_slug=proj,
                transcript_path=tpath if (not light or tpath) else "",
                first_user_preview=clean_title(first, 80) if first else "",
                has_official_name=bool(official),
                title_quality=quality,
                allow_sync=bool(allow.get(cid, False)),
                override_title=override,
            )
            out.append(rec)

        # Headers 往往不全：Cursor 工作区里能看到的对话，很多只在 composerData 里
        for cid, meta in data_map.items():
            if cid in seen_ids:
                continue
            hit = tx_index.get(cid)
            proj = hit[0] if hit else ""
            tpath = hit[1] if hit else ""
            data_name = str(meta.get("name") or "").strip()
            wp = workspace_from_data_meta(meta) or decode_project_slug(proj)
            # 跳过完全空壳（无标题、无路径、无 transcript）——避免再次灌满「未绑定」
            if not data_name and not wp and not tpath:
                continue
            seen_ids.add(cid)
            first = ""
            if not light and tpath:
                first = extract_first_user_text(Path(tpath))
            suggested = clean_title(first) if first else (data_name or "(untitled)")
            override = overrides.get(cid, "")
            official = data_name
            out.append(
                ChatRecord(
                    composer_id=cid,
                    cursor_name=official or "(no header)",
                    suggested_title=override or official or suggested,
                    subtitle=str(meta.get("subtitle") or ""),
                    unified_mode=str(meta.get("unifiedMode") or ""),
                    last_updated=int(
                        meta.get("lastUpdatedAt") or meta.get("createdAt") or 0
                    ),
                    created_at=int(meta.get("createdAt") or 0),
                    is_archived=bool(meta.get("isArchived")),
                    workspace_path=wp,
                    project_slug=proj,
                    transcript_path=tpath,
                    first_user_preview=clean_title(first, 80) if first else "",
                    has_official_name=bool(official),
                    title_quality=score_title(official) if official else "missing",
                    allow_sync=bool(allow.get(cid, False)),
                    override_title=override,
                )
            )

        # transcript 有、但 headers/composerData 都没有的（仅完整模式补预览）
        if not light:
            for cid, (proj_name, tpath) in tx_index.items():
                if cid in seen_ids:
                    continue
                jsonl = Path(tpath)
                meta = data_map.get(cid) or {}
                data_name = str(meta.get("name") or "").strip()
                first = extract_first_user_text(jsonl)
                suggested = clean_title(first) if first else "(untitled)"
                override = overrides.get(cid, "")
                official = data_name
                out.append(
                    ChatRecord(
                        composer_id=cid,
                        cursor_name=official or "(no header)",
                        suggested_title=override or official or suggested,
                        project_slug=proj_name,
                        transcript_path=tpath if jsonl.is_file() else "",
                        first_user_preview=suggested,
                        has_official_name=bool(official),
                        title_quality=score_title(official)
                        if official
                        else "missing",
                        allow_sync=bool(allow.get(cid, False)),
                        override_title=override,
                        last_updated=int(
                            meta.get("lastUpdatedAt")
                            or (
                                jsonl.stat().st_mtime * 1000
                                if jsonl.is_file()
                                else 0
                            )
                        ),
                        workspace_path=workspace_from_data_meta(meta)
                        or decode_project_slug(proj_name),
                        unified_mode=str(meta.get("unifiedMode") or ""),
                        subtitle=str(meta.get("subtitle") or ""),
                        is_archived=bool(meta.get("isArchived")),
                    )
                )

        out.sort(key=lambda r: r.last_updated, reverse=True)
        return out

    def apply_title_to_cursor(self, composer_id: str, title: str) -> list[str]:
        """Write title into composerHeaders + composerData. Returns actions taken."""
        title = title.strip()
        if not title:
            return []
        actions: list[str] = []

        def set_name(e: dict[str, Any]) -> None:
            e["name"] = title

        if self.store._update_composer_header_entry(composer_id, set_name):
            actions.append("composerHeaders")
        if self.store.set_composer_data_name(composer_id, title):
            actions.append("composerData")
        self.set_title_override(composer_id, title)
        actions.append("title_overrides")
        return actions

    def batch_fill_missing_from_first_message(
        self, records: list[ChatRecord], only_missing: bool = True
    ) -> int:
        n = 0
        for r in records:
            if only_missing and r.has_official_name and r.title_quality == "ok":
                continue
            title = r.override_title or r.suggested_title
            if not title or title == "(untitled)":
                continue
            if only_missing and r.has_official_name and title == r.cursor_name:
                continue
            self.apply_title_to_cursor(r.composer_id, title)
            n += 1
        return n

    def archive_many(self, composer_ids: list[str]) -> int:
        from backup import backup_chats

        n = 0
        allow = self.allowlist()
        ids = [c for c in composer_ids if c]
        if ids:
            backup_chats(self.store, ids, reason="archive")
        for cid in ids:
            acts = self.store.archive_chat(cid)
            if acts:
                n += 1
            allow.pop(cid, None)
        self.set_allowlist(allow)
        return n

    def hard_delete_many(
        self, composer_ids: list[str], *, remove_transcript: bool = False
    ) -> int:
        from backup import backup_chats

        n = 0
        allow = self.allowlist()
        overrides = self.title_overrides()
        ids = [c for c in composer_ids if c]
        if ids:
            backup_chats(self.store, ids, reason="hard-delete")
        for cid in ids:
            acts = self.store.hard_delete_chat(
                cid, remove_transcript=remove_transcript
            )
            if acts:
                n += 1
            allow.pop(cid, None)
            overrides.pop(cid, None)
        self.set_allowlist(allow)
        save_json(TITLE_OVERRIDES_PATH, overrides)
        return n
