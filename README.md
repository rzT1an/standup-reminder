# 🧑‍💻 站立提醒器 · Stand Up Reminder

> 带桌面桌宠的久坐提醒工具。坐满 30 分钟弹窗响铃，直到你真的站起来。  
> 人离开自动冻计时——不会把上厕所算成久坐。

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey)]()

---

## ✨ 功能

| 模块 | 说明 |
|------|------|
| 🧑 **桌面桌宠** | 右下角小人 7 种姿势切换（放松 / 慵懒 / 翘腿 / 趴桌 / 挺直 / 起身 / 伸展） |
| 👆 **戳宠互动** | 双击桌宠触发坐姿提醒 + 播放互动音效 |
| ⏰ **久坐提醒** | 活跃坐满 30 分钟弹窗，铃声循环播放直到点击按钮 |
| 🔔 **Qt 弹窗** | PySide6 圆角气泡，带淡入动画，从桌宠嘴边引出 |
| 💤 **空闲检测** | 监控键盘/鼠标/触屏，人走开自动冻结久坐计时 |
| 🔒 **锁屏识别** | 检测 Win+L 锁屏状态，锁屏直接清零 |
| 👀 **坐姿提醒** | 每 5 分钟桌宠冒泡提示「腰挺直一点」 |
| ⚡ **强制休息** | 连续点两次「稍后」后，全屏遮罩 60 秒倒计时，不可跳过 |
| 🎨 **交叉淡入淡出** | 精灵切换 5 帧渐变过渡，非硬切 |
| 🎵 **铃声切换** | 托盘右键 → 铃声菜单，可试听、切换、打开文件夹 |
| 🔊 **音效切换** | 托盘右键 → 互动音效菜单，戳宠音效也可替换 |
| 🖥 **高清适配** | Per-monitor DPI aware，4K 屏不模糊 |
| 📌 **开机自启** | 启动文件夹快捷方式，开机自动运行 |

### 空闲检测策略

```
⌨🖱 有操作 (<2 分钟)  → 正常累计
🚻 短暂离开 (2~15 分) → 冻结计时
🍱 长期离开 (>15 分)  → 清零重算
🔒 锁屏 Win+L         → 立即清零 + 重置 snooze
```

> 覆盖键盘、鼠标移动/点击/滚轮、触屏输入。

---

## 🚀 开始使用

**环境：** Windows 10/11 + Python 3.10+

```powershell
pip install -r requirements.txt
pythonw.exe standup_reminder.py
```

或直接**双击 `启动.vbs`**。系统托盘出现图标、右下角出现桌宠即运行成功。

---

## ⚙️ 配置

编辑 `standup_reminder.py` 顶部：

```python
INTERVAL_MINUTES = 30            # 站立提醒间隔（分）
SNOOZE_MINUTES   = 5             # 稍后延迟（分）
POSTURE_REMINDER_MINUTES = 5     # 坐姿提醒间隔（分）
MAX_SNOOZE_COUNT = 2             # 最大稍后次数
FORCED_REST_AFTER_SNOOZES = 2    # 几次 snooze 后强制休息
FORCED_REST_SECONDS = 60         # 强制休息倒计时（秒）
IDLE_PAUSE_MINUTES = 2           # 空闲冻结阈值（分）
IDLE_RESET_MINUTES = 15          # 空闲清零阈值（分）
ENABLE_RED_HEAT = False          # 红温表情（预留代码，待精灵绘制完开启）
USE_QT_POPUP = True              # Qt 弹窗（关掉则用 Tkinter 回退）
```

---

## 📁 项目结构

```
standing_up_reminding/
├── standup_reminder.py          # 主程序
├── qt_popup_demo.py             # Qt 弹窗模块
├── 启动.vbs                      # 双击启动脚本（无窗口）
├── requirements.txt
├── README.md
└── assets/
    ├── pet/                     # 桌宠精灵图
    │   ├── sit_relaxed.png
    │   ├── sit_lean_bad.png
    │   ├── sit_cross_leg.png
    │   ├── sit_upright.png
    │   ├── stand_getting_up.png
    │   ├── stand_stretch_up.png
    │   └── stand_neck.png
    ├── sounds/                  # 铃声库
    └── effects/                 # 戳宠互动音效库
```

---

## 🔧 技术栈

| 层 | 技术 |
|----|------|
| 桌宠渲染 | PIL (Pillow) 程序化 + PNG 精灵图 |
| 系统托盘 | pystray |
| 主力弹窗 | PySide6 Qt — 圆角气泡 + 属性动画 |
| 回退弹窗 | Tkinter Canvas |
| 空闲检测 | Win32 `GetLastInputInfo` |
| 铃声/音效 | `winsound` 异步循环 |
| DPI 适配 | Win32 DPI Awareness API |

---

## 🗺 路线图

### 🎨 短期 — 打磨当前版本
- [ ] 打包 `.exe`，免安装即用
- [ ] 久坐数据统计面板
- [ ] 番茄钟模式（25 分钟专注 + 5 分钟休息）
- [ ] 红温精灵逐级绘制（替换滤镜叠加）

### 🦴 中期 — 3D 骨架建模
- [ ] Blender / Spine 3D 骨架小人
- [ ] 骨骼动画替换 PNG 精灵图
- [ ] 连续动作过渡（起身、坐下、伸懒腰）
- [ ] 表情系统（高兴、犯困、生气、鼓励）

### 📱 远期 — 跨设备 & 硬件联动
- [ ] iOS / Android App，推送同步手机
- [ ] 手表 / 手环联动（Apple Watch、小米手环）
- [ ] 久坐数据云端同步
- [ ] 智能硬件：升降桌、座椅震动提醒

---

## 👤 作者

**[@rzT1an](https://github.com/rzT1an)** — 第一个正式项目，持续维护中。欢迎 Star ⭐ 和 PR。

---

MIT License
