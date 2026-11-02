# GeekIME 极客输入法

基于 [中州韵 / 小狼毫（Weasel）](https://github.com/rime/weasel) 深度定制的 Windows 输入法方案，自带 **托盘管家**、**Web 管理台** 和 **AI 剪贴板问答**，把 Rime 强大的引擎装进一套开箱即用、可视化管理的外壳里。

## 功能特性

### 输入方案（Rime 配置层）
- **搜狗双拼 / 微软双拼 / 小鹤双拼 / 自然码** 等多套方案，可自创、复制、改键位
- **英文直输模式**：`e` + 字母直接出英文候选（主词典 + 用户词前缀补全），选过的词自动记忆加权（`lua/english_mode.lua`）
- **英文自动记忆**：直接打一串字母按回车上屏，长度达到阈值（默认 6 个字母，Web 可调）即自动记入用户词库；记过的词在普通双拼输入时直接出英文候选（搜狗式回显），重打同一串字母即可再次上屏
- **长短语候选缩略**：自定义短语过长时候选栏只显示前 N 个字符 + …（默认 8 字，Web 可调，0 = 不缩略；`lua/phrase_abbr.lua`），上屏仍是完整内容
- **标点直达**：顿号、分号等中文标点直接上屏，不弹选择菜单（`lua/punct_direct.lua`）
- **v 模式**：`v` 开头的临时英文/数字直通（`lua/v_mode.lua`）
- 自定义短语：词组 + 编码 + 频次，双拼下直接上屏
- 内置搜狗词库转换（`dedup_sogou.py`）

### 极客管家（GeekTray.exe，纯 C# WinForms 单文件）
- 常驻托盘，右键菜单承载全部管理功能，无需命令行
- **AI 剪贴板问答**：开启开关后，复制任意内容 → 自动调用 OpenAI 兼容 API 提问 → 答案写回剪贴板 → 气泡通知；问答留档可回看
- 快速添加自定义短语（弹出小窗，随填随生效）
- 重新部署（快速）/ 深度重建（全量编译）/ 重启输入法服务
- 开机自动启动：管家启动即静默拉起输入法服务与 Web 管理台（`pythonw` 无窗口，无黑框）

### Web 管理台（Flask，默认 http://127.0.0.1:8765）
- 端口被占用时自动顺延（8766、8767…），实际端口写入 `geek_web/web_port.txt`，托盘管家按该文件打开页面；也可用环境变量 `GEEK_WEB_PORT` 指定起始端口
- 自定义短语增删改查、批量导入导出
- 双拼方案管理：启用开关、自创方案、复制方案、键位详情编辑
- **英文·设置页**：英文自动记忆阈值、长短语候选缩略长度在线调节（Lua 侧 mtime 节流探测，改完无需部署即生效）；英文记忆词库搜索、增删、清空
- 词库导入导出（Rime `.dict.yaml` 格式）
- **AI 助手页**：API 地址 / Key / 模型 / 系统提示词配置，一键测试连通，问答历史
- 一键重新部署；手机连同一局域网亦可访问（需自行放开监听地址）

## 目录结构

```
├── double_pinyin*.schema.yaml   # 各双拼方案（含英文/标点/v模式/短语缩略模块挂载）
├── punctuation.yaml             # 标点直达配置
├── english.dict.yaml            # 基础英文词典
├── sogou_lexicon.dict.yaml      # 搜狗词库（示例）
├── custom_phrase.txt            # 自定义短语（本地文件，不入库）
├── geek_settings.txt            # 共享设置（Web 写 / Lua 读，本地运行时文件，不入库）
├── english_user.txt             # 英文记忆词库（本地运行时文件，不入库）
├── rime.lua / lua/              # Rime Lua 模块（英文模式、v模式、标点直达、短语缩略、共享设置）
├── geek_web/
│   ├── app.py                   # Flask Web 管理台
│   ├── GeekTray.cs              # 托盘管家 C# 源码（单文件）
│   ├── build_tray.ps1           # 管家编译+重启脚本
│   └── templates/index.html     # 管理台前端
├── do_build.ps1                 # 全量部署脚本（直调 rime.dll，可看编译通知）
└── geek_*.bat                   # 安装/启动/停止/卸载快捷脚本
```

## 安装与使用

前提：已安装 [小狼毫 Weasel 0.17.4+](https://github.com/rime/weasel/releases)（本方案在其用户目录上扩展）。

1. 将仓库内容放入 Rime 用户目录（小狼毫默认 ` %APPDATA%\Rime`，或「输入法设定 → 用户文件夹」查看实际路径）
2. 全量部署：`powershell -ExecutionPolicy Bypass -File do_build.ps1`（或在托盘菜单点「深度重建」）
3. 编译托盘管家：`powershell -ExecutionPolicy Bypass -File geek_web\build_tray.ps1`（用系统自带 csc，无需额外 SDK）
4. 双击 `geek_install.bat` 注册开机自启（输入法服务 + 管家）

### 英文记忆与短语缩略

- **记单词**：打一串字母（≥ 阈值，默认 6 个）直接按回车上屏，即自动记住；也可在管理台「英文·设置」手动添加
- **用单词**：再次打同一串字母，候选里直接出现（标「英文」）；或 `e` + 单词前缀，从主词典与用户词库补全
- **调阈值**：管理台「英文·设置」改「英文自动记忆最短长度」（2–30），约 5 秒内生效
- **短语缩略**：管理台「英文·设置」改「候选短语缩略显示长度」（0–50 字），过长短语候选只显示开头 + …，上屏完整

### AI 剪贴板问答配置

打开 Web 管理台 → 「AI 助手」页 → 填写任意 **OpenAI 兼容** 中转站地址、API Key、模型名 → 「测试连通」→ 保存 → 勾选开启监听。之后复制任何内容即自动问答，答案直接写回剪贴板。配置存于本地 `ai_config.json`（已被 .gitignore 排除，不会上传）。

## 环境兼容说明

- Windows 10/11 + 小狼毫 Weasel 0.17.4
- 管家与部署脚本仅依赖系统自带组件（PowerShell 5.1、.NET Framework 4 csc）
- Web 管理台依赖 Python 3.8+ 与 Flask（`pip install flask`）

## License

GPL-3.0（本项目基于 GPLv3 的 [rime/weasel](https://github.com/rime/weasel) 生态定制）
