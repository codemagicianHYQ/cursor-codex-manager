# Cursor / Codex Manager

Windows 本地工具：用网页管理 **Cursor 工作区 / 对话归档与硬删**，以及 **Codex Keep-in-sync** 开关。

> 会读写本机 Cursor / Codex 用户数据。请先退出 Cursor 再做删除类操作。作者不对数据损坏负责——硬删前会做轻量备份，但仍建议自行留底。

## 功能

| 页 | 说明 |
|----|------|
| 工作区 | 清理 Recents / workspaceStorage 幽灵项（不删磁盘源码） |
| 对话 | 按项目归档 / 硬删；可选同时删除 `agent-transcripts` |
| 同步 | Codex「Keep imports in sync」 |
| 标题 | 补缺对话标题 |
| MCP | Cursor ↔ Codex MCP 对照 |

适配较新 Cursor：`composerHeaders` 独立 SQLite 表。

## 环境

- Windows 10/11
- Python 3.10+
- 已安装 Cursor（对话库在 `%APPDATA%\Cursor` 或你自定义的 user-data）

```bash
pip install -r requirements.txt
```

## 启动

| 方式 | 说明 |
|------|------|
| 双击 `open.vbs` | 无黑窗；会重启占用 8501 的旧进程再打开 |
| `open.bat` / `run.bat` | 控制台可见 |
| `python launch.py` | 同上 |
| `python launch.py --reuse` | 若已在跑则只开浏览器 |

浏览器打开：http://127.0.0.1:8501  

可选开机启动：`install_startup.ps1`

## 自定义 Cursor 用户数据目录

默认自动找 `%APPDATA%\Cursor`（含 junction / symlink）。

若你的 profile 不在默认位置，任选其一：

1. 环境变量：`CURSOR_USER_DATA_DIR=D:\path\to\your\user-data`
2. 本地文件（勿提交）：`data/user_data_path.txt` 写一行绝对路径

## 备份

归档 / 硬删 / 清工作区前会写入 `data/backups/`（headers + composerData 等，**不含**巨型 bubble 正文）。  
该目录已在 `.gitignore` 中，**请勿把备份推到公开仓库**。

## 隐私

上传 GitHub 前请确认：

- 不要提交 `data/backups/`、`data/streamlit.log`、本机路径覆盖文件
- 备份里可能含对话标题、工作区路径、加密相关字段

## License

MIT（可按需自行更换）
