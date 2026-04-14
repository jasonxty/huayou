# 华友钴业 (603799) 用户界面手册

## 系统概览

本系统通过 **4个渠道** 向你推送交易信息，全自动运行，无需手动操作：

| 渠道 | 形式 | 何时触发 | 需要操作吗 |
|------|------|----------|-----------|
| macOS 弹窗 | 阻塞式对话框（必须点确认） | 做T信号触发时 | 点"知道了" |
| macOS 通知 | 右上角横幅（自动消失） | 每15分钟状态播报 | 无 |
| 微信推送 | Server酱消息 | 晨报 + 做T信号 | 无 |
| HTML 网页 | 浏览器自动打开 | 每日晨报 | 无 |

---

## 每日时间线

```
09:15  📰 晨报弹窗
       ├── macOS通知: "¥XX.XX | HOLD (震荡观望) | 风险:LOW"
       ├── 浏览器自动打开 HTML 晨报 (briefs/brief-YYYY-MM-DD.html)
       └── 触发脚本: com.huayou.popup → morning-popup.sh → analyze.py --html

09:20  🚀 实时监控启动
       ├── 微信推送晨报 (Server酱)
       ├── macOS通知: "华友钴业 监控已启动 | 卖区/买区"
       └── 触发脚本: com.huayou.monitor → start-monitor.sh → monitor.py

09:25  📊 盘中实时监控 (每60秒轮询)
 ~     ├── 每15分钟: macOS通知横幅 "¥价格(涨跌%) | 距高抛 X% | 距低吸 X%"
15:00  ├── 做T信号触发: 阻塞式弹窗 (见下方详情)
       └── 微信推送做T信号 (每日最多5次)

15:00  🔔 收盘通知
       └── macOS通知: "今日监控结束 | 已触发: X个信号"
```

---

## 弹窗类型详解

### 1. 做T交易信号（阻塞式弹窗 — 必须点击才消失）

所有做T信号都会弹出 **阻塞式对话框**，跳到屏幕最前面，确保你不会错过。

| 信号 | 图标 | 触发条件 | 弹窗内容示例 |
|------|------|----------|-------------|
| 🔴 高抛第1批 | ℹ️ 蓝色 | 价格 ≥ 卖区下沿 | "¥61.50(+2.35%) 触及高抛第1批<br>卖出 200股 @ ¥61.30" |
| 🔴 高抛第2批 | ℹ️ 蓝色 | 价格 ≥ 卖区上沿 | "¥62.80(+3.10%) 触及高抛第2批<br>卖出 100股 @ ¥62.50" |
| 🟢 低吸触发 | ℹ️ 蓝色 | 价格 ≤ 买区上沿 | "¥58.80(-1.20%) 进入低吸区间<br>买入 200股 @ ¥59.00~58.50" |
| ⛔ 止损警报 | 🛑 红色 | 价格 ≤ 止损价 | "¥56.20(-3.80%) 跌破止损 ¥56.50<br>立即减仓！" |
| ⚡ 放量突破 | ℹ️ 蓝色 | 价格 ≥ 突破价 | "¥64.00(+5.20%) 突破阻力 ¥63.50<br>已卖→不追，持仓→继续持有" |

每个信号类型 **每天只触发一次**，不会重复骚扰。

### 2. 状态播报（通知横幅 — 自动消失）

每 **15分钟** 弹一次右上角通知，告诉你当前价格和距离做T区间的距离：

```
📊 华友钴业 10:30
¥60.50(+1.23%) | 距高抛 1.8% | 距低吸 2.5%
```

这种通知不会挡住你的工作，几秒后自动消失。

### 3. 晨报弹窗（通知横幅 + 浏览器）

每天 9:15 自动弹出晨报摘要 + 在浏览器打开完整 HTML 报告：

```
📈 华友钴业晨报 — 2026-03-22
¥59.91 | HOLD (震荡观望) | 风险:LOW
```

HTML 晨报包含：技术面评分、基本面评分、商品催化剂、新闻情绪、做T建议、回测结果。

---

## 手动命令

### 分析 & 晨报

```bash
# 完整流水线: 拉数据 → 分析 → 回测 → 生成晨报
python analyze.py

# 生成晨报 + 自动打开 HTML
python analyze.py --html

# 推送晨报到微信
python analyze.py --push-brief

# 只拉数据，不分析
python analyze.py --fetch-only

# 查看推荐准确率
python analyze.py --performance

# 查看淘股吧大神观点
python analyze.py --experts
```

### 持仓管理

```bash
# 录入持仓: 1000股，成本65.30元
python analyze.py --set-position 1000 65.3

# 做T完成: 卖出200股@62.5，买回@60.0 → 自动调整成本
python analyze.py --t0-done 62.5 60.0 200
```

### 实时监控

```bash
# 启动监控（带弹窗，60秒轮询，收盘自动退出）
python monitor.py

# 检查一次就退出
python monitor.py --once

# 自定义30秒轮询
python monitor.py --interval 30

# 只要终端+微信，不要弹窗
python monitor.py --no-popup

# 测试弹窗是否正常
python monitor.py --test-popup

# 测试微信推送
python monitor.py --test-push
```

### 回测

```bash
# 运行5个策略回测
python analyze.py --backtest

# 做T策略历史验证
python analyze.py --backtest-t0
```

---

## 自动化服务管理

系统通过 macOS `launchd` 自动运行，无需手动启动。

### 查看状态

```bash
launchctl list | grep huayou
```

正常输出：
```
-   0   com.huayou.monitor    # 实时监控（9:20启动）
-   0   com.huayou.popup      # 晨报弹窗（9:15启动）
```

### 临时关闭

```bash
# 关闭实时监控
launchctl unload ~/Library/LaunchAgents/com.huayou.monitor.plist

# 关闭晨报弹窗
launchctl unload ~/Library/LaunchAgents/com.huayou.popup.plist
```

### 重新启用

```bash
launchctl load ~/Library/LaunchAgents/com.huayou.monitor.plist
launchctl load ~/Library/LaunchAgents/com.huayou.popup.plist
```

### 日志位置

| 服务 | 日志文件 |
|------|---------|
| 晨报弹窗 | `logs/popup-YYYYMMDD.log` |
| 实时监控 | `logs/monitor-YYYYMMDD.log` |

---

## 配置项

### config.yaml

```yaml
notification:
  serverchan_key: "YOUR_SENDKEY_HERE"   # Server酱 SendKey（微信推送）

taoguba:
  enabled: true
  experts:
    - id: "13371078"
      name: "罐头哥勇闯大a"
  max_post_age_days: 3
  request_delay_seconds: 3
```

### config.py 关键参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `MONITOR_INTERVAL` | 60秒 | 行情轮询间隔 |
| `MONITOR_DAILY_PUSH_LIMIT` | 5次 | 每日微信推送上限（Server酱免费版） |
| `MONITOR_STATUS_INTERVAL` | 900秒 (15分钟) | 状态通知横幅间隔 |
| `MONITOR_COOLDOWN` | 1800秒 (30分钟) | 同类信号冷却期 |

---

## 文件结构 (用户相关)

```
huayou-analyst/
├── analyze.py              # 主入口 — 晨报生成
├── monitor.py              # 实时监控 + 弹窗
├── config.yaml             # 个人配置（微信key、专家列表）
├── briefs/                 # HTML 晨报存档
│   └── brief-2026-03-22.html
├── logs/                   # 自动化日志
│   ├── monitor-20260322.log
│   └── popup-20260322.log
├── huayou.db               # SQLite 数据库（行情/指标/持仓/交易记录）
└── scripts/
    ├── start-monitor.sh    # 监控启动脚本
    ├── morning-popup.sh    # 晨报弹窗脚本
    ├── com.huayou.monitor.plist  # launchd: 实时监控
    └── com.huayou.popup.plist    # launchd: 晨报弹窗
```

---

## 故障排查

| 问题 | 检查方法 |
|------|---------|
| 没收到弹窗 | `launchctl list \| grep huayou` — 检查服务是否运行 |
| 弹窗测试 | `python monitor.py --test-popup` |
| 微信没收到 | `python monitor.py --test-push` — 检查 SendKey |
| 监控没启动 | 查看 `logs/monitor-YYYYMMDD.log` |
| 晨报没生成 | 查看 `logs/popup-YYYYMMDD.log` |
| 非交易日不触发 | 正常行为 — 周末和节假日自动跳过 |
| HTML 没打开 | 检查 `briefs/` 目录下是否有当天文件 |

---

## 免责声明

本工具仅供个人研究和学习使用，不构成投资建议。历史回测不代表未来收益。交易前请自行做好尽职调查。
