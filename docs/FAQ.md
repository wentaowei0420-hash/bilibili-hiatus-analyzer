# 常见问题 FAQ

这里汇总了使用B站催更分析器时最常遇到的问题和解决方案。

## 📋 目录

- [安装和配置](#安装和配置)
- [Cookie相关](#cookie相关)
- [运行问题](#运行问题)
- [数据相关](#数据相关)
- [其他问题](#其他问题)

---

## 安装和配置

### Q: 需要什么版本的Python？

**A**: Python 3.7或更高版本。推荐使用Python 3.9或3.10。

检查你的Python版本：
```bash
python --version
```

### Q: 如何安装依赖？

**A**: 在项目目录运行：
```bash
pip install -r requirements.txt
```

如果使用国内网络，建议使用国内镜像源：
```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### Q: Windows上提示"找不到python"怎么办？

**A**: 
1. 确保已安装Python
2. 安装时勾选"Add Python to PATH"
3. 或者尝试使用`py`命令代替`python`：
   ```bash
   py bilibili_hiatus_analyzer.py
   ```

---

## Cookie相关

### Q: 为什么需要Cookie？

**A**: B站的关注列表API需要登录认证。Cookie包含你的登录凭证，让脚本能以你的身份访问API。

### Q: Cookie在哪里获取？

**A**: 详细步骤请参考[README中的Cookie获取教程](README.md#-cookie获取教程)。

简要步骤：
1. 登录B站
2. F12打开开发者工具
3. Network标签 → 刷新页面
4. 任意请求 → Request Headers → 复制Cookie

### Q: Cookie包含哪些必需字段？

**A**: 必需包含以下字段：
- `SESSDATA` - 会话认证（最重要）
- `bili_jct` - CSRF令牌
- `DedeUserID` - 用户ID

### Q: 提示"Cookie可能已过期"怎么办？

**A**: Cookie有时效性，通常几天到几周会过期。

解决方法：
1. 重新登录B站
2. 按照教程重新获取Cookie
3. 更新脚本中的Cookie配置

### Q: Cookie安全吗？会不会泄露？

**A**: 
- ✅ 脚本完全在本地运行，不会上传Cookie
- ✅ 代码开源透明，可自行审查
- ⚠️ 但请注意：不要将包含Cookie的脚本分享给他人
- ⚠️ 不要在公共电脑上保存Cookie
- ⚠️ 不要将Cookie提交到Git仓库

### Q: 如何保护Cookie安全？

**A**: 
1. 使用环境变量存储Cookie（而不是硬编码）
2. 不要截图包含Cookie的代码
3. 定期更换Cookie（重新登录）
4. 使用完后及时删除包含Cookie的文件

---

## 运行问题

### Q: 提示"网络请求失败"怎么办？

**A**: 可能的原因和解决方法：

1. **网络问题**
   - 检查网络连接
   - 尝试访问 bilibili.com 确认能正常访问

2. **请求过快被限流**
   - 增加`REQUEST_DELAY`的值（改为2或3秒）
   - 稍后重试

3. **代理/VPN问题**
   - 如果使用了代理，尝试关闭
   - 或者配置requests使用代理

### Q: 提示"API返回错误"怎么办？

**A**: 根据具体错误码处理：

- `-101`: 账号未登录 → Cookie过期，重新获取
- `-352`: 风控校验失败 → 更换网络环境或稍后重试
- `-403`: 访问权限不足 → 检查Cookie是否完整
- `-404`: 接口不存在 → B站API可能更新，等待脚本更新

### Q: 脚本运行很慢，正常吗？

**A**: 是的，这是正常现象。

原因：
- 为了遵守"君子协议"，每次请求间隔1秒
- 如果关注了200位UP主，需要约3-4分钟

计算公式：
```
大约耗时 = 关注数 × 请求延时(秒) / 60
```

**不建议**减少延时，可能导致：
- IP被封禁
- Cookie失效
- 触发风控

### Q: 脚本卡住不动了？

**A**: 可能原因：

1. **某个UP主的数据获取超时**
   - 等待timeout（默认10秒）
   - 脚本会自动跳过并继续

2. **网络突然中断**
   - 检查网络
   - Ctrl+C中断后重新运行

3. **真的卡死了**
   - Ctrl+C中断
   - 查看最后的输出，确定卡在哪里
   - 提交Issue报告

### Q: 可以中断后继续运行吗？

**A**: 当前版本不支持断点续传。如果中途中断，需要重新运行。

未来版本可能会添加：
- 进度保存
- 断点续传
- 增量更新

---

## 数据相关

### Q: 某些UP主显示"暂无视频"？

**A**: 可能的原因：

1. **UP主确实没有视频**
   - 只发动态，从未发过视频
   - 纯直播UP主

2. **视频被删除/隐藏**
   - UP主删除了所有视频
   - 视频设为私密或仅粉丝可见

3. **API权限问题**
   - 某些特殊账号的视频列表需要更高权限

这不影响其他UP主的数据获取。

### Q: 播放量数据不准确？

**A**: 
- 播放量是获取时的快照，不是实时数据
- B站的播放量有时会延迟更新
- 某些视频的播放量可能被隐藏

### Q: 为什么某些UP主的最新视频日期不对？

**A**: 可能原因：

1. **UP主删除了更新的视频**
   - 脚本获取的是当前最新可见的视频

2. **视频发布后又设为私密**
   - API只能看到公开视频

3. **动态和视频的区别**
   - 脚本只统计正式视频，不包括动态

### Q: CSV文件中文乱码？

**A**: 
脚本使用`utf-8-sig`编码，Excel应该能正确显示。

如果仍有问题：

**方法1: 使用其他软件**
- WPS Office
- Google Sheets
- LibreOffice Calc

**方法2: Excel导入**
1. Excel → 数据 → 从文本
2. 文件原始格式选择"65001: Unicode (UTF-8)"
3. 导入

**方法3: 转换编码**
1. 用记事本打开CSV
2. 另存为 → 编码选择"ANSI"
3. 用Excel打开新文件

### Q: 可以导出其他格式吗（Excel、JSON等）？

**A**: 当前版本只支持CSV。

未来可能添加：
- Excel (.xlsx)
- JSON
- HTML报告
- 数据可视化图表

如果你需要这个功能，欢迎在Issues中提出！

---

## 其他问题

### Q: 可以分析别人的关注列表吗？

**A**: 不可以。脚本只能分析当前登录账号（Cookie所属账号）的关注列表。

### Q: 会不会被B站封号？

**A**: 极小概率，只要：

1. ✅ 不修改请求延时为过于频繁的值
2. ✅ 不在短时间内多次运行
3. ✅ 只用于个人学习和使用
4. ✅ 不进行大规模数据采集

建议：
- 每天最多运行1-2次
- 保持1秒以上的请求延时
- 不要用于商业目的

### Q: 可以定时自动运行吗？

**A**: 可以，但需要自己配置：

**Windows (任务计划程序)**
1. 打开"任务计划程序"
2. 创建基本任务
3. 触发器：每天/每周
4. 操作：启动程序 → python.exe
5. 参数：脚本路径

**Linux/macOS (cron)**
```bash
# 编辑crontab
crontab -e

# 每天上午10点运行
0 10 * * * cd /path/to/script && python bilibili_hiatus_analyzer.py
```

### Q: 可以添加邮件提醒吗？

**A**: 可以自行扩展。在脚本末尾添加邮件发送逻辑：

```python
import smtplib
from email.mime.text import MIMEText

# 在分析完成后
def send_email_notification(top_pigeons):
    # 配置你的邮箱
    sender = "your@email.com"
    receiver = "your@email.com"
    
    # 构建邮件内容
    content = f"今日鸽王：{top_pigeons[0]['uploader_name']}"
    
    # 发送邮件
    # ... (邮件发送代码)
```

未来版本可能会内置此功能。

### Q: 代码可以商用吗？

**A**: 
本项目基于MIT协议开源，你可以：
- ✅ 个人使用
- ✅ 学习研究
- ✅ 修改代码
- ✅ 商业使用（需遵守协议）

但请注意：
- ⚠️ 需遵守B站用户协议
- ⚠️ 不要进行大规模数据采集
- ⚠️ 商业使用需自行承担风险

### Q: 如何贡献代码或报告问题？

**A**: 
- 报告Bug: [提交Issue](https://github.com/DAILtech/bilibili-hiatus-analyzer/issues/new?template=bug_report.md)
- 功能建议: [提交Issue](https://github.com/DAILtech/bilibili-hiatus-analyzer/issues/new?template=feature_request.md)
- 贡献代码: 查看[贡献指南](CONTRIBUTING.md)

### Q: 还有其他问题？

**A**: 
- 查看[README.md](README.md)
- 搜索现有[Issues](https://github.com/DAILtech/bilibili-hiatus-analyzer/issues)
- 在[Discussions](https://github.com/DAILtech/bilibili-hiatus-analyzer/discussions)中提问
- 提交新的Issue

---

**没找到你的问题？**

欢迎在[GitHub Discussions](https://github.com/DAILtech/bilibili-hiatus-analyzer/discussions)中提问，或者[提交Issue](https://github.com/DAILtech/bilibili-hiatus-analyzer/issues/new?template=question.md)！

