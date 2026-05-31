<p align="center">
  <br>
  <picture>
    <source media="(prefers-color-scheme: dark)">
    <img width="120" alt="Stand Up Reminder" src="https://img.icons8.com/fluency/240/person-standing.png">
  </picture>
  <h1 align="center">Stand Up Reminder</h1>
  <p align="center">
    久坐提醒器 &nbsp;·&nbsp; 你的桌面健康伙伴
    <br>
    <sub>每 30 分钟提醒你站起来 — 人到、计时、提醒、休息。</sub>
  </p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10_%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Platform-Windows_10_%2F_11-0078D6?style=flat-square&logo=windows&logoColor=white" alt="Windows">
  <img src="https://img.shields.io/badge/License-MIT-success?style=flat-square" alt="License">
  <img src="https://img.shields.io/badge/Status-Active-brightgreen?style=flat-square" alt="Status">
</p>

<br>

---

## 💡 它能做什么

你坐在电脑前写代码、看文档、打游戏……不知不觉三四个小时就过去了。  
**Stand Up Reminder** 会在你坐满 30 分钟后弹窗 + 响铃，直到你真正站起来才停。  
如果你只是走开上厕所，它不会算成久坐——我们检测的是**你真的坐在电脑前操作**的时间。

| | |
|---|---|
| 🧑 **桌面宠物** | 右下角 7 种姿势的小人，双击它可以随时触发坐姿提醒 |
| ⏱ **久坐提醒** | 活跃操作满 30 分钟 → 弹出圆角气泡窗口 + 循环铃声 |
| 🔔 **动画弹窗** | Qt 驱动，淡入动画，从宠物嘴边引出，像它在对你说话 |
| 💤 **智能计时** | 人离开自动冻结计时器，回来继续累计，不会把休息算成久坐 |
| 👀 **坐姿提醒** | 每 5 分钟提示「腰挺直一点」 |
| ⚡ **强制休息** | 连续「稍后」两次后进入 60 秒全屏强制休息 |
| 🎵 **自定义铃声** | 放入 `.wav` 文件即可在托盘菜单切换，音效同理 |
| 📌 **开机自启** | 一次配置，每次开机自动运行 |

### ⌨ 空闲是怎么检测的？

| 你在干什么 | 系统判定 | 结果 |
|-----------|---------|------|
| 敲键盘、动鼠标、滑触屏 | **活跃** | 正常累计久坐时间 |
| 倒水、上厕所（2~15 分钟） | **短暂离开** | 冻结计时器，回来继续 |
| 吃饭、开会（>15 分钟） | **长时间离开** | 清零，回来重新算 |
| 按了 `Win + L` 锁屏 | **锁屏** | 立即清零 |

> 和 Slack / Teams 判断你「离开」用的是**同一个 Windows API**。

---

## ⚠️ Mac 能用吗？

**当前版本仅支持 Windows 10 / 11。**

Mac 版需要替换几个 Windows 专属组件（`winsound` → `afplay`、`GetLastInputInfo` → `CGEventSource`、`winotify` → 原生通知）。已在路线图中，预计在跨设备阶段适配。

> 如果你是 Mac 用户并且想提前用上，欢迎提 PR 👏

---

## 🚀 给小白看的安装指南

**适用于：从未用过命令行的 Windows 用户。**

### 第 1 步：安装 Python

1. 打开浏览器，访问 [python.org](https://www.python.org/downloads/)
2. 点击黄色大按钮 **Download Python 3.x.x**
3. 运行下载的文件
4. ⚠️ **关键一步**：勾选底部的 **「Add Python to PATH」**，再点 Install Now
5. 等安装完成，点 Close

验证安装：按 `Win + R`，输入 `cmd` 回车，在黑色窗口里输入：

```cmd
python --version
```

如果显示 `Python 3.10.x` 或更高版本，说明装好了。

### 第 2 步：下载本项目

点击页面上方绿色的 **Code ▾** → **Download ZIP**，解压到 `D:\standing_up_reminding`。

### 第 3 步：安装依赖

按 `Win + R`，输入 `cmd` 回车，在黑色窗口里**依次**输入：

```cmd
cd /d D:\standing_up_reminding
pip install -r requirements.txt
```

等它跑完（一分钟左右），没有报红字就成功了。

### 第 4 步：启动

**双击**文件夹里的 `启动.vbs`。

右下角出现一个坐椅子的绿色小人、系统托盘出现图标——启动成功！🎉

### 第 5 步（可选）：设为开机自启

1. 按 `Win + R`，输入 `shell:startup` 回车
2. 在弹出的文件夹里**右键** → **新建** → **快捷方式**
3. 位置填 `D:\standing_up_reminding\启动.vbs`，下一步，完成
4. 下次开机自动运行

---

## ⚙️ 怎么改设置

用记事本打开 `standup_reminder.py`，找到最前面的这段，改数字就行：

```python
INTERVAL_MINUTES = 30            # 多久提醒一次（分钟）
SNOOZE_MINUTES   = 5             # 「稍后」推迟多久（分钟）
POSTURE_REMINDER_MINUTES = 5     # 坐姿提醒间隔（分钟）
MAX_SNOOZE_COUNT = 2             # 最多稍后几次
FORCED_REST_SECONDS = 60         # 强制休息倒计时（秒）
IDLE_PAUSE_MINUTES = 2           # 离开多久暂停计时（分钟）
IDLE_RESET_MINUTES = 15          # 离开多久清零重算（分钟）
```

改完保存，右键托盘 →退出 → 重新双击 `启动.vbs` 生效。

---

## 怎么换铃声和音效

1. 把你喜欢的 `.wav` 文件放进 `assets/sounds/`
2. **右键托盘图标** → 铃声 → 选你刚放进去的
3. 系统会自动试听 2 秒

同理，把 `.wav` 放进 `assets/effects/`，在**互动音效**菜单里切换。

---

## 📁 文件夹说明

```
D:\standing_up_reminding\
│
├── 启动.vbs              ← 双击这个启动程序
├── standup_reminder.py   ← 主程序（用记事本打开可改设置）
├── qt_popup_demo.py      ← 弹窗模块
├── requirements.txt      ← 依赖清单（给 pip 安装用）
│
└── assets\
    ├── pet\              ← 桌宠的 7 个动作图片
    ├── sounds\           ← 放铃声 .wav 文件的文件夹
    └── effects\          ← 放戳宠音效 .wav 文件的文件夹
```

---

## 🧠 背后的技术

| 做什么 | 用的什么 | 为什么选它 |
|--------|---------|-----------|
| 画桌宠 | PIL (Pillow) | 程序化绘制 + PNG 精灵混用，轻量快速 |
| 弹窗 | PySide6 (Qt) | 圆角气泡 + 淡入动画，比 Tkinter 平滑得多 |
| 托盘图标 | pystray | 轻量，右键菜单灵活 |
| 检测空闲 | Win32 `GetLastInputInfo` | 系统级 API，不耗 CPU，和 Teams/Slack 同款 |
| 播放声音 | `winsound` | Windows 原生异步播放，无需额外库 |
| 高清屏 | Win32 DPI API | 4K 屏不会模糊 |

---

## 🗺 路线图

> 排在前面的先做。

**🟢 短期 — 打磨体验**
- [ ] 打包 `.exe`（双击即用，不用装 Python）
- [ ] 久坐数据面板（今天坐了多久、站起来几次）
- [ ] 番茄钟模式（45 分钟专注 + 5 分钟休息）
- [ ] 红温表情绘制（目前有预留代码，等精灵图就位）

**🟡 中期 — 3D 桌宠**
- [ ] Blender / Spine 3D 骨架小人替换 PNG
- [ ] 连续动作：起身 / 坐下 / 伸懒腰全流程
- [ ] 表情系统：高兴、犯困、生气、给你加油

**🔵 远期 — 跨平台 + 硬件**
- [ ] Mac 版本（替换 Windows 专属组件）
- [ ] iOS & Android App，手机同步推送
- [ ] Apple Watch / 小米手环 震动提醒
- [ ] 智能升降桌联动 & 久坐数据云同步

---

## 👤

**[@rzT1an](https://github.com/rzT1an)**

第一个从头开始做的项目，认真维护。  
如果你觉得有用，点个 Star ⭐ 就是最好的鼓励。  
想参与开发？欢迎 Issue 和 PR。

---

<p align="center">
  <sub>MIT License · Copyright © 2025 rzT1an</sub>
</p>
