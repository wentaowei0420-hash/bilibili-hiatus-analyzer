# 项目结构

本文档说明了B站催更分析器项目的文件组织结构。

```
bilibili-hiatus-analyzer/
│
├── .github/                          # GitHub配置目录
│   ├── ISSUE_TEMPLATE/              # Issue模板
│   │   ├── bug_report.md           # Bug报告模板
│   │   ├── feature_request.md      # 功能建议模板
│   │   └── question.md             # 问题咨询模板
│   ├── workflows/                   # GitHub Actions工作流
│   │   ├── lint.yml                # 代码质量检查
│   │   └── release.yml             # 自动发布
│   ├── pull_request_template.md    # PR模板
│   └── FUNDING.yml                  # 赞助配置
│
├── bilibili_hiatus_analyzer.py      # 🎯 主程序脚本
│
├── requirements.txt                  # Python依赖列表
├── setup.py                         # 安装配置文件
│
├── .gitignore                       # Git忽略配置
├── .editorconfig                    # 编辑器配置
│
├── README.md                        # 📖 项目主文档（必读）
├── README_bilibili.md               # 中文详细说明
├── USAGE_GUIDE.md                   # 📚 使用指南（新手友好）
├── FAQ.md                           # ❓ 常见问题解答
├── CONTRIBUTING.md                  # 🤝 贡献指南
├── CODE_OF_CONDUCT.md               # 行为准则
├── CHANGELOG.md                     # 📝 更新日志
├── LICENSE                          # ⚖️ MIT开源协议
├── SECURITY.md                      # 🔒 安全策略
├── PROJECT_STRUCTURE.md             # 📁 本文件
│
├── config_example.txt               # Cookie配置示例
│
└── bilibili_hiatus_ranking.csv      # 输出文件（运行后生成，不提交到Git）

```

## 文件说明

### 核心文件

#### `bilibili_hiatus_analyzer.py`
- **作用**: 主程序脚本
- **功能**: 
  - 获取B站关注列表
  - 分析UP主更新频率
  - 生成鸽王排行榜
  - 导出CSV文件
- **使用**: `python bilibili_hiatus_analyzer.py`

#### `requirements.txt`
- **作用**: Python依赖管理
- **内容**: 
  ```
  requests>=2.31.0
  ```
- **使用**: `pip install -r requirements.txt`

### 文档文件

#### `README.md`
- **作用**: 项目主文档
- **内容**: 
  - 项目介绍
  - 功能特性
  - 快速开始
  - 使用说明
  - FAQ
- **受众**: 所有用户

#### `USAGE_GUIDE.md`
- **作用**: 详细使用指南
- **内容**: 
  - 分步教程
  - 图文说明
  - 故障排除
- **受众**: 新手用户

#### `FAQ.md`
- **作用**: 常见问题解答
- **内容**: 
  - 安装问题
  - Cookie问题
  - 运行问题
  - 数据问题
- **受众**: 遇到问题的用户

#### `CONTRIBUTING.md`
- **作用**: 贡献指南
- **内容**: 
  - 贡献流程
  - 代码规范
  - 提交规范
- **受众**: 贡献者

#### `CHANGELOG.md`
- **作用**: 更新日志
- **内容**: 
  - 版本历史
  - 功能变更
  - Bug修复
- **受众**: 所有用户

#### `SECURITY.md`
- **作用**: 安全策略
- **内容**: 
  - 漏洞报告流程
  - 安全最佳实践
  - 已知安全考虑
- **受众**: 安全研究者、用户

### 配置文件

#### `.gitignore`
- **作用**: Git忽略配置
- **内容**: 
  - Python缓存文件
  - 虚拟环境
  - CSV输出文件
  - Cookie配置文件
- **重要性**: 防止敏感信息泄露

#### `.editorconfig`
- **作用**: 编辑器配置
- **内容**: 
  - 缩进规则
  - 字符编码
  - 换行符
- **受益**: 保持代码风格一致

#### `setup.py`
- **作用**: 安装配置
- **内容**: 
  - 包信息
  - 依赖声明
  - 元数据
- **用途**: 作为Python包安装

### GitHub配置

#### `.github/ISSUE_TEMPLATE/`
- **bug_report.md**: Bug报告模板
- **feature_request.md**: 功能建议模板
- **question.md**: 问题咨询模板

#### `.github/workflows/`
- **lint.yml**: 代码质量检查（Flake8、Black）
- **release.yml**: 自动创建Release

#### `.github/pull_request_template.md`
- **作用**: PR模板
- **内容**: 
  - 更改描述
  - 测试说明
  - 检查清单

#### `.github/FUNDING.yml`
- **作用**: 赞助配置
- **用途**: GitHub Sponsors等

### 示例文件

#### `config_example.txt`
- **作用**: Cookie配置示例
- **内容**: 
  - 获取步骤
  - 配置方法
  - 注意事项
- **受众**: 新手用户

### 输出文件

#### `bilibili_hiatus_ranking.csv`
- **作用**: 分析结果输出
- **生成**: 运行脚本后自动生成
- **内容**: 
  - UP主信息
  - 视频数据
  - 未更新天数
- **注意**: 
  - 不会提交到Git（在.gitignore中）
  - 包含个人数据，注意保管

## 文件依赖关系

```
用户阅读流程：
README.md → USAGE_GUIDE.md → FAQ.md → CONTRIBUTING.md

开发流程：
CONTRIBUTING.md → CODE_OF_CONDUCT.md → SECURITY.md

版本管理：
CHANGELOG.md ← setup.py

自动化：
.github/workflows/*.yml → README.md, CHANGELOG.md
```

## 文件维护

### 必须更新的文件

当添加新功能时：
- ✅ `CHANGELOG.md` - 记录更改
- ✅ `README.md` - 更新功能列表
- ✅ `setup.py` - 更新版本号

当修复Bug时：
- ✅ `CHANGELOG.md` - 记录修复
- ✅ FAQ.md（如果是常见问题）

当更改API时：
- ✅ `README.md` - 更新使用说明
- ✅ `USAGE_GUIDE.md` - 更新步骤
- ✅ `CHANGELOG.md` - 标注破坏性更改

### 定期检查的文件

- `README.md` - 保持截图/示例最新
- `FAQ.md` - 添加新的常见问题
- `requirements.txt` - 更新依赖版本
- `SECURITY.md` - 审查安全策略

## 新手指南

**我该看哪些文件？**

1. 📖 **想了解项目** → `README.md`
2. 🚀 **想快速上手** → `USAGE_GUIDE.md`
3. ❓ **遇到问题** → `FAQ.md`
4. 🤝 **想贡献代码** → `CONTRIBUTING.md`
5. 🔒 **关心安全** → `SECURITY.md`
6. 📝 **查看更新** → `CHANGELOG.md`

**我该修改哪些文件？**

- ✅ **仅配置**: `bilibili_hiatus_analyzer.py`（填入Cookie）
- ❌ **不要动**: 其他所有文件（除非你知道在做什么）

## 开发者指南

**添加新功能的检查清单：**

- [ ] 实现功能代码
- [ ] 更新 `README.md`
- [ ] 更新 `USAGE_GUIDE.md`（如果影响使用）
- [ ] 更新 `CHANGELOG.md`
- [ ] 更新 `setup.py` 版本号
- [ ] 添加必要的文档注释
- [ ] 测试功能
- [ ] 提交PR

**发布新版本的检查清单：**

- [ ] 更新 `CHANGELOG.md`
- [ ] 更新 `setup.py` 版本号
- [ ] 更新 `README.md`（如果需要）
- [ ] 创建Git标签 `git tag v1.x.x`
- [ ] 推送标签 `git push --tags`
- [ ] GitHub Actions自动创建Release

---

**有疑问？** 查看 [CONTRIBUTING.md](CONTRIBUTING.md) 或提交Issue。

