# 更新日志

本文档记录了项目的所有重要更改。

格式基于[Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 计划中
- 支持导出多种格式（Excel、JSON、HTML）
- 添加数据可视化功能（图表展示）
- 支持自定义排序规则
- 添加UP主标签分类功能
- 定期自动运行和邮件提醒
- GUI图形界面版本

## [1.0.0] - 2025-10-07

### 新增
- 🎉 项目首次发布
- 🔐 Cookie认证登录功能
- 📊 自动获取B站关注列表
- 🏆 生成"鸽王"排行榜
- 💾 导出CSV文件功能
- 📝 完整的中文文档
- 🛡️ 完善的错误处理机制
- ⏱️ 请求延时（君子协议）
- 🌐 跨平台支持（Windows/Linux/macOS）

### 核心功能
- 获取所有关注UP主列表（支持分页）
- 获取每位UP主的最新视频信息
- 计算未更新天数
- 按未更新天数降序排序
- 导出详细数据到CSV

### 数据字段
- UP主姓名
- UP主UID
- 最新视频标题
- 最新视频发布日期
- 未更新天数
- 最新视频播放量
- 视频链接

### 文档
- README.md - 项目说明文档
- CONTRIBUTING.md - 贡献指南
- LICENSE - MIT开源协议
- requirements.txt - 依赖列表
- .gitignore - Git忽略配置
- config_example.txt - Cookie配置示例

---

## 版本说明

### 版本号格式：主版本号.次版本号.修订号

- **主版本号**：不兼容的API修改
- **次版本号**：向下兼容的功能性新增
- **修订号**：向下兼容的问题修正

### 更新类型

- `新增` - 新功能
- `更改` - 现有功能的变更
- `弃用` - 即将移除的功能
- `移除` - 已移除的功能
- `修复` - Bug修复
- `安全` - 安全性相关的修复

---

**注意**：本项目遵循语义化版本规范。

[未发布]: https://github.com/DAILtech/bilibili-hiatus-analyzer/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/DAILtech/bilibili-hiatus-analyzer/releases/tag/v1.0.0

