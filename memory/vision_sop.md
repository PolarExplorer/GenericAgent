# Vision API SOP

> 模型选择看 `model_dispatch_sop`；工具/编程器选择看 `tool_dispatch_sop`

## ⚠️ 前置规则（必须遵守）

1. **先枚举窗口**：调用 vision 前必须先用 `pygetwindow` 枚举窗口标题，确认目标窗口存在且已激活到前台。窗口不存在就不要截图。
2. **🚫 禁止全屏截图**：必须先利用ljqCtrl截取窗口区域。能截局部（如标题栏）就不截整窗口，能截窗口就绝不全屏。全屏截图在任何场景下都不允许。
3. **能不用 vision 就不用**：如果窗口标题/本地 OCR（`ocr_utils.py`）能获取所需信息，就不要调用 vision API，省 token 且更可靠。Vision 是最后手段。

## 快速用法

```python
from vision_api import ask_vision

result = ask_vision(image, prompt="描述图片内容", backend='minimax')
# image: 文件路径(str/Path) 或 PIL Image
# backend: 'minimax'(默认, mimo-v2.5) | 'openai'(gpt-5.4) | 'modelscope'
# 返回 str：成功为模型回复，失败为 'Error: ...'
```

## 后端与模型

| backend | 模型 | 用途 | Token消耗 |
|---------|------|------|-----------|
| `minimax`（默认） | mimo-v2.5 | 图片理解，中文友好 | 1x |
| `openai` | gpt-5.4 | 备用，英文场景 | — |

> ⚠️ `mimo-v2.5-pro` **不支持**图片输入（纯文本模型），勿使用。
> `mimo-v2-omni` 也支持多模态，需时可在 mykey.py 中切换。

## 已构建完成

`memory/vision_api.py` 已存在且测试通过（2026-04-24）。
