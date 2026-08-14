# legacy/ —— 旧项目文件（ThreeDogMemory 时代）

这些是三狗珊瑚的前身 ThreeDogMemory 及其配套文件的存档，仅作参考，**不参与打包**（已在 .gitignore 排除）：

- `assistant_backend.py` —— 旧版 ThreeDogMemory 实现（重构前的样子）
- `index.html` / `启动服务.bat` —— 旧版配套的前端与启动脚本
- `{meta['anchor_snippet']}.txt` —— 早期残留的占位文件
- `chat_history_default.json` / `heat_memory.json` / `cold_memory.json` —— 旧版运行时数据

新版数据统一在 `memory_data/`（coral_* 前缀），旧数据格式与新格式不兼容。
