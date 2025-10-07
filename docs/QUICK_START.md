# 快速开始

5分钟快速上手B站催更分析器！

## 🎯 三步走

### 1️⃣ 安装

```bash
# 克隆项目
git clone https://github.com/DAILtech/bilibili-hiatus-analyzer.git
cd bilibili-hiatus-analyzer

# 安装依赖
pip install -r requirements.txt
```

### 2️⃣ 配置Cookie

1. 登录 [B站](https://www.bilibili.com)
2. 按 `F12` → `Network` → 刷新页面
3. 点击任意请求 → 找到 `Cookie` → 复制
4. 打开 `bilibili_hiatus_analyzer.py`
5. 找到第17行，粘贴Cookie：
   ```python
   COOKIE = "你的Cookie"
   ```

### 3️⃣ 运行

```bash
python bilibili_hiatus_analyzer.py
```

等待几分钟，查看生成的 `bilibili_hiatus_ranking.csv` 文件。

## ✅ 完成！

现在你知道谁最能鸽了！去催更吧！🎉

---

**遇到问题？** → [FAQ.md](FAQ.md) | [完整教程](USAGE_GUIDE.md)

