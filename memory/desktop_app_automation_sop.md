# Desktop App Automation SOP

## Struct Header
- Reader: GA 总控
- When to read: 需要操作没有 API/CLI 的桌面软件时
- Trigger: 用户要求对桌面应用执行自动化操作（如微信发消息、WPS填表、金融软件录入等）
- Inputs: 目标应用名称、操作步骤描述、输入数据
- Outputs: 操作完成确认（截图/OCR验证）
- Tools: ljqCtrl(键鼠), ocr_utils/vision(定位验证), pyperclip(文本输入)
- Side effects: 窗口激活、键鼠操作、剪贴板修改
- Risk: R2(不可逆操作如发送消息) / R1(可撤销操作)
- Failure path: 窗口未找到→重新搜索进程；元素未定位→截图+OCR重试；操作后验证失败→回滚/重做
- Review: 不可逆操作前必须展示计划给用户确认

## 核心流程: 激活 → 定位 → 验证 → 操作 → 验证

### Step 1: 激活窗口
```
ljqCtrl.gw('窗口标题关键字')  # 激活目标窗口
sleep(0.5)  # 等待窗口前台化
```

### Step 2: 定位元素
- 优先用 OCR 找文字元素的坐标
- OCR 找不到时用固定坐标（需记录分辨率/DPI）
- 复杂 UI 用 vision 截图 + 多模态定位

### Step 3: 验证元素
- 操作前截图确认当前界面状态符合预期
- 防止弹窗/加载中状态干扰

### Step 4: 执行操作
- 点击: `ljqCtrl.Click(x, y)`
- 输入文本: 三击选中 → `pyperclip.copy(text)` → `ljqCtrl.Press('ctrl+v')`
- 快捷键: `ljqCtrl.Press('ctrl+s')` 等

### Step 5: 验证结果
- 操作后截图 + OCR 确认状态变化
- 失败时重试（最多 2 次）

## 关键约束

1. **禁 pyautogui**: 一律用 ljqCtrl，用物理坐标
2. **操作前先 gw 激活**: 禁止对后台窗口发送键鼠事件
3. **DPI 感知**: 坐标必须统一为物理像素，注意缩放系数
4. **每步等待**: 操作间加 300-500ms 延迟，等待 UI 响应
5. **人在回路**: 不可逆操作（发送消息/提交表单）前必须征得用户确认

## 常见坑

- 微信窗口标题是动态的（显示当前聊天对象）→ 用类名而非窗口标题定位
- 剪贴板被其他程序抢占 → copy 和 paste 之间不要插入其他操作
- 输入法拦截快捷键 → 先切换到英文输入法
- 高 DPI 屏幕坐标偏移 → 始终用 ljqCtrl.dpi_scale 校准
