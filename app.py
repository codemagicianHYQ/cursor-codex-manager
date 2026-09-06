# -*- coding: utf-8 -*-
"""Cursor / Codex 管理器."""
from __future__ import annotations

import csv
import io
import time
from pathlib import Path

import pandas as pd
import streamlit as st

import streamlit.components.v1 as components

from backup import BACKUP_ROOT, backup_workspace_paths, list_recent_backups
from chats import ChatInventory, fmt_ts
from cursor_workspace_store import CursorStore
from store import SyncStore, detect_user_data, ensure_data_dir, process_running

st.set_page_config(
    page_title="Cursor / Codex 管理器",
    page_icon="◇",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 柔和深灰主题 + 保留顶栏控件（侧边栏折叠 / 主题切换）
st.markdown(
    """
<style>
  :root {
    /* GitHub Dim 风格：别太黑 */
    --bg: #2b303b;
    --bg-elev: #363d4a;
    --bg-soft: #3d4450;
    --ink: #e6edf3;
    --muted: #9aa4b2;
    --line: rgba(230,237,243,0.12);
    --line-strong: rgba(230,237,243,0.20);
    --accent: #6cb6ff;
    --ok: #57ab5a;
    --hover: #444c5c;
  }

  html, body, [class*="css"] {
    font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
    color: var(--ink);
  }
  code, pre, .stCode {
    font-family: Consolas, "Cascadia Mono", monospace !important;
  }

  /* 顶栏：矮、半透明，不压内容 */
  header[data-testid="stHeader"] {
    background: rgba(43,48,59,0.92) !important;
    backdrop-filter: blur(8px);
    border-bottom: 1px solid var(--line) !important;
    height: auto !important;
    min-height: 0 !important;
  }
  [data-testid="stToolbar"] {
    background: transparent !important;
    color: var(--ink) !important;
    min-height: 2.4rem !important;
  }
  [data-testid="stToolbar"] button,
  [data-testid="stToolbar"] span,
  [data-testid="stToolbar"] svg {
    color: var(--ink) !important;
    fill: var(--ink) !important;
  }
  /* 只藏 Deploy */
  [data-testid="stAppDeployButton"],
  .stAppDeployButton,
  a[href*="share.streamlit"] {
    display: none !important;
  }
  [data-testid="stDecoration"] { display: none !important; }
  footer { display: none !important; }

  .stApp, [data-testid="stAppViewContainer"] {
    background: var(--bg) !important;
    color: var(--ink);
  }
  /* 给顶栏留空，避免遮挡标题卡片 */
  .block-container {
    padding-top: 2.6rem !important;
    padding-bottom: 2.2rem;
    max-width: 1160px;
  }

  h1 {
    font-size: 1.55rem !important;
    font-weight: 650 !important;
    letter-spacing: -0.03em;
    color: var(--ink) !important;
    margin-bottom: 0.15rem !important;
  }
  h2, h3 { color: var(--ink) !important; }

  [data-testid="stMetricValue"] { font-size: 1.35rem; font-weight: 650; color: var(--ink) !important; }
  [data-testid="stMetricLabel"] { color: var(--muted) !important; }

  .stTabs [data-baseweb="tab-list"] {
    gap: 0.15rem;
    border-bottom: 1px solid var(--line) !important;
    background: transparent !important;
  }
  .stTabs [data-baseweb="tab"] {
    height: 2.4rem;
    padding: 0 0.9rem !important;
    background: transparent !important;
    color: var(--muted) !important;
    border-radius: 8px 8px 0 0 !important;
  }
  .stTabs [data-baseweb="tab"] * { color: inherit !important; }
  .stTabs [data-baseweb="tab"]:hover {
    background: var(--hover) !important;
    color: var(--ink) !important;
  }
  .stTabs [aria-selected="true"] {
    color: var(--ink) !important;
    box-shadow: inset 0 -2px 0 var(--accent) !important;
    background: transparent !important;
  }

  div.stButton > button {
    border-radius: 8px !important;
    border: 1px solid var(--line-strong) !important;
    background: var(--bg-elev) !important;
    color: var(--ink) !important;
    font-weight: 550 !important;
  }
  div.stButton > button p, div.stButton > button span { color: inherit !important; }
  div.stButton > button:hover,
  div.stButton > button:focus {
    background: var(--hover) !important;
    border-color: rgba(108,182,255,.5) !important;
    color: #fff !important;
  }
  div.stButton > button[kind="primary"],
  div.stButton > button[data-testid="baseButton-primary"] {
    background: #316dca !important;
    border-color: #539bf5 !important;
    color: #fff !important;
  }
  div.stButton > button[kind="primary"]:hover,
  div.stButton > button[data-testid="baseButton-primary"]:hover {
    background: #539bf5 !important;
    color: #fff !important;
  }
  div.stButton > button:disabled { opacity: .45 !important; color: var(--muted) !important; }

  [data-testid="stTextInput"] input,
  [data-baseweb="select"] > div,
  [data-testid="stCode"] {
    background: var(--bg-elev) !important;
    color: var(--ink) !important;
    border-color: var(--line-strong) !important;
  }

  div[data-testid="stDataFrame"],
  [data-testid="stDataFrameResizable"] {
    border: 1px solid var(--line) !important;
    border-radius: 10px !important;
    background: var(--bg-elev) !important;
    overflow: hidden !important;
  }

  .hint { color: var(--muted); font-size: 0.88rem; line-height: 1.5; margin: 0.15rem 0 0.85rem; }
  [data-testid="stCaption"], .stCaption { color: var(--muted) !important; }

  .hero-card {
    border: 1px solid var(--line);
    background: linear-gradient(145deg, rgba(108,182,255,.12), rgba(54,61,74,.95));
    border-radius: 12px;
    padding: 0.9rem 1.05rem;
    margin-bottom: 0.75rem;
  }
  .status-pill {
    display: inline-block;
    padding: 0.16rem 0.55rem;
    border-radius: 999px;
    font-size: 0.76rem;
    font-weight: 550;
    margin-right: 0.3rem;
    border: 1px solid var(--line);
    background: var(--bg-elev);
  }
  .pill-on { color: var(--ok); border-color: rgba(87,171,90,.4); background: rgba(87,171,90,.12); }
  .pill-off { color: var(--muted); }

  [data-testid="stSidebar"] {
    background: #24292f !important;
    border-right: 1px solid var(--line) !important;
  }
  [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] { color: var(--ink); }
  [data-testid="stSidebar"] pre,
  [data-testid="stSidebar"] code,
  [data-testid="stSidebar"] [data-testid="stCode"] {
    background: var(--bg-elev) !important;
    color: var(--ink) !important;
    border: 1px solid var(--line) !important;
  }
  [data-testid="stSidebarCollapsedControl"],
  button[kind="headerNoPadding"] {
    color: var(--ink) !important;
    background: var(--bg-elev) !important;
    border: 1px solid var(--line) !important;
  }
  [data-testid="stSidebarCollapsedControl"] svg,
  button[kind="headerNoPadding"] svg {
    fill: var(--ink) !important;
    color: var(--ink) !important;
  }

[data-testid="stAlert"] { border-radius: 10px !important; }

  /* 暗色主题弹出菜单：浅色字（之前误写成黑字，暗底看不清） */
  [data-baseweb="popover"],
  [data-baseweb="menu"],
  [role="menu"],
  [data-testid="stMainMenuPopover"],
  ul[role="menu"] {
    color: #e6edf3 !important;
    background-color: #363d4a !important;
  }
  [data-baseweb="popover"] *,
  [data-baseweb="menu"] *,
  [role="menu"] *,
  [data-testid="stMainMenuPopover"] * {
    color: #e6edf3 !important;
  }
  [data-baseweb="popover"] svg,
  [role="menu"] svg,
  [data-testid="stMainMenuPopover"] svg {
    fill: #e6edf3 !important;
    color: #e6edf3 !important;
  }
  [data-baseweb="popover"] li:hover,
  [role="menuitem"]:hover,
  [data-baseweb="menu"] li:hover {
    background: #444c5c !important;
    color: #ffffff !important;
  }
  /* 藏主题切换（若仍出现） */
  [data-testid="stToolbar"] [aria-label*="theme" i],
  [data-testid="stToolbar"] [aria-label*="Theme"],
  [data-testid="stMainMenuPopover"] [data-testid="stThemeSelector"],
  button[aria-label="System"],
  button[aria-label="Light"],
  button[aria-label="Dark"],
  button[aria-label="跟随系统"],
  button[aria-label="浅色"],
  button[aria-label="深色"] {
    display: none !important;
  }


</style>
""",
    unsafe_allow_html=True,
)

# 右上角菜单英文 → 中文
components.html(
    """
<script>
(function () {
  const map = {
    "Rerun": "重新运行",
    "Always rerun": "始终重新运行",
    "Auto rerun": "自动重新运行",
    "Clear cache": "清除缓存",
    "Print": "打印",
    "Record a screencast": "录制屏幕",
    "Record screen": "录制屏幕",
    "Settings": "设置",
    "About": "关于",
    "Main menu": "主菜单",
    "Developer options": "开发者选项",
    "Made with Streamlit": "基于 Streamlit",
    "File change.": "文件已更改。",
    "Source file changed.": "源文件已更改。",
  };
  function walk(node) {
    if (!node) return;
    if (node.nodeType === 3) {
      const t = node.nodeValue;
      if (!t) return;
      const s = t.trim();
      if (map[s]) node.nodeValue = t.replace(s, map[s]);
      return;
    }
    for (const c of node.childNodes) walk(c);
  }
  function apply() {
    try {
      const root = window.parent.document;
      // 藏主题切换按钮行
      root.querySelectorAll('button').forEach((btn) => {
        const a = (btn.getAttribute('aria-label') || '') + (btn.textContent || '');
        if (/^(System|Light|Dark|跟随系统|浅色|深色)$/.test(a.trim()) ||
            /跟随系统|浅色|深色|System|Light|Dark/.test(a) && btn.closest('[role="menu"], [data-baseweb="popover"]')) {
          const row = btn.closest('[class*="emotion"]') || btn.parentElement;
          if (row && row.querySelectorAll('button').length <= 3) {
            row.style.display = 'none';
          }
          btn.style.display = 'none';
        }
      });
      const scopes = [
        root.querySelector('[data-testid="stToolbar"]'),
        root.querySelector('[data-testid="stHeader"]'),
        root.querySelector('#MainMenu'),
        ...root.querySelectorAll('[data-baseweb="popover"]'),
        ...root.querySelectorAll('[data-baseweb="menu"]'),
        ...root.querySelectorAll('[role="menu"]'),
        ...root.querySelectorAll('[role="listbox"]'),
      ].filter(Boolean);
      scopes.forEach(walk);
      root.querySelectorAll("[aria-label]").forEach((el) => {
        const a = el.getAttribute("aria-label");
        if (a && map[a]) el.setAttribute("aria-label", map[a]);
      });
    } catch (e) {}
  }
  apply();
  try {
    new MutationObserver(apply).observe(window.parent.document.body, {
      childList: true, subtree: true, characterData: true
    });
  } catch (e) {}
})();
</script>
""",
    height=0,
)

ensure_data_dir()
store = SyncStore()
inv = ChatInventory(store)
ws_store = CursorStore(Path(detect_user_data()))


def _bust_caches() -> None:
    st.session_state.pop("cache_ws_items", None)
    st.session_state.pop("cache_chat_light", None)
    st.session_state.pop("title_records", None)


def load_workspaces(*, force: bool = False):
    if force or "cache_ws_items" not in st.session_state:
        st.session_state.cache_ws_items = ws_store.list_workspaces()
    return st.session_state.cache_ws_items


def load_chats_light(*, force: bool = False):
    if force or "cache_chat_light" not in st.session_state:
        st.session_state.cache_chat_light = inv.list_chats(light=True)
    return st.session_state.cache_chat_light


def header_id_set() -> set[str]:
    return {
        str(e.get("composerId"))
        for e in inv.store.list_composer_header_entries()
        if isinstance(e, dict) and e.get("composerId")
    }


def workspace_label(r) -> str:
    wp = (r.workspace_path or "").strip()
    if wp:
        return str(Path(wp)).replace("/", "\\") if not wp.startswith("(") else wp
    if r.project_slug:
        return f"(slug) {r.project_slug}"
    return "(未绑定 · 无路径)"


def process_status_cached(ttl_sec: float = 20.0) -> tuple[bool, bool]:
    now = time.time()
    cache = st.session_state.get("_proc_status")
    if cache and now - cache[0] < ttl_sec:
        return cache[1], cache[2]
    c_run = process_running("Cursor.exe")
    x_run = process_running("ChatGPT.exe") or process_running("codex.exe")
    st.session_state["_proc_status"] = (now, c_run, x_run)
    return c_run, x_run


def write_guard(widget_key: str = "force_write_ok") -> bool:
    """Return True if writes are allowed. Checkbox only shown when Cursor is running."""
    c_run, _ = process_status_cached()
    if not c_run:
        return True
    st.warning("Cursor 正在运行：写入可能被内存覆盖。建议先完全退出 Cursor。")
    return st.checkbox("仍要写入（我已知晓风险）", value=False, key=widget_key)


# ---------- shell ----------
c_run, x_run = process_status_cached()
top_l, top_r = st.columns([3.4, 1.3])
with top_l:
    st.title("Cursor / Codex 管理器")
    st.markdown(
        '<p class="hint">工作区清理 · 对话归档/硬删（轻量备份）· Codex 同步。双击 <code>open.vbs</code> 会重启服务。</p>',
        unsafe_allow_html=True,
    )
with top_r:
    pills = []
    pills.append(
        f'<span class="status-pill {"pill-on" if c_run else "pill-off"}">Cursor {"运行中" if c_run else "未开"}</span>'
    )
    pills.append(
        f'<span class="status-pill {"pill-on" if x_run else "pill-off"}">Codex {"运行中" if x_run else "未开"}</span>'
    )
    if store.headers_use_table():
        pills.append('<span class="status-pill pill-on">对话列表：新表</span>')
    st.markdown(
        f'<div class="hero-card">{"".join(pills)}'
        f'<div class="hint" style="margin:0.55rem 0 0">用户数据目录<br><code>{store.cursor_user_data}</code></div></div>',
        unsafe_allow_html=True,
    )

if not store.cursor_db_ok():
    st.error(f"找不到 Cursor 数据库：`{store.db_path}`")
    st.stop()
if not store.ok():
    st.warning(f"找不到 Codex 状态 `{store.global_state_path}`（同步页不可用，其余可用）")

with st.sidebar:
    st.markdown("### 路径")
    st.code(
        f"Codex 主目录\n{store.codex_home}\n\n"
        f"Cursor 主目录\n{store.cursor_home}\n\n"
        f"用户数据\n{store.cursor_user_data}\n\n"
        f"数据库\n{store.db_path}",
        language=None,
    )
    st.markdown("### 备份")
    st.caption(f"目录：`{BACKUP_ROOT}`")
    recent = list_recent_backups(8)
    if not recent:
        st.caption("尚无备份（归档/硬删/清工作区时自动生成）")
    else:
        reason_cn = {
            "archive": "归档",
            "hard-delete": "硬删除",
            "workspace-remove": "清工作区",
            "smoke-test": "测试",
        }
        for b in recent:
            reason = reason_cn.get(str(b.get("reason") or ""), str(b.get("reason") or "-"))
            st.markdown(f"- `{b.get('name')}` · {reason}")
    if st.button("清空列表缓存并刷新", use_container_width=True):
        _bust_caches()
        st.rerun()

tab_ws, tab_chats, tab_sync, tab_titles, tab_mcp, tab_help = st.tabs(
    ["工作区", "对话", "同步", "标题", "MCP", "说明"]
)

# ---------- Workspaces ----------
with tab_ws:
    st.markdown(
        '<p class="hint">只改 Cursor 列表缓存，不删磁盘源码。删除前会做列表快照备份。</p>',
        unsafe_allow_html=True,
    )
    if not ws_store.ok():
        st.error(f"找不到 state.vscdb：`{ws_store.db_path}`")
    else:
        allow_write = write_guard("force_write_ws")

        f1, f2, f3 = st.columns([2.4, 1.2, 1])
        with f1:
            q_ws = st.text_input(
                "筛选",
                key="ws_list_filter",
                placeholder="路径 / 名称",
                label_visibility="collapsed",
            )
        with f2:
            only_ghost = st.checkbox("只看幽灵", value=False)
        with f3:
            if st.button("刷新", key="ws_refresh", use_container_width=True):
                _bust_caches()
                st.rerun()

        items = list(load_workspaces())
        if q_ws.strip():
            ql = q_ws.strip().lower()
            items = [
                i
                for i in items
                if ql in i.path.lower() or ql in i.display_name.lower()
            ]
        if only_ghost:
            items = [i for i in items if not i.exists]

        m1, m2, m3 = st.columns(3)
        m1.metric("可见", len(items))
        m2.metric("幽灵", sum(1 for i in items if not i.exists))
        m3.metric("repo", sum(1 for i in items if i.kind == "repo"))

        rows = []
        for item in items[:300]:
            rows.append(
                {
                    "名称": item.display_name,
                    "状态": "幽灵"
                    if not item.exists
                    else ("repo" if item.kind == "repo" else "本地"),
                    "路径": item.path,
                    "来源": ", ".join(item.sources),
                }
            )
        df = pd.DataFrame(rows)
        event = st.dataframe(
            df,
            width="stretch",
            hide_index=True,
            height=min(520, 48 + 35 * max(len(df), 1)),
            on_select="rerun",
            selection_mode="multi-row",
            key="ws_table",
            column_config={
                "名称": st.column_config.TextColumn(width="medium"),
                "状态": st.column_config.TextColumn(width="small"),
                "路径": st.column_config.TextColumn(width="large"),
                "来源": st.column_config.TextColumn(width="medium"),
            },
        )
        sel_idx = list(event.selection.rows) if event and event.selection else []
        a1, a2 = st.columns([1, 3])
        with a1:
            do_del = st.button(
                f"删除选中 ({len(sel_idx)})",
                type="primary",
                disabled=not sel_idx or not allow_write,
                use_container_width=True,
                key="ws_batch_del",
            )
        with a2:
            st.caption("点表格左侧多选。远程 SSH/WSL 也可清（只清本地缓存）。")
        if do_del and allow_write:
            paths = [items[i].path for i in sel_idx if 0 <= i < len(items)]
            bak = backup_workspace_paths(store, paths, reason="workspace-remove")
            for p in paths:
                ws_store.remove_workspace(p)
            _bust_caches()
            msg = f"已移除 {len(paths)} 条 · 请退出并重开 Cursor"
            if bak:
                msg += f" · 备份 `{bak.name}`"
            st.success(msg)
            st.rerun()

# ---------- Chats ----------
with tab_chats:
    st.markdown(
        '<p class="hint">先选项目。归档/硬删前自动轻量备份（headers + composerData，不含巨型 bubble 正文）。</p>',
        unsafe_allow_html=True,
    )
    allow_write = write_guard("force_write_chats")

    controls = st.columns([1, 1.05, 1.05, 0.95, 1.2])
    with controls[0]:
        if st.button("刷新", key="chat_refresh", use_container_width=True):
            _bust_caches()
            st.rerun()
    with controls[1]:
        show_arch = st.checkbox("含归档", value=False)
    with controls[2]:
        only_sidebar = st.checkbox("侧边栏同源", value=False)
    with controls[3]:
        hard = st.toggle("硬删除", value=False)
    with controls[4]:
        q = st.text_input(
            "搜索",
            key="chat_q_simple",
            placeholder="标题",
            label_visibility="collapsed",
        )

    del_tx = False
    if hard:
        del_tx = st.checkbox(
            "同时删除本地 agent-transcripts 文件夹",
            value=False,
            key="hard_del_transcript",
            help="勾选后会删 %USERPROFILE%\\.cursor\\projects\\...\\agent-transcripts\\<id>",
        )
        st.caption(
            "硬删除：composerHeaders + composerData + bubble/checkpoint 等 KV。"
            "轻量备份不含 bubble（体积过大）。"
        )

    records = load_chats_light()
    hdr_ids = header_id_set()

    groups: dict[str, list] = {}
    labels: dict[str, str] = {}
    seen: set[str] = set()
    ql = q.strip().lower()
    for r in records:
        if r.composer_id in seen:
            continue
        if only_sidebar and r.composer_id not in hdr_ids:
            continue
        if not show_arch and r.is_archived:
            continue
        if ql and ql not in (r.cursor_name + r.composer_id).lower():
            continue
        seen.add(r.composer_id)
        label = workspace_label(r)
        gid = label.casefold()
        labels.setdefault(gid, label)
        groups.setdefault(gid, []).append(r)

    for gid in groups:
        groups[gid].sort(key=lambda x: x.last_updated, reverse=True)

    def group_rank(g: str) -> tuple:
        label = labels[g]
        unbound = 1 if ("未绑定" in label or label.startswith("(slug)")) else 0
        latest = max((x.last_updated for x in groups[g]), default=0)
        return (unbound, -latest)

    ordered = sorted(groups.keys(), key=group_rank)
    if not ordered:
        st.info("没有匹配的对话")
    else:
        options = []
        for g in ordered:
            lab = labels[g]
            short = Path(lab).name if not lab.startswith("(") else lab
            options.append(f"{short}  ·  {len(groups[g])} 条")

        pick = st.selectbox(
            "项目", options, key="chat_ws_pick", label_visibility="collapsed"
        )
        try:
            gid = ordered[options.index(pick)]
        except ValueError:
            gid = ordered[0]
        flat = groups[gid]
        n_side = sum(1 for r in flat if r.composer_id in hdr_ids)

        table_key = f"chat_table_{abs(hash(gid)) % 10**12}"

        c1, c2, c3 = st.columns(3)
        c1.metric("本项目", len(flat))
        c2.metric("侧边栏同源", n_side)
        c3.metric("仅 Data", len(flat) - n_side)

        chat_rows = []
        for r in flat:
            chat_rows.append(
                {
                    "标题": r.cursor_name or "(untitled)",
                    "时间": fmt_ts(r.last_updated),
                    "来源": "侧边栏" if r.composer_id in hdr_ids else "仅Data",
                    "状态": "归档" if r.is_archived else "活跃",
                    "_id": r.composer_id,
                }
            )
        cdf = pd.DataFrame(chat_rows)
        cevent = st.dataframe(
            cdf.drop(columns=["_id"]),
            width="stretch",
            hide_index=True,
            height=min(560, 48 + 35 * max(len(cdf), 1)),
            on_select="rerun",
            selection_mode="multi-row",
            key=table_key,
            column_config={
                "标题": st.column_config.TextColumn(width="large"),
                "时间": st.column_config.TextColumn(width="medium"),
                "来源": st.column_config.TextColumn(width="small"),
                "状态": st.column_config.TextColumn(width="small"),
            },
        )
        crow = list(cevent.selection.rows) if cevent and cevent.selection else []
        ids = [flat[i].composer_id for i in crow if 0 <= i < len(flat)]

        b1, b2, b3 = st.columns([1.25, 1.45, 2])
        label = "硬删除选中" if hard else "归档选中"
        if b1.button(
            f"{label} ({len(ids)})",
            type="primary",
            disabled=not ids or not allow_write,
            use_container_width=True,
        ):
            if hard:
                n = inv.hard_delete_many(ids, remove_transcript=del_tx)
                st.success(
                    f"已硬删除 {n} 条"
                    + ("（含 transcript）" if del_tx else "")
                    + " · 已轻量备份 · 请退出并重开 Cursor"
                )
            else:
                n = inv.archive_many(ids)
                st.success(f"已归档 {n} 条 · 已轻量备份")
            _bust_caches()
            st.rerun()
        all_label = "硬删除本项目全部" if hard else "归档本项目全部"
        if b2.button(all_label, disabled=not allow_write, use_container_width=True):
            all_ids = [r.composer_id for r in flat]
            if hard:
                n = inv.hard_delete_many(all_ids, remove_transcript=del_tx)
                st.success(
                    f"已硬删除 {n} 条"
                    + ("（含 transcript）" if del_tx else "")
                    + " · 已轻量备份 · 请退出并重开 Cursor"
                )
            else:
                n = inv.archive_many(all_ids)
                st.success(f"已归档 {n} 条 · 已轻量备份")
            _bust_caches()
            st.rerun()
        b3.caption(f"`{labels[gid]}`")

# ---------- Sync ----------
with tab_sync:
    if not store.ok():
        st.info("未检测到 Codex 全局状态，跳过同步页。")
    else:
        st.subheader("保持导入同步")
        state = store.get_sync_state()
        groups_sel = store.categorize_selection(state.selection)

        c1, c2, c3 = st.columns(3)
        with c1:
            cur_on = st.checkbox(
                "同步来源：Cursor", value=state.keep_cursor, key="prov_cursor"
            )
        with c2:
            cl_on = st.checkbox(
                "同步来源：Claude Code",
                value=state.keep_claude,
                key="prov_claude",
            )
        with c3:
            st.caption(f"版本 = {state.version}")

        st.markdown("**大类**")
        f1, f2 = st.columns(2)
        sel_flags = {k: v for k, v in groups_sel["flags"]}
        chats_flag = f1.checkbox(
            "对话 chats（建议关）",
            value=bool(sel_flags.get("chats", False)),
            key="flag_chats",
        )
        projects_flag = f2.checkbox(
            "项目 projects",
            value=bool(sel_flags.get("projects", False)),
            key="flag_projects",
        )

        st.markdown("**配置项**")
        mcp_on = st.checkbox(
            "MCP（容易把 v0 等又同步回来）",
            value=any(v for _, v in groups_sel["MCP"]) if groups_sel["MCP"] else False,
            key="cat_mcp",
        )
        skills_on = st.checkbox(
            "技能 Skills",
            value=any(v for _, v in groups_sel["SKILLS"])
            if groups_sel["SKILLS"]
            else False,
            key="cat_skills",
        )
        config_on = st.checkbox(
            "配置 CONFIG",
            value=any(v for _, v in groups_sel["CONFIG"])
            if groups_sel["CONFIG"]
            else False,
            key="cat_config",
        )

        with st.expander("原始选择项", expanded=False):
            for k, v in sorted(state.selection.items()):
                st.code(f"{'开 ' if v else '关 '}  {k}")

        bsave, bpreset = st.columns(2)
        if bsave.button("保存", type="primary", use_container_width=True):
            state = store.set_provider(state, "cursor", cur_on)
            state = store.set_provider(state, "claude-code", cl_on)
            state.selection["chats"] = chats_flag
            state.selection["projects"] = projects_flag
            state = store.set_category_prefix(state, "MCP_SERVER_CONFIG", mcp_on)
            state = store.set_category_prefix(state, "SKILLS", skills_on)
            state = store.set_category_prefix(state, "CONFIG", config_on)
            store.set_sync_state(state)
            st.success("已写入。请重启 Codex / ChatGPT。")
            st.rerun()
        if bpreset.button("推荐：关闭 MCP + 对话同步", use_container_width=True):
            state = store.get_sync_state()
            state.selection["chats"] = False
            state = store.set_category_prefix(state, "MCP_SERVER_CONFIG", False)
            store.set_sync_state(state)
            st.success("已关闭。请重启 Codex。")
            st.rerun()

# ---------- Titles ----------
with tab_titles:
    st.markdown(
        '<p class="hint">Rename 写在 composerData.name。默认不扫全文；需要时再点扫描。</p>',
        unsafe_allow_html=True,
    )
    allow_write = write_guard("force_write_titles")
    if st.button("扫描缺标题（读 transcript，稍慢）", key="title_scan"):
        with st.spinner("扫描中…"):
            st.session_state["title_records"] = inv.list_chats(light=False)
        st.rerun()

    records = st.session_state.get("title_records")
    if not records:
        st.info("点上方按钮后再编辑 / 导出。")
    else:
        need = [
            r
            for r in records
            if (not r.is_archived)
            and (r.title_quality != "ok" or not r.has_official_name)
        ]
        st.metric("待补标题", len(need))
        if st.button(
            "一键填充缺 name", type="primary", disabled=not allow_write
        ):
            n = inv.batch_fill_missing_from_first_message(need, only_missing=True)
            st.success(f"已写回 {n} 条。请 Cursor Reload Window。")
            st.session_state.pop("title_records", None)
            st.rerun()

        q2 = st.text_input("筛选", key="title_filter", placeholder="标题关键字")
        seen_title_ids: set[str] = set()
        for r in need[:60]:
            if r.composer_id in seen_title_ids:
                continue
            seen_title_ids.add(r.composer_id)
            if q2.strip() and q2.strip().lower() not in (
                r.cursor_name + r.suggested_title + r.composer_id
            ).lower():
                continue
            with st.expander(f"{r.cursor_name} · {fmt_ts(r.last_updated)}"):
                st.caption(f"{r.composer_id} · {r.title_quality}")
                st.text(r.first_user_preview)
                new_title = st.text_input(
                    "标题",
                    value=r.override_title or r.suggested_title,
                    key=f"title_{r.composer_id}",
                )
                if st.button(
                    "应用", key=f"apply_{r.composer_id}", disabled=not allow_write
                ):
                    actions = inv.apply_title_to_cursor(r.composer_id, new_title)
                    st.success(", ".join(actions))

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(
            [
                "composer_id",
                "cursor_name",
                "suggested_title",
                "quality",
                "project",
                "workspace",
                "transcript",
            ]
        )
        for r in records:
            w.writerow(
                [
                    r.composer_id,
                    r.cursor_name,
                    r.suggested_title,
                    r.title_quality,
                    r.project_slug,
                    r.workspace_path,
                    r.transcript_path,
                ]
            )
        st.download_button(
            "导出 CSV",
            data=buf.getvalue().encode("utf-8-sig"),
            file_name="cursor_chat_titles.csv",
            mime="text/csv",
        )

# ---------- MCP ----------
with tab_mcp:
    mcp = store.read_mcp_servers()
    codex_mcp = store.read_codex_mcp_names()
    left, right = st.columns(2)
    with left:
        st.markdown("**Cursor mcp.json**")
        st.write(mcp or "（空）")
    with right:
        st.markdown("**Codex mcp_servers**")
        st.write(codex_mcp or "（无）")
    st.caption(
        "skills-cursor: "
        + ", ".join(store.list_skills(store.skills_cursor))[:400]
    )

# ---------- Help ----------
with tab_help:
    st.markdown(
        f"""
### 日常怎么开

1. 双击 `open.vbs`：结束旧 8501 → 启动 → 打开网页  
2. 仍空白：关标签 → 再双击 → Ctrl+F5  
3. `python launch.py --reuse`：已运行则只开浏览器  

### 备份

- 归档 / 硬删 / 清工作区前自动写入 `{BACKUP_ROOT}`  
- 含 `composerHeaders` + `composerData` + 相关 ItemTable 键  
- **不含** `bubbleId` 正文（太大）；硬删后消息不可从轻量备份完整还原  

### 对话条数

侧边栏看 `composerHeaders` 表；工具还会扫 `composerData`。「侧边栏同源」可对齐。

### 清理建议

退出 Cursor → 对话页操作 → 再开 Cursor。  
硬删可勾选「同时删除 transcript」。
"""
    )
