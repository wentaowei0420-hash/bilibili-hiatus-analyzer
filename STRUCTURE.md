# 📁 项目结构

> B站催更分析器的完整目录结构说明

## 🗂️ 目录结构

```
bilibili-hiatus-analyzer/
│
├── 📄 核心文件
│   ├── bilibili_hiatus_analyzer.py    # 主程序脚本
│   ├── requirements.txt                # Python依赖列表
│   ├── setup.py                        # 安装配置文件
│   ├── LICENSE                         # MIT开源协议
│   └── README.md                       # 项目主文档
│
├── ⚙️ 配置文件
│   ├── .gitignore                      # Git忽略配置
│   └── .editorconfig                   # 编辑器配置
│
├── 📚 docs/ - 文档目录
│   ├── README.md                       # 文档索引
│   │
│   ├── 🚀 用户文档
│   ├── 00_START_HERE.md               # 快速导航
│   ├── QUICK_START.md                 # 快速开始
│   ├── USAGE_GUIDE.md                 # 详细教程
│   ├── FAQ.md                         # 常见问题
│   ├── README_bilibili.md             # 完整说明（中文）
│   └── README_EN.md                   # 完整说明（英文）
│   │
│   ├── 🤝 社区文档
│   ├── CONTRIBUTING.md                # 贡献指南
│   ├── CODE_OF_CONDUCT.md             # 行为准则
│   ├── CHANGELOG.md                   # 更新日志
│   └── SECURITY.md                    # 安全策略
│   │
│   ├── 🔧 开发者文档
│   ├── PROJECT_STRUCTURE.md           # 详细结构说明
│   ├── PROJECT_FILES_OVERVIEW.md      # 文件总览
│   └── PROJECT_SUMMARY.md             # 项目总结
│   │
│   └── 📋 维护者文档
│       ├── GITHUB_RELEASE_CHECKLIST.md    # 发布清单
│       └── GITHUB_OPEN_SOURCE_READY.md    # 开源准备报告
│
├── 📝 examples/ - 示例目录
│   ├── README.md                      # 示例说明
│   ├── config_example.txt             # Cookie配置示例
│   └── example_output.csv             # 输出文件示例
│
└── 🤖 .github/ - GitHub配置
    ├── dependabot.yml                 # 依赖自动更新
    ├── FUNDING.yml                    # 赞助配置
    ├── pull_request_template.md       # PR模板
    │
    ├── ISSUE_TEMPLATE/                # Issue模板
    │   ├── bug_report.md             # Bug报告
    │   ├── feature_request.md        # 功能建议
    │   └── question.md               # 问题咨询
    │
    └── workflows/                     # GitHub Actions
        ├── lint.yml                  # 代码质量检查
        └── release.yml               # 自动发布
```

## 📂 目录说明

### 根目录（7个文件）

**保持简洁，只包含核心文件：**

| 文件 | 说明 | 重要性 |
|------|------|--------|
| `bilibili_hiatus_analyzer.py` | 主程序 | ⭐⭐⭐⭐⭐ |
| `requirements.txt` | 依赖管理 | ⭐⭐⭐⭐⭐ |
| `setup.py` | 安装配置 | ⭐⭐⭐⭐ |
| `LICENSE` | 开源协议 | ⭐⭐⭐⭐⭐ |
| `README.md` | 项目主文档 | ⭐⭐⭐⭐⭐ |
| `.gitignore` | Git配置 | ⭐⭐⭐⭐⭐ |
| `.editorconfig` | 编辑器配置 | ⭐⭐⭐ |

### docs/ 目录（15个文件）

**所有文档集中管理：**

- **用户文档**（6个）：帮助用户使用项目
- **社区文档**（4个）：管理开源社区
- **开发者文档**（3个）：帮助理解项目
- **维护者文档**（2个）：项目发布和维护

### examples/ 目录（3个文件）

**示例和模板：**

- Cookie配置示例
- CSV输出示例
- 示例说明文档

### .github/ 目录（8个文件）

**GitHub自动化配置：**

- Issue模板（3个）
- PR模板（1个）
- GitHub Actions（2个）
- 其他配置（2个）

## 🎯 设计原则

### 1. 简洁的根目录

根目录只保留**必需文件**，让项目看起来清爽专业：
- ✅ 核心代码
- ✅ 依赖配置
- ✅ 主README
- ✅ 开源协议

### 2. 文档集中化

所有文档放在 `docs/` 目录：
- 📚 易于查找
- 📖 便于维护
- 🔗 清晰的组织

### 3. 示例独立化

示例文件放在 `examples/` 目录：
- 📝 不污染根目录
- 🎯 用途明确
- 📂 易于管理

### 4. GitHub配置隐藏

GitHub相关配置在 `.github/` 目录：
- 🤖 自动化配置
- 📋 模板文件
- 🔧 工作流

## 📊 文件统计

```
总文件数: 33
│
├── 根目录: 7 (21%)
├── docs/: 16 (48%)
├── examples/: 3 (9%)
└── .github/: 7 (22%)
```

## 🔍 快速查找

### 想使用项目？
→ 根目录 `README.md` → `docs/QUICK_START.md`

### 想了解详情？
→ `docs/README.md` → 选择相关文档

### 想贡献代码？
→ `docs/CONTRIBUTING.md` → `docs/PROJECT_STRUCTURE.md`

### 需要示例？
→ `examples/` 目录

### 想报告问题？
→ GitHub Issues（使用 `.github/ISSUE_TEMPLATE/`）

## 💡 优势

### 对比旧结构

**之前**：30+ 个文件全在根目录 ❌
- 难以查找
- 看起来混乱
- 不专业

**现在**：清晰的三级结构 ✅
- 一目了然
- 组织有序
- 专业规范

### 符合最佳实践

- ✅ GitHub推荐的目录结构
- ✅ Python项目标准布局
- ✅ 开源项目通用规范

## 🔄 维护建议

### 添加新文件时

1. **文档类** → `docs/`
2. **示例类** → `examples/`
3. **配置类** → 根目录
4. **GitHub** → `.github/`

### 保持根目录简洁

只有这些文件应该在根目录：
- Python源代码
- 配置文件（requirements.txt, setup.py等）
- README.md
- LICENSE

## 📚 延伸阅读

- [完整文档索引](docs/README.md)
- [详细结构说明](docs/PROJECT_STRUCTURE.md)
- [文件总览](docs/PROJECT_FILES_OVERVIEW.md)

---

**返回**: [主页](README.md) | [文档中心](docs/README.md)

