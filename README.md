# Epic Notify Bot

一个基于 Python 的 Telegram 机器人，自动推送 Epic Games Store 每周限时免费游戏信息。

## 功能简介
- 自动定时检查 Epic 商店限免游戏，并推送给所有订阅用户
- 支持 /start 订阅、/stop 取消订阅、/checknow 手动查询当前限免
- 支持多用户订阅，自动管理订阅者列表
- 推送内容包含游戏标题、原价、限免时间、领取直达链接及封面图片
- 支持自定义时区、推送时间、地区和语言

## 快速开始

### 1. 克隆项目
```bash
git clone <本项目地址>
cd epic_notify
```

### 2. 安装依赖
建议使用 Python 3.8 及以上版本。
```bash
pip install -r requirements.txt
```

如无 requirements.txt，可手动安装主要依赖：
```bash
pip install python-telegram-bot requests python-dotenv pytz
```

### 3. 配置 .env 文件
复制 `.env` 文件模板，填写你的 Telegram Bot Token：
```
TELEGRAM_BOT_TOKEN=你的BotToken
TARGET_TIMEZONE=Asia/Shanghai  # 可自定义
CHECK_HOUR=23                 # 每天推送小时
CHECK_MINUTE=15               # 每天推送分钟
EPIC_LOCALE=zh-CN             # Epic商店语言
EPIC_COUNTRY=CN               # Epic商店国家
```

### 4. 启动机器人
```bash
python epic.py
```

## 使用说明
- 在 Telegram 中与 Bot 对话，发送 /start 订阅推送
- 发送 /stop 取消订阅
- 发送 /checknow 可手动查询当前限免游戏

## 文件说明
- `epic.py`         主程序，包含所有逻辑
- `sent_games.json` 已推送过的游戏 slug 列表
- `subscribers.json`订阅用户列表
- `.env`            配置文件

## 常见问题
- **Bot 无法推送消息？**
  - 检查 Bot Token 是否正确，Bot 是否已加入群组/频道并被授权。
- **定时推送不准？**
  - 检查服务器时区，或调整 .env 中的 `TARGET_TIMEZONE`。
- **API 获取失败？**
  - 可能是 Epic API 变动或网络问题，可稍后重试。

## 代码生成
本项目中的大部分代码由 Google 的大型语言模型 **Gemini Pro** 生成。

## 致谢
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- [Epic Games Store](https://store.epicgames.com/)

---
如有问题或建议，欢迎 Issue 或 PR！
