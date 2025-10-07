# 使用指南

本指南将手把手教你如何使用B站催更分析器。

## 📋 目录

- [前置准备](#前置准备)
- [第一步：安装](#第一步安装)
- [第二步：获取Cookie](#第二步获取cookie)
- [第三步：配置脚本](#第三步配置脚本)
- [第四步：运行脚本](#第四步运行脚本)
- [第五步：查看结果](#第五步查看结果)
- [进阶使用](#进阶使用)
- [故障排除](#故障排除)

---

## 前置准备

在开始之前，请确保你已经：

- ✅ 安装了Python 3.7或更高版本
- ✅ 有一个B站账号
- ✅ 关注了一些UP主（否则没什么可分析的😄）
- ✅ 能够访问B站

### 检查Python版本

打开命令行（Windows: CMD或PowerShell，Mac/Linux: Terminal），输入：

```bash
python --version
```

应该看到类似输出：
```
Python 3.9.5
```

如果提示"找不到命令"，请先[安装Python](https://www.python.org/downloads/)。

---

## 第一步：安装

### 1.1 下载项目

**方法A：使用Git（推荐）**

```bash
git clone https://github.com/DAILtech/bilibili-hiatus-analyzer.git
cd bilibili-hiatus-analyzer
```

**方法B：直接下载**

1. 访问项目GitHub页面
2. 点击绿色的"Code"按钮
3. 选择"Download ZIP"
4. 解压到任意目录
5. 在命令行中进入该目录

### 1.2 安装依赖

在项目目录运行：

```bash
pip install -r requirements.txt
```

**如果网速慢，使用国内镜像：**

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

看到类似输出表示成功：
```
Successfully installed requests-2.31.0
```

---

## 第二步：获取Cookie

这是最关键的一步！请仔细按照步骤操作。

### 2.1 准备工作

1. 打开浏览器（Chrome、Firefox、Edge都可以）
2. 访问 https://www.bilibili.com
3. **登录**你的B站账号（如果还没登录）

### 2.2 打开开发者工具

**方法A：快捷键**
- Windows/Linux: 按 `F12`
- Mac: 按 `Command + Option + I`

**方法B：菜单**
- Chrome: 右键 → 检查
- Firefox: 右键 → 检查元素

### 2.3 切换到Network标签

开发者工具打开后，顶部有多个标签，点击 **Network（网络）**。

```
Elements  Console  Sources  [Network]  ...
                             ↑ 点这里
```

### 2.4 刷新页面

按 `F5` 或点击浏览器的刷新按钮。

你会看到左侧出现很多请求：

```
Name                Status  Type    ...
www.bilibili.com    200     document
api.bilibili.com    200     xhr
...
```

### 2.5 找到Cookie

1. 点击左侧**任意一个请求**（建议点击第一个）
2. 右侧会显示详细信息
3. 找到 **Request Headers（请求头）** 部分
4. 向下滚动，找到 **Cookie** 字段

看起来像这样：
```
Cookie: SESSDATA=cb06xxxx...; bili_jct=xxxxx; DedeUserID=123456; ...
```

### 2.6 复制Cookie

**方法A：直接复制**
- 鼠标选中Cookie的值（从第一个字符到最后一个字符）
- 右键 → 复制

**方法B：双击复制**
- 双击Cookie的值
- Ctrl+C（Mac: Command+C）

⚠️ **重要**：
- 要复制**完整的**Cookie，不要遗漏任何部分
- Cookie很长，可能需要滚动才能看到全部
- 不要复制"Cookie: "这几个字，只要后面的值

### 2.7 验证Cookie

一个正确的Cookie应该包含：

```
SESSDATA=...    ← 必须有
bili_jct=...    ← 必须有
DedeUserID=...  ← 必须有
```

如果缺少这些字段，可能需要：
1. 确认已登录
2. 刷新页面后重新获取
3. 尝试访问 https://space.bilibili.com 后再获取

---

## 第三步：配置脚本

### 3.1 打开脚本文件

用任何文本编辑器打开 `bilibili_hiatus_analyzer.py`：

- Windows: 记事本、Notepad++、VS Code
- Mac: TextEdit、VS Code
- Linux: gedit、vim、VS Code

### 3.2 找到Cookie配置区域

在文件开头（大约第17行），你会看到：

```python
# ===========================
# 配置区域 - 请在这里填入你的信息
# ===========================

# Cookie配置
COOKIE = "在这里粘贴你的Cookie"
```

### 3.3 粘贴Cookie

将刚才复制的Cookie粘贴到引号中：

**修改前：**
```python
COOKIE = "在这里粘贴你的Cookie"
```

**修改后：**
```python
COOKIE = "SESSDATA=cb06xxxx...; bili_jct=xxxxx; DedeUserID=123456; ..."
```

⚠️ **注意**：
- 保留引号
- Cookie要在一行内
- 不要添加额外的空格或换行

### 3.4 保存文件

- Ctrl+S（Mac: Command+S）
- 或 文件 → 保存

---

## 第四步：运行脚本

### 4.1 在命令行中进入项目目录

```bash
cd /path/to/bilibili-hiatus-analyzer
```

### 4.2 运行脚本

```bash
python bilibili_hiatus_analyzer.py
```

### 4.3 观察输出

你会看到类似输出：

```
============================================================
🎯 B站催更分析器 - 寻找你关注的UP主中的「鸽王」
============================================================

📥 正在获取关注列表...
   已获取 50 位UP主...
   已获取 100 位UP主...
✅ 成功获取 150 位关注的UP主

🔍 正在分析每位UP主的最新视频...

[1/150] 正在获取 某某UP主 的最新视频...
   ✅ 最后更新: 5 天前
[2/150] 正在获取 另一位UP主 的最新视频...
   ✅ 最后更新: 30 天前
...
```

### 4.4 等待完成

- 这个过程需要几分钟（取决于你关注了多少人）
- 请耐心等待，不要中断
- 如果某个UP主获取失败，脚本会自动跳过

---

## 第五步：查看结果

### 5.1 控制台输出

脚本运行完成后，会显示Top 10鸽王排行榜：

```
============================================================
🏆 B站鸽王排行榜 - Top 10
============================================================

第 1 名: 某鸽王UP主
   ⏰ 已鸽 365 天
   📺 最新视频: 我一定会更新的！
   📅 发布日期: 2024-01-01 12:00:00
   👁️  播放量: 123,456
   🔗 链接: https://www.bilibili.com/video/BVxxxxxxxxx

第 2 名: ...
...
```

### 5.2 CSV文件

在项目目录下会生成 `bilibili_hiatus_ranking.csv` 文件。

**打开方法：**

**Windows:**
- 双击文件，用Excel打开
- 或右键 → 打开方式 → Excel

**Mac:**
- 双击文件
- 或用Numbers打开

**Linux:**
- LibreOffice Calc

### 5.3 CSV文件内容

文件包含所有UP主的详细数据，按未更新天数排序：

| UP主姓名 | UP主UID | 最新视频标题 | 发布日期 | 未更新天数 | 播放量 | 链接 |
|---------|---------|------------|---------|----------|--------|------|
| 鸽王UP | 123456 | ... | ... | 365 | 500000 | ... |

---

## 进阶使用

### 定制输出文件名

在脚本中找到：

```python
OUTPUT_CSV = "bilibili_hiatus_ranking.csv"
```

修改为你想要的文件名：

```python
OUTPUT_CSV = "my_analysis_2024.csv"
```

### 调整请求延时

如果你觉得速度太慢（不推荐修改）：

```python
REQUEST_DELAY = 1  # 秒
```

⚠️ 不建议设置小于1秒，可能导致被封禁。

### 定时自动运行

**Windows（任务计划程序）：**

1. Win+R → 输入 `taskschd.msc`
2. 创建基本任务
3. 设置触发器（如每周一早上9点）
4. 操作：启动程序
   - 程序：`python.exe`
   - 参数：`bilibili_hiatus_analyzer.py`
   - 起始于：脚本所在目录

**Mac/Linux（Cron）：**

```bash
# 编辑crontab
crontab -e

# 每周一上午9点运行
0 9 * * 1 cd /path/to/script && python bilibili_hiatus_analyzer.py
```

---

## 故障排除

### 问题1：提示"找不到python"

**解决方法：**
- Windows: 尝试用 `py` 代替 `python`
- 或重新安装Python，勾选"Add to PATH"

### 问题2：提示"Cookie可能已过期"

**解决方法：**
- 重新登录B站
- 按照步骤重新获取Cookie
- 更新脚本中的Cookie

### 问题3：提示"网络请求失败"

**解决方法：**
- 检查网络连接
- 确认能访问B站
- 稍后重试

### 问题4：某些UP主显示"暂无视频"

**原因：**
- UP主没发过视频（只发动态）
- UP主删除了所有视频
- 这是正常情况，不影响其他数据

### 问题5：CSV文件乱码

**解决方法：**
- 用WPS Office打开
- 或在Excel中：数据 → 从文本 → UTF-8编码导入

### 问题6：脚本运行很慢

**说明：**
- 这是正常的，为了避免被封禁
- 关注200人大约需要3-4分钟
- 请耐心等待

---

## 下一步

- 🌟 给项目点个Star
- 🐛 遇到问题？[提交Issue](https://github.com/DAILtech/bilibili-hiatus-analyzer/issues)
- 💡 有建议？[提交Feature Request](https://github.com/DAILtech/bilibili-hiatus-analyzer/issues/new?template=feature_request.md)
- 🤝 想贡献代码？查看[贡献指南](CONTRIBUTING.md)

---

**祝你成功催更你的爱豆UP主！** 🎉

