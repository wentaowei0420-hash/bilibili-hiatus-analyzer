# 贡献指南

首先，感谢你愿意为 **B站催更分析器** 项目贡献力量！🎉

这份文档提供了关于如何贡献的指导原则。请花几分钟时间阅读，以确保贡献过程顺利进行。

## 📋 目录

- [行为准则](#行为准则)
- [我能做什么贡献？](#我能做什么贡献)
- [开始之前](#开始之前)
- [贡献流程](#贡献流程)
- [代码规范](#代码规范)
- [提交信息规范](#提交信息规范)
- [问题报告](#问题报告)
- [功能建议](#功能建议)

## 行为准则

参与本项目即表示你同意遵守我们的行为准则：

- 保持友善和尊重
- 接受建设性的批评
- 关注什么对社区最有利
- 对其他社区成员表示同理心

## 我能做什么贡献？

有很多方式可以为项目做出贡献：

### 🐛 报告Bug

发现了Bug？请帮助我们改进！

- 在提交之前，先搜索现有的[Issues](https://github.com/DAILtech/bilibili-hiatus-analyzer/issues)，避免重复报告
- 使用清晰的标题和详细的描述
- 提供复现步骤
- 说明预期行为和实际行为
- 附上截图（如果适用）
- 提供系统环境信息（Python版本、操作系统等）

### 💡 提出新功能

有好的想法？我们很乐意听取！

- 先在Issues中提出，讨论该功能的必要性
- 清楚地说明功能的用途和价值
- 考虑该功能是否适合大多数用户

### 📝 改进文档

文档永远可以更好！

- 修正拼写错误或语法问题
- 添加更多示例
- 改进现有文档的清晰度
- 翻译文档（如英文版）

### 💻 提交代码

修复Bug或实现新功能：

- Fork项目
- 创建功能分支
- 编写代码
- 添加测试（如果适用）
- 提交Pull Request

## 开始之前

在开始贡献之前，请：

1. **Fork仓库**到你的GitHub账号
2. **Clone到本地**：
   ```bash
   git clone https://github.com/DAILtech/bilibili-hiatus-analyzer.git
   cd bilibili-hiatus-analyzer
   ```
3. **创建虚拟环境**（推荐）：
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/macOS
   # 或
   venv\Scripts\activate  # Windows
   ```
4. **安装依赖**：
   ```bash
   pip install -r requirements.txt
   ```

## 贡献流程

### 1. 创建分支

为你的更改创建一个新分支：

```bash
git checkout -b feature/amazing-feature
```

分支命名规范：
- `feature/xxx` - 新功能
- `bugfix/xxx` - Bug修复
- `docs/xxx` - 文档改进
- `refactor/xxx` - 代码重构

### 2. 进行更改

- 编写清晰、可维护的代码
- 遵循项目的代码风格
- 添加必要的注释
- 更新相关文档

### 3. 测试

在提交之前，请确保：

- 代码能正常运行
- 没有引入新的Bug
- 所有功能按预期工作

测试你的更改：

```bash
python bilibili_hiatus_analyzer.py
```

### 4. 提交更改

```bash
git add .
git commit -m "feat: 添加了令人惊叹的新功能"
```

遵循[提交信息规范](#提交信息规范)。

### 5. 推送到GitHub

```bash
git push origin feature/amazing-feature
```

### 6. 创建Pull Request

1. 访问你Fork的仓库
2. 点击"New Pull Request"
3. 选择你的分支
4. 填写PR描述：
   - 清楚地描述你做了什么
   - 说明为什么需要这些更改
   - 关联相关的Issue（如果有）
   - 附上测试截图（如果适用）

## 代码规范

### Python代码风格

遵循[PEP 8](https://www.python.org/dev/peps/pep-0008/)规范：

```python
# Good ✅
def get_user_videos(user_id, page_size=50):
    """
    获取用户视频列表
    
    参数:
        user_id: 用户ID
        page_size: 每页数量，默认50
    
    返回:
        视频列表
    """
    pass

# Bad ❌
def GetUserVideos(userID,pageSize):
    pass
```

### 注释规范

- 使用中文注释（本项目面向中文用户）
- 为复杂逻辑添加说明
- 使用文档字符串说明函数用途

```python
def calculate_days_since(timestamp):
    """
    计算从指定时间戳到现在经过了多少天
    
    参数:
        timestamp: Unix时间戳
    
    返回:
        天数（整数）
    """
    # 实现逻辑...
```

### 错误处理

总是使用适当的错误处理：

```python
try:
    response = requests.get(url, timeout=10)
    response.raise_for_status()
except requests.exceptions.RequestException as e:
    print(f"请求失败: {e}")
    return None
```

## 提交信息规范

使用清晰的提交信息，遵循[约定式提交](https://www.conventionalcommits.org/)：

### 格式

```
<类型>(<范围>): <简短描述>

<详细描述>（可选）

<Footer>（可选）
```

### 类型

- `feat`: 新功能
- `fix`: Bug修复
- `docs`: 文档更新
- `style`: 代码格式调整（不影响功能）
- `refactor`: 代码重构
- `perf`: 性能优化
- `test`: 测试相关
- `chore`: 构建/工具相关

### 示例

```bash
# 好的提交信息 ✅
git commit -m "feat: 添加按播放量排序功能"
git commit -m "fix: 修复Cookie过期检测问题"
git commit -m "docs: 更新README中的安装说明"

# 不好的提交信息 ❌
git commit -m "更新"
git commit -m "修复bug"
git commit -m "一些改动"
```

## 问题报告

提交Bug报告时，请包含：

### Bug报告模板

```markdown
## 问题描述
简短描述遇到的问题

## 复现步骤
1. 第一步...
2. 第二步...
3. 看到错误

## 预期行为
应该发生什么

## 实际行为
实际发生了什么

## 截图
如果适用，添加截图

## 环境信息
- 操作系统: [例如 Windows 10]
- Python版本: [例如 3.9.5]
- 脚本版本: [例如 v1.0.0]

## 额外信息
其他相关信息
```

## 功能建议

提出新功能时，请包含：

### 功能建议模板

```markdown
## 功能描述
清晰简洁地描述你希望添加的功能

## 使用场景
这个功能解决什么问题？为什么需要它？

## 实现方案（可选）
如果你有实现的想法，可以描述一下

## 替代方案（可选）
你考虑过的其他解决方案

## 额外信息
其他相关信息、截图、示例等
```

## Pull Request检查清单

提交PR之前，请确认：

- [ ] 代码遵循项目的代码规范
- [ ] 添加了必要的注释
- [ ] 更新了相关文档
- [ ] 测试了所有更改
- [ ] 没有引入新的警告或错误
- [ ] 提交信息清晰明了
- [ ] PR描述详细说明了更改内容

## 需要帮助？

如果你在贡献过程中遇到问题：

- 查看现有的[Issues](https://github.com/DAILtech/bilibili-hiatus-analyzer/issues)
- 在[Discussions](https://github.com/DAILtech/bilibili-hiatus-analyzer/discussions)中提问
- 查看[README.md](README.md)和其他文档

## 致谢

感谢你花时间阅读这份指南，并考虑为本项目贡献！

每一个贡献，无论大小，都会让这个项目变得更好。❤️

---

**Happy Coding!** 🚀

