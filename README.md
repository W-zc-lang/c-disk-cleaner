# 🧹 C 盘清理工具 · System Storage Manager

> Windows 磁盘清理工具，界面参考微软电脑管家的「系统存储空间管理」。一键扫描系统垃圾、应用缓存、大文件与重复文件，**安全释放 C 盘空间**。

[![Platform](https://img.shields.io/badge/Platform-Windows-blue)](https://github.com/W-zc-lang/c-disk-cleaner)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Release](https://img.shields.io/github/v/release/W-zc-lang/c-disk-cleaner)](https://github.com/W-zc-lang/c-disk-cleaner/releases)

## ✨ 功能

- **磁盘概览**：各磁盘已用/可用空间，分类彩色容量条
- **深度清理**：系统临时文件、回收站、浏览器缓存、更新缓存与 WinSxS、常见应用缓存
- **大文件扫描**：按磁盘扫描，阈值可自定义（默认 1GB）
- **重复文件**：按大小 + MD5 分组，逐条勾选删除

## 🚀 下载

👉 **[GitHub Releases 下载](https://github.com/W-zc-lang/c-disk-cleaner/releases)**

## ☕ 支持

点个 **Star** ⭐ 支持作者。

---


## 功能 / Features

- **磁盘概览**：显示各磁盘已用/可用空间，按用户文件、应用文件、系统文件、回收站、其他分类展示彩色容量条。
- **深度清理**：扫描系统临时文件、回收站、浏览器缓存（Edge/Chrome）、更新缓存与 WinSxS、常见应用缓存（豆包/微信/抖音/夸克/QQ/钉钉/网易云音乐/有道/Edge/Python 等）。
- **大文件扫描**：支持按磁盘扫描，阈值可自定义（默认 1 GB），逐条勾选删除。
- **重复文件扫描**：支持按磁盘或所有磁盘扫描，按文件大小 + MD5 分组，保留 1 份、其余删除到回收站。
- **快速加速**：一键清理临时文件、刷新 DNS、整理空闲任务，并显示内存占用。
- **删除安全**：所有文件删除统一进入回收站（可恢复）；WinSxS 仅通过系统 DISM 维护（需管理员）。

## 下载 / Download

从 [GitHub Releases](../../releases) 下载 `CDiskCleaner.exe`，双击即可运行，无需安装 Python。

## 使用 / Usage

1. 双击 `CDiskCleaner.exe` 启动。
2. 在「存储」页查看 C 盘空间分布。
3. 点击「深度清理」→「扫描」→ 勾选项目 →「清理所选」。
4. 「大文件」/「重复文件」可选择目标磁盘后扫描并清理。

建议以管理员身份运行，以便清理 WinSxS 等系统项。

## 赞赏支持 / Donate

如果你觉得这个工具对你有帮助，欢迎赞赏支持。

![赞赏码](src/gui/assets/donate.png)

## 开发 / Development

```bash
# 创建虚拟环境并安装依赖
python -m venv venv
venv\Scripts\pip install -r requirements.txt

# 生成图标（需要 Pillow）
venv\Scripts\python generate_icon.py

# 运行
venv\Scripts\python src\main.py

# 打包
build.cmd
```

## 技术栈 / Stack

- Python 3.13
- pywebview 6.x
- PyInstaller 6.x

## 注意 / Notes

- 本工具仅清理白名单目录，不会自动删除 Desktop/Downloads/Documents 等个人目录中的文件。
- 大文件与重复文件删除前需要逐条确认。

## License

MIT
