# Web 工具链初始化执行 SOP

## Struct Header
- Reader: GA 总控
- When to read: 首次使用 web_scan / web_execute_js 之前
- Trigger: 需要建立浏览器交互能力，web_scan 返回不可用
- Inputs: 系统级权限（code_run 可用）
- Outputs: web_scan / web_execute_js 测试通过
- Tools: code_run, web_scan, web_execute_js
- Side effects: 安装 Chrome 扩展（tmwd_cdp_bridge）
- Risk: R1（仅影响本地浏览器扩展）
- Schedule: 1.检测浏览器 → 2.安装扩展 → 3.验证 web_scan → 4.验证 web_execute_js
- Failure path: 若验证失败，用 ask_user 请求用户协助
- Review: 无需独立审查


> 模型选择看 `model_dispatch_sop`；工具/编程器选择看 `tool_dispatch_sop`

若 web_scan 和 web_execute_js 已测试可用，无需执行此 SOP。
仅供初始安装时，code_run 可用但 web 工具尚未配置的场景。

## 目标
在仅具备系统级权限（code_run）时，建立 Web 交互能力（web_scan / web_execute_js）。

## 前置：检测浏览器
用 `code_run` 执行 `where chrome` 确认浏览器安装路径。
**确认点：** 若未检测到浏览器，用 `ask_user` 询问用户安装路径。

## 安装 tmwd_cdp_bridge 扩展
扩展路径: `../assets/tmwd_cdp_bridge/`（MV3 Chrome 扩展，含 CDP debugger + scripting + cookie 能力）

### 自动打开扩展管理页
`chrome://extensions` 无法通过命令行或 JS 打开，需用剪贴板+地址栏方案

### 安装步骤（chrome扩展页难以自动化）
1. 打开扩展管理页，开启「开发者模式」
2. 点击「加载已解压的扩展程序」，选择 `assets/tmwd_cdp_bridge/` 目录，或让用户直接拖入
3. 显示“错误”不用管，一般只是因为还没连上GA

## 验证
**gate：** web_scan 返回有效标签页后，再执行 `web_execute_js` 测试。
⚠ web_scan 显示「没有可用标签页」不一定是扩展没装好，可能是浏览器未打开或只有 blank 页。
此时禁止乱试，先用 `start "" "https://www.baidu.com"` 打开一个正常页面，再 `web_scan` 确认。
若仍不可用，无法自动探测默认浏览器是哪个、插件装在了哪个浏览器、或是否已安装——此时请求用户协助。

## 相关资源
- 模型分发：`memory/model_dispatch_sop.md`
- 工具分发：`memory/tool_dispatch_sop.md`
- 浏览器驱动：`memory/tmwebdriver_sop.md`
- 扩展目录：`../assets/tmwd_cdp_bridge/`
