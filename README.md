# 🧑‍💻 站立提醒器 · Stand Up Reminder

> 一个带桌面桌宠的久坐提醒工具。每 30 分钟弹窗，循环响铃，直到你真的站起来。  
> 人离开自动冻计时，不会把上厕所算成久坐。

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey)]()

---

## ✨ 功能

| 模块 | 说明 |
|------|------|
| 🧑 **桌面桌宠** | 右下角小人动态切换姿势：放松 / 慵懒 / 翘腿 / 趴桌 / 挺直 / 站立 |
| ⏰ **久坐提醒** | 活跃坐满 30 分钟弹窗，铃声反复播直到你点按钮 |
| 🔔 **Qt 弹窗** | PySide6 圆角气泡，带淡入动画，从桌宠嘴边引出 |
| 💤 **空闲检测** | `GetLastInputInfo` 监控键鼠触屏，人走自动冻结计时 |
| 🔒 **锁屏识别** | 检测 Win+L 锁屏，锁屏立即清零 |
| 😡 **红温系统** | 连续点「稍后」小人越来越红，超限锁死 |
| 👀 **坐姿提醒** | 每 5 分钟桌宠冒泡「腰挺直一点」 |
| ⚡ **强制休息** | 多次 snooze 后全屏遮罩 60 秒，不可跳过 |
| 🎨 **交叉淡入淡出** | 精灵切换 5 帧 `Image.blend` 过渡，无硬切 |
| 🎵 **多铃声** | 把 `.wav` 放 `assets/sounds/` 就能在托盘菜单切 |
| 🖥 **高清屏** | Per-monitor DPI aware，4K 不糊 |
| 📌 **开机自启** | 启动文件夹快捷方式，开机即运行 |

### 空闲检测策略

```
⌨🖱 有操作 (<2 分钟)  → 正常累计
🚻 短暂离开 (2~15 分) → 冻结计时
🍱 长期离开 (>15 分)  → 清零重算
🔒 锁屏 Win+L         → 立即清零
```

> 覆盖键盘、鼠标移动/点击/滚轮、触屏——与 Teams / Slack 的「离开」标准一致。

---

## 🚀 开始使用

**环境：** Windows 10/11 + Python 3.10+

```powershell
# 安装依赖
pip install -r requirements.txt

# 启动（无窗口）
pythonw.exe standup_reminder.py
```

或直接**双击 `启动.vbs`**。

托盘出现绿色图标，右下角出现桌宠小人，即运行成功。

---

## ⚙️ 配置

编辑 `standup_reminder.py` 前几行即可：

```python
INTERVAL_MINUTES = 30           # 站立提醒间隔
SNOOZE_MINUTES   = 5            # 稍后延迟
POSTURE_REMINDER_MINUTES = 5    # 坐姿提醒间隔
MAX_SNOOZE_COUNT = 2            # 最大稍后次数
FORCED_REST_SECONDS = 60        # 强制休息倒计时
IDLE_PAUSE_MINUTES = 2          # 空闲冻结阈值
IDLE_RESET_MINUTES = 15         # 空闲清零阈值
```

---

## 📁 结构

```
standing_up_reminding/
├── standup_reminder.py          # 主程序
├── qt_popup_demo.py             # Qt 弹窗模块
├── 启动.vbs                      # 开机 / 双击启动
├── requirements.txt
├── README.md
├── .gitignore
└── assets/
    ├── pet/                     # 桌宠精灵 PNG
    │   ├── sit_relaxed.png
    │   ├── sit_lean_bad.png
    │   ├── sit_cross_leg.png
    │   ├── sit_upright.png
    │   ├── stand_getting_up.png
    │   ├── stand_stretch_up.png
    │   └── stand_neck.png
    ├── sounds/                  # 铃声 WAV
    └── effects/                 # 戳宠音效 WAV
```

---

## 🔧 技术栈

| 层 | 技术 |
|----|------|
| 桌宠绘制 | PIL (Pillow) 程序化 + 精灵图 |
| 系统托盘 | pystray |
| 主力弹窗 | PySide6 Qt — 圆角气泡 + 属性动画 |
| 回退弹窗 | Tkinter Canvas |
| 空闲检测 | Win32 `GetLastInputInfo` |
| 铃声 | `winsound` 异步循环 |
| 高清适配 | Win32 DPI Awareness API |

---

## 🗺 后续计划

- [ ] 桌宠换连贯动画 / 视频 / Spine
- [ ] 红温 PNG 逐级绘制（已支持 `{name}_l{1-5}.png` 命名自动加载）
- [ ] 久坐数据统计
- [ ] 番茄钟模式
- [ ] 自定义文案
- [ ] 打包 `.exe`

---

## 👤 作者

**[@rzT1an](https://github.com/rzT1an)** — 第一个正式项目，认真维护，欢迎 Star ⭐ 和 PR。

---

MIT License
