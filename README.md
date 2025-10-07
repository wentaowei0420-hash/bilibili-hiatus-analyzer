<div align="center">

# 🎯 B站催更分析器

**Bilibili Hiatus Analyzer**

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.7+-blue.svg" alt="Python Version">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License">
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg" alt="Platform">
</p>

<p align="center">
  <strong>自动分析你关注的B站UP主更新情况，找出最久没更新的"鸽王"</strong>
</p>

<p align="center">
  <a href="#-快速开始">快速开始</a> •
  <a href="#-功能特性">功能特性</a> •
  <a href="docs/USAGE_GUIDE.md">使用教程</a> •
  <a href="docs/FAQ.md">常见问题</a> •
  <a href="docs/CONTRIBUTING.md">贡献指南</a>
</p>

---

</div>

## 📖 项目简介

你是否经常感叹："这个UP主怎么又鸽了？"

**B站催更分析器**能够自动获取你在B站关注的所有UP主，分析他们的视频更新频率，并生成一份详细的"鸽王排行榜"。让你一目了然地知道哪些UP主最久没有更新！

## ✨ 功能特性

- 🔐 **Cookie认证** - 安全可靠的登录方式
- 📊 **智能分析** - 自动分析每位UP主的更新频率
- 🏆 **鸽王排行榜** - 按未更新天数降序排序
- 💾 **数据导出** - 自动生成CSV文件
- 🛡️ **错误处理** - 完善的异常处理机制
- ⏱️ **请求延时** - 遵守君子协议，避免被封禁

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置Cookie

1. 登录 [B站](https://www.bilibili.com)
2. 按 `F12` → `Network` → 刷新页面
3. 点击任意请求 → 找到 `Cookie` → 复制
4. 编辑 `bilibili_hiatus_analyzer.py`，在第26行粘贴你的Cookie

```python
COOKIE = "你的Cookie"
```

> 💡 详细的Cookie获取教程请查看 [使用指南](docs/USAGE_GUIDE.md)

### 3. 运行脚本

```bash
python bilibili_hiatus_analyzer.py
```

### 4. 查看结果

程序会生成 `bilibili_hiatus_ranking.csv` 文件，用Excel打开即可查看完整的鸽王排行榜！

## 📊 数据字段

| 字段 | 说明 |
|------|------|
| UP主姓名 | UP主的昵称 |
| UP主UID | 用户唯一ID |
| 最新视频标题 | 最新发布的视频标题 |
| 发布日期 | 视频发布时间 |
| **未更新天数** | 距今多少天未更新 ⭐ |
| 播放量 | 最新视频的播放次数 |
| 视频链接 | B站视频直达链接 |

## 📚 文档

- 📖 **[完整文档](docs/)** - 所有文档的索引
- 🚀 **[快速开始](docs/QUICK_START.md)** - 5分钟快速上手
- 📝 **[使用教程](docs/USAGE_GUIDE.md)** - 详细的分步教程
- ❓ **[常见问题](docs/FAQ.md)** - 疑难解答
- 🤝 **[贡献指南](docs/CONTRIBUTING.md)** - 如何贡献代码
- 🔒 **[安全策略](docs/SECURITY.md)** - 安全相关信息
- 📝 **[更新日志](docs/CHANGELOG.md)** - 版本更新记录

## 🎬 示例输出

```
============================================================
🎯 B站催更分析器 - 寻找你关注的UP主中的「鸽王」
============================================================

📥 正在获取关注列表...
✅ 成功获取 150 位关注的UP主

🔍 正在分析每位UP主的最新视频...

============================================================
🏆 B站鸽王排行榜 - Top 10
============================================================

第 1 名: 某鸽王UP主
   ⏰ 已鸽 365 天
   📺 最新视频: 我一定会更新的！
   📅 发布日期: 2024-01-01 12:00:00
   👁️  播放量: 123,456
   🔗 链接: https://www.bilibili.com/video/BVxxxxxxxxx

...

✅ 排行榜已保存到文件: bilibili_hiatus_ranking.csv
```

## 📁 项目结构

```
bilibili-hiatus-analyzer/
├── bilibili_hiatus_analyzer.py    # 主程序
├── requirements.txt                # 依赖列表
├── setup.py                        # 安装配置
├── LICENSE                         # MIT协议
├── README.md                       # 本文件
├── .gitignore                      # Git忽略配置
├── .editorconfig                   # 编辑器配置
│
├── docs/                           # 📚 文档目录
│   ├── README.md                   # 文档索引
│   ├── QUICK_START.md             # 快速开始
│   ├── USAGE_GUIDE.md             # 使用教程
│   ├── FAQ.md                     # 常见问题
│   ├── CONTRIBUTING.md            # 贡献指南
│   └── ...                        # 更多文档
│
├── examples/                       # 📝 示例文件
│   ├── config_example.txt         # Cookie配置示例
│   └── example_output.csv         # 输出示例
│
└── .github/                        # GitHub配置
    ├── workflows/                 # GitHub Actions
    └── ISSUE_TEMPLATE/           # Issue模板
```

## ⚠️ 注意事项

- 🔒 **Cookie安全** - 请勿泄露你的Cookie给他人
- ⏱️ **请求延时** - 脚本已设置延时，请勿修改过小
- 📝 **个人使用** - 仅用于分析自己的关注列表
- 🤝 **遵守协议** - 遵守B站用户协议，不要滥用

## ❓ 常见问题

<details>
<summary><b>Q: 为什么需要Cookie？</b></summary>

**A**: B站的关注列表API需要登录认证。Cookie包含你的登录凭证，让脚本能以你的身份访问API。
</details>

<details>
<summary><b>Q: 提示"412错误"怎么办？</b></summary>

**A**: 这是B站的反爬虫机制。请等待15-30分钟后重试。脚本已经设置了延时和随机化，正常情况下不会触发。
</details>

<details>
<summary><b>Q: 脚本运行很慢是正常的吗？</b></summary>

**A**: 是的。为了避免被封禁，脚本会在每次请求间延时3-4秒。如果关注了200位UP主，大约需要10-15分钟。
</details>

更多问题请查看 **[完整FAQ](docs/FAQ.md)**

## 🤝 贡献

欢迎贡献！请查看 [贡献指南](docs/CONTRIBUTING.md) 了解如何参与项目。

## 📄 开源协议

本项目基于 [MIT License](LICENSE) 开源。

## 🌟 支持项目

如果这个项目对你有帮助：

- ⭐ 给项目点个Star
- 🐛 报告Bug或提出建议
- 🔀 Fork并贡献代码
- 📢 分享给更多人

## 📞 联系方式

- **问题反馈**: [GitHub Issues](https://github.com/DAILtech/bilibili-hiatus-analyzer/issues)
- **讨论交流**: [GitHub Discussions](https://github.com/DAILtech/bilibili-hiatus-analyzer/discussions)

---

<div align="center">

**Made with ❤️ by Python developers**

如果这个项目帮你成功催更了你的爱豆UP主，别忘了给个Star！⭐

[English Documentation](docs/README_EN.md) | [详细说明](docs/README_bilibili.md)

</div>
