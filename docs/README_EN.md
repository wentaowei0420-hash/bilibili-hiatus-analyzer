<div align="center">

# 🎯 Bilibili Hiatus Analyzer

**Find the "Pigeon King" among your followed UP creators**

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.7+-blue.svg" alt="Python Version">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License">
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey.svg" alt="Platform">
  <img src="https://img.shields.io/badge/Bilibili-API-ff69b4.svg" alt="Bilibili">
</p>

<p align="center">
  <strong>Automatically analyze update frequency of Bilibili UP creators you follow and generate a "Pigeon King" ranking</strong>
</p>

<p align="center">
  <a href="README.md">简体中文</a> | <a href="README_EN.md">English</a>
</p>

---

</div>

## 📖 Introduction

Ever wonder: "Why hasn't this UP creator updated yet?"

**Bilibili Hiatus Analyzer** is a lightweight Python script that automatically fetches all UP creators you follow on Bilibili, analyzes their video update frequency, and generates a detailed "Pigeon King" ranking. Know at a glance which creators haven't updated in the longest time, making it easier to "remind" them in the comments!

## ✨ Features

- 🔐 **Cookie Authentication** - Uses browser cookies to simulate login, safe and reliable
- 📊 **Smart Analysis** - Automatically fetches following list and analyzes each UP creator's latest videos
- 🏆 **Pigeon King Ranking** - Sorted by days since last update to find the biggest procrastinators
- 💾 **Data Export** - Automatically generates CSV file, Excel-compatible
- 🛡️ **Error Handling** - Comprehensive exception handling, stable operation
- ⏱️ **Rate Limiting** - Request delays to avoid being blocked by Bilibili servers
- 🌐 **Cross-platform** - Supports Windows, Linux, macOS

## 📊 Data Fields

| Field | Description |
|-------|-------------|
| UP Creator Name | Creator's nickname |
| UP Creator UID | Unique user ID (mid) |
| Latest Video Title | Title of most recent video |
| Latest Video Date | When the video was published |
| **Days Since Update** | Days since last video (key metric) ⭐ |
| Latest Video Views | View count of that video |
| Video Link | Direct link to the video |

## 🚀 Quick Start

### Requirements

- Python 3.7 or higher
- pip package manager

### Installation

1. **Clone the repository**

```bash
git clone https://github.com/DAILtech/bilibili-hiatus-analyzer.git
cd bilibili-hiatus-analyzer
```

2. **Install dependencies**

```bash
pip install -r requirements.txt
```

3. **Configure Cookie**

See [Cookie Tutorial](#-cookie-tutorial) below to get your Bilibili cookie and add it to the script.

4. **Run the script**

```bash
python bilibili_hiatus_analyzer.py
```

5. **View results**

The program will generate `bilibili_hiatus_ranking.csv` in the current directory. Open it with Excel or any spreadsheet software.

## 🔑 Cookie Tutorial

### Method 1: Chrome Browser

1. Open [Bilibili](https://www.bilibili.com) and **login** to your account
2. Press `F12` to open Developer Tools
3. Click the **Network** tab at the top
4. Press `F5` to refresh the page
5. Click any request in the list (recommend the first one)
6. Find **Request Headers** on the right
7. Locate the **Cookie** field, right-click → **Copy value**

### Configure in Script

Open `bilibili_hiatus_analyzer.py`, find line 17:

```python
COOKIE = "Paste your Cookie here"
```

Paste the copied Cookie:

```python
COOKIE = "SESSDATA=cb06xxxx...; bili_jct=xxxxx; DedeUserID=123456; ..."
```

> ⚠️ **Security Notice**: Cookie contains your login credentials, do not share with others!

## 📝 Usage

### Basic Usage

```bash
python bilibili_hiatus_analyzer.py
```

### Output Example

The program displays real-time progress:

```
============================================================
🎯 Bilibili Hiatus Analyzer - Finding the "Pigeon King"
============================================================

📥 Fetching following list...
   Fetched 50 UP creators...
   Fetched 100 UP creators...
✅ Successfully fetched 150 followed UP creators

🔍 Analyzing latest videos for each UP creator...

[1/150] Fetching latest video for某某UP主...
   ✅ Last update: 5 days ago
...

============================================================
🏆 Pigeon King Ranking - Top 10
============================================================

Rank 1: Some Pigeon King
   ⏰ Haven't updated for 365 days
   📺 Latest video: I will definitely update!
   📅 Published: 2024-01-01 12:00:00
   👁️  Views: 123,456
   🔗 Link: https://www.bilibili.com/video/BVxxxxxxxxx

============================================================
✅ Ranking saved to file: bilibili_hiatus_ranking.csv
📊 Analyzed 145 UP creators
============================================================
```

## ❓ FAQ

<details>
<summary><b>Q: Why do I need a Cookie?</b></summary>

**A**: Bilibili's following list API requires login authentication. Cookie contains your login credentials, allowing the script to access the API as you.
</details>

<details>
<summary><b>Q: Is my Cookie safe?</b></summary>

**A**: 
- ✅ Script runs completely locally, doesn't upload Cookie
- ✅ Code is open source and transparent, can be audited
- ⚠️ But note: Don't share scripts containing your Cookie
- ⚠️ Don't save Cookie on public computers
</details>

<details>
<summary><b>Q: Why is the script slow?</b></summary>

**A**: Yes, this is normal. To follow "gentleman's agreement" and avoid being blocked, the script delays 1 second between each request. If you follow 200 creators, it takes about 3-4 minutes.
</details>

For more questions, see [FAQ.md](FAQ.md)

## 🔒 Privacy & Security

This project takes your privacy and data security seriously:

- ✅ **Local Execution** - All code runs on your computer
- ✅ **No Data Upload** - Doesn't send any data to third-party servers
- ✅ **Open Source** - All code is publicly auditable
- ✅ **Cookie Security** - Cookie only used for Bilibili API access

## 🤝 Contributing

Contributions of all forms are welcome! Whether reporting bugs, suggesting features, or submitting code.

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## 📜 Disclaimer

This project is for **learning and personal use only**. Please follow these principles:

1. **Follow Bilibili's ToS** - Don't use for commercial purposes or mass data collection
2. **Gentleman's Agreement** - Don't modify request delays to be too frequent
3. **Personal Use** - Only for analyzing your own following list
4. **Data Security** - Cookie contains sensitive information, keep it safe

## 📄 License

This project is open source under the [MIT License](LICENSE).

## 💖 Support the Project

If this project helps you:

- ⭐ Give it a Star
- 🐛 Report bugs or suggest features
- 🔀 Fork and contribute code
- 📢 Share with others

## 📞 Contact

- **Bug Reports**: [GitHub Issues](https://github.com/DAILtech/bilibili-hiatus-analyzer/issues)
- **Discussions**: [GitHub Discussions](https://github.com/DAILtech/bilibili-hiatus-analyzer/discussions)

---

<div align="center">

**Made with ❤️ by Python developers**

If this project helped you successfully remind your favorite UP creators, don't forget to give it a Star! ⭐

[⬆ Back to top](#-bilibili-hiatus-analyzer)

</div>

