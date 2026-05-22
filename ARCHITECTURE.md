# 系统架构与功能说明

本文档面向两类读者：

1. 新接手本项目的开发者。
2. 需要快速理解项目结构、边界和主流程的 AI / Agent。

目标不是逐行解释代码，而是提供一份“先读这一份就能快速上手”的项目地图。

## 1. 项目一句话概述

这是一个面向 `Bilibili` 和 `抖音` 的“停更/活跃度分析系统”。

它支持：

- 抓取 B 站关注列表与 UP 主数据。
- 抓取抖音关注列表、主页、视频明细与缓存。
- 对抖音视频和创作者做评分。
- 导出 CSV / SQLite 结果。
- 上传结果到飞书表格。
- 通过桌面 GUI 或 Web API 发起、查看、取消任务。

## 2. AI / 开发者快速入口

如果你是第一次看这个仓库，推荐按下面顺序阅读：

1. `ARCHITECTURE.md`
2. `backend/README.md`
3. `backend/api.py`
4. `backend/task_runner.py`
5. `gui.py`
6. `gui_backend_client.py`
7. `bilibili_analyzer/app.py`
8. `douyin_analyzer/app.py`
9. `common/export_store.py`
10. `common/platform_store.py`

如果你只想知道“这个系统怎么跑起来”，先看：

- `start_gui.bat`：双击启动桌面版
- `start_web.bat`：双击启动 Web 版
- `gui.py`：桌面入口
- `backend/__main__.py`：后端入口

## 3. 顶层目录说明

```text
bilibili-hiatus-analyzer/
├─ backend/                  HTTP API、任务队列、GUI 元数据、数据查询接口
├─ bilibili_analyzer/        B 站分析主逻辑
├─ douyin_analyzer/          抖音分析主逻辑、评分、缓存同步
├─ common/                   共享基础设施：仓储、导出、停止控制、平台状态库
├─ frontend/                 极简 Web 控制台（静态页面）
├─ data/                     运行输出、缓存、状态文件、列表输入
├─ runtime/                  运行时日志、浏览器用户目录、vendor 依赖
├─ tests/                    顶层测试
├─ douyin-downloader-main/   外部下载器子项目，当前与主系统隔离
├─ gui.py                    PyQt 桌面 GUI
├─ gui_backend_client.py     GUI 到本地后端的 HTTP 客户端与自动拉起逻辑
├─ gui_models.py             GUI 运行参数模型
├─ main.py                   旧式 CLI 统一入口
├─ start_gui.bat             Windows 双击启动桌面版
└─ start_web.bat             Windows 双击启动 Web 版
```

## 4. 系统分层

可以把当前系统理解成 5 层。

### 4.1 交互层

包含三种入口：

- `gui.py`
  - 当前最完整的桌面入口。
  - 基于 `PyQt5`。
  - 负责界面、按钮、对话框、进度条、日志展示。
  - 不直接承载核心业务，而是调用本地后端 API。

- `frontend/index.html`
  - 极简 Web 控制台。
  - 由 `backend/api.py` 挂载到 `/` 和 `/static`。
  - 负责创建任务、查看任务列表、轮询日志、取消任务。

- `main.py`
  - 旧式 CLI 统一入口。
  - 仍然可以直接触发 B 站、抖音、UID 抓取、评分、导出等能力。
  - 更像是“脚本式入口”，不是现在的主 UI 边界。

### 4.2 接口与编排层

主要在 `backend/`。

- `backend/api.py`
  - 对外暴露 HTTP API。
  - 提供健康检查、任务创建、任务查询、日志拉取、GUI 元数据、配置默认值、抖音统计与评分查询等接口。

- `backend/job_manager.py`
  - 管理任务生命周期。
  - 使用 `ThreadPoolExecutor(max_workers=1)`。
  - 当前默认单线程串行执行任务。

- `backend/task_runner.py`
  - 是“后端请求 -> 实际业务函数”的核心分发器。
  - 把 `JobCreateRequest` 转成环境变量。
  - 统一调用 `bilibili_analyzer.app` / `douyin_analyzer.app` 中的具体函数。
  - 捕获 stdout/stderr，把业务日志转成任务日志流。

- `backend/job_models.py`
  - 定义任务请求、任务状态、任务类型、运行时设置结构。

### 4.3 业务层

分为两个平台包。

#### `bilibili_analyzer/`

主要负责：

- 关注列表抓取
- UP 主停更分析
- 视频时长分析
- UID 全量视频抓取
- 输出 CSV / SQLite
- 飞书上传

关键文件：

- `app.py`：平台级入口函数
- `analyzer.py`：核心分析逻辑
- `bilibili_api.py` / `http_client.py`：B 站接口访问
- `cache.py`：缓存读写
- `export_service.py` / `exporters.py`：导出逻辑
- `feishu_uploader.py`：飞书上传
- `config.py`：配置与路径定义

#### `douyin_analyzer/`

主要负责：

- 抖音关注列表抓取
- 抖音主页/视频抓取
- 多种抓取模式：`counts` / `verify` / `monitor` / `delta` / `full`
- UID 全量视频抓取
- 取消关注
- 非当前关注缓存清理
- 喜欢视频缓存
- 缓存同步
- 视频评分与创作者评分
- 精简 CSV 导出
- 飞书上传

关键文件：

- `app.py`：平台级入口函数
- `analyzer.py`：核心分析逻辑
- `browser_client.py`：DrissionPage 浏览器抓取实现
- `playwright_browser_client.py`：Playwright 浏览器抓取实现
- `data_sync.py`：progress 与状态表同步
- `export_service.py` / `exporters.py`：导出逻辑
- `rating/creator_scoring.py` / `rating/video_scoring.py`：评分逻辑
- `rating/store.py`：评分相关存储
- `config.py`：配置与路径定义

### 4.4 共享基础设施层

主要在 `common/`。

- `runtime_control.py`
  - 提供全局停止信号。
  - `request_stop()` 会触发共享的 `_STOP_EVENT`。
  - 分析过程中通过 `check_stop()` 主动检查并中断。

- `repositories.py`
  - `AnalyzerCacheRepository` 是对旧缓存结构的轻量仓储封装。
  - 目的是在不破坏现有缓存文件的前提下，给业务层提供更稳定的依赖边界。

- `export_store.py`
  - 管理导出 SQLite 表与快照历史。
  - 支持“当前表 + 历史快照”模式。

- `platform_store.py`
  - 管理平台级 SQLite 状态库。
  - 保存创作者、视频、视频状态、缓存状态、摘要信息。

### 4.5 外部隔离子项目

- `douyin-downloader-main/`
  - 当前仓库明确将其视为“隔离子项目”。
  - `backend/README.md` 已说明：新的 API/GUI 重构**不应该直接耦合或改造这个子项目**。
  - `gui.py` 只是在部分场景下启动其 GUI，而不是把它并入主分析链路。

## 5. 核心架构图

```text
用户
  │
  ├─ 双击 start_gui.bat
  │    └─ gui.py (PyQt)
  │         └─ gui_backend_client.py
  │              ├─ 自动检查/拉起 backend
  │              └─ 通过 HTTP 调 backend/api.py
  │
  ├─ 双击 start_web.bat
  │    └─ 启动 backend
  │         └─ 浏览器打开 http://127.0.0.1:8000
  │              └─ frontend/index.html 调 backend/api.py
  │
  └─ 直接运行 main.py
       └─ 直接调用 bilibili_analyzer.app / douyin_analyzer.app

backend/api.py
  └─ JobManager
       └─ task_runner.run_job()
            ├─ 设置运行时环境变量
            ├─ 构建 reporter / log stream
            └─ 分发到平台业务函数
                 ├─ bilibili_analyzer.app
                 └─ douyin_analyzer.app

业务执行
  ├─ 读取 .env 和 config.py
  ├─ 读写 data/*/state 下的缓存与 SQLite
  ├─ 写入 data/*/output 下的 CSV / Markdown
  ├─ 写 runtime/logs
  └─ 可选上传飞书
```

## 6. 主要运行模式

### 6.1 桌面 GUI 模式

入口：

- `start_gui.bat`
- `gui.py`

特点：

- 当前最完整的交互方式。
- GUI 自己不直接跑重任务，而是通过 `gui_backend_client.py` 调本地 API。
- 当 API 地址是 `127.0.0.1 / localhost / ::1` 且后端不可用时，GUI 会自动启动 `python -m backend`。

### 6.2 Web 模式

入口：

- `start_web.bat`
- `backend/__main__.py`
- `frontend/index.html`

特点：

- `start_web.bat` 会先做健康检查。
- 如果后端未启动，会后台启动 `python -m backend`。
- 然后自动打开浏览器访问 `http://127.0.0.1:8000`。

### 6.3 CLI 模式

入口：

- `main.py`

特点：

- 适合快速脚本执行。
- 直接调用业务函数，不经过 HTTP/任务队列。
- 对后续新功能来说，优先考虑接入 `backend/` 和 `gui.py`，CLI 可按需同步。

## 7. 后端任务系统

### 7.1 为什么是任务模型

分析任务通常是长耗时、涉及浏览器自动化、日志连续输出的，因此系统把一次执行建模为 `Job`。

### 7.2 任务请求模型

定义在 `backend/job_models.py`：

- `JobKind`：任务种类
- `AnalysisAction`：动作类型
- `RuntimeSettings`：运行时参数
- `FetchOrderSettings`：UID 抓取顺序
- `JobCreateRequest`：创建任务的完整请求体

### 7.3 当前支持的任务

`JobKind` 当前包含：

- `bilibili_analysis`
- `douyin_analysis`
- `both_analysis`
- `bilibili_upload`
- `douyin_upload`
- `bilibili_uid_fetch`
- `douyin_uid_fetch`
- `douyin_unfollow`
- `douyin_video_score`
- `douyin_creator_score`
- `douyin_rating_refresh`
- `douyin_compact_export`
- `douyin_liked_video_cache`

### 7.4 任务执行链

执行路径如下：

1. `POST /api/jobs`
2. `backend/api.py` 调 `job_manager.create_job()`
3. `JobManager` 把任务提交到线程池
4. `task_runner.run_job()` 根据 `JobKind` 分发
5. 业务代码输出日志
6. 日志被 `LineWriter` 捕获并写入任务日志缓冲区
7. 前端或 GUI 轮询 `/api/jobs/{id}` 和 `/api/jobs/{id}/events`

### 7.5 为什么默认单线程

`backend/api.py` 中的 `JobManager(max_workers=1)` 和 `backend/README.md` 的说明都表明：

- 抖音浏览器自动化不适合并发运行。
- 运行停止标志是全局共享的。
- 共用缓存/状态文件时，并发冲突风险较高。

这意味着当前系统更像“单队列任务执行器”，而不是多任务并发平台。

### 7.6 取消机制的重要限制

`common/runtime_control.py` 使用的是**全局**停止事件，而不是 job 级别的取消令牌。

这意味着：

- 取消一个运行中的任务，本质上是发出全局停止请求。
- 在当前单线程模式下这通常是可接受的。
- 如果未来改为多任务并发，这里必须重构，否则取消语义会不正确。

## 8. GUI 的设计方式

### 8.1 GUI 不是“直接操作业务层”

`gui.py` 负责：

- 画界面
- 采集表单参数
- 触发线程
- 展示日志与进度
- 弹出对话框

它不应该直接读取各类 SQLite 或缓存文件来拼业务结果。

### 8.2 GUI 元数据来自后端

以下信息由后端提供，而不是硬编码在 GUI 里：

- 运行时参数字段
- 下拉选项
- 表格列定义
- 评分展示列
- 统计区间定义

对应接口和文件：

- `/api/gui/metadata` -> `backend/gui_schema.py`
- `/api/config/defaults` -> `backend/config_defaults.py`

这是一条非常重要的边界：

- GUI 负责“渲染”
- 后端负责“定义展示协议”

后续如果你要新增一个 GUI 配置项，通常要同时看：

1. `backend/task_runner.py`
2. `backend/gui_schema.py`
3. `backend/config_defaults.py`
4. `gui.py`
5. `gui_models.py`
6. `gui_backend_client.py`

## 9. Web 前端的定位

`frontend/index.html` 是一个非常轻量的控制台，不是复杂前端工程。

它的职责只有：

- 创建任务
- 查看任务列表
- 轮询日志
- 取消任务
- 查看健康状态

所以：

- 复杂业务规则仍应放在 `backend/`
- 如果要扩展高级功能，优先先加后端接口，再决定是否扩展 Web 页面

## 10. 两个平台的业务职责划分

### 10.1 Bilibili 侧

主要输出：

- 停更排行
- 全量视频明细
- 视频时长分析
- UID 抓取汇总

主要状态来源：

- 关注列表缓存
- 精确分析进度
- 视频时长分析进度

主要落盘位置：

- `data/bilibili/output/`
- `data/bilibili/state/`

### 10.2 Douyin 侧

功能更重，主要包括：

- 关注列表缓存
- 主页信息核验
- 不同抓取模式的数据刷新
- 视频明细抓取
- 喜欢视频缓存
- 非当前关注缓存清理
- 取消关注
- 视频评分
- 创作者评分
- 精简表导出
- 归档/恢复/状态重置相关数据视图

主要落盘位置：

- `data/douyin/output/`
- `data/douyin/state/`

## 11. 数据存储设计

系统当前不是纯数据库驱动，而是“文件缓存 + SQLite 状态库 + 导出文件”混合模式。

### 11.1 输出目录

以平台划分：

- `data/bilibili/output/`
- `data/douyin/output/`

常见输出类型：

- 主结果 CSV
- 全量视频 CSV
- 视频时长分析 CSV
- Markdown 报告
- 评分 CSV
- 精简汇总 CSV

### 11.2 状态目录

以平台划分：

- `data/bilibili/state/`
- `data/douyin/state/`

常见状态类型：

- `progress.json`
- followings cache
- duration progress
- export store sqlite
- rating store sqlite
- GUI 配置状态
- fetch manifest

### 11.3 导出库：`common/export_store.py`

用途：

- 把面向飞书/报表的“当前表”写入 SQLite
- 维护快照历史

特点：

- 使用“当前表 + `_sheet_snapshots` + `_sheet_current_meta`”结构
- 支持按内容 hash 判断是否需要新增快照
- 某些高频变化表默认禁用快照历史

适用场景：

- 保留最近几次报表快照
- 给 GUI 或后续接口提供结构化查询来源

### 11.4 平台状态库：`common/platform_store.py`

用途：

- 保存平台级原始创作者行、视频行、视频状态、缓存状态和摘要数据

每个平台会创建这类表：

- `{platform}_creator_raw`
- `{platform}_video_raw`
- `{platform}_video_state`
- `{platform}_cache_state`
- `{platform}_summary_current`

这部分更像“分析缓存标准化层”。

### 11.5 仓储封装：`common/repositories.py`

`AnalyzerCacheRepository` 的定位是：

- 兼容旧缓存结构
- 对外提供更稳定的读写接口
- 逐步把分析代码从“直接操作原始 dict”迁移到“更可控的仓储边界”

它是一个迁移中的中间层，不是完整 DDD 仓储体系。

## 12. 配置与环境变量

### 12.1 配置加载

两个平台都通过 `config.py` 从 `.env` 和默认路径加载配置：

- `bilibili_analyzer/config.py`
- `douyin_analyzer/config.py`

它们定义了：

- API / 浏览器参数
- 输出路径
- 状态文件路径
- SQLite 路径
- 节流、重试、冷却时间
- 飞书上传参数

### 12.2 后端如何把请求参数传给业务层

`backend/task_runner.py` 会把任务请求转换成环境变量，例如：

- `DOUYIN_BROWSER_BACKEND`
- `ANALYSIS_MODE`
- `ENABLE_VIDEO_DURATION_ANALYSIS`
- `BILIBILI_FETCH_ORDER_BY`
- `DOUYIN_FETCH_ORDER_BY`

这说明当前“后端请求参数 -> 业务运行参数”的桥接方式，主要还是**环境变量注入**。

这是当前架构的一个关键事实：

- 业务层配置读取仍然以 `config.py + os.getenv()` 为主。
- 后端不是直接把复杂配置对象一路显式传下去。

## 13. 典型调用流程

### 13.1 桌面 GUI 发起抖音分析

```text
用户点击“开始运行”
-> gui.py 组装 RunConfig
-> gui_backend_client.payload_from_config()
-> POST /api/jobs
-> backend.task_runner 根据 JobKind 分发
-> douyin_analyzer.app.run_analysis(...)
-> 分析器读缓存 / 浏览器抓取 / 写结果
-> stdout/stderr 被写入 job logs
-> GUI 轮询任务状态与日志并刷新进度条
```

### 13.2 Web 页面发起任务

```text
用户点击“启动任务”
-> frontend/index.html 发送 POST /api/jobs
-> backend 创建任务
-> 浏览器周期性轮询 /api/jobs 和 /events
-> 页面展示状态和日志
```

### 13.3 旧 CLI 发起任务

```text
main.py
-> 根据菜单选择平台和动作
-> 直接调用 bilibili_analyzer.app / douyin_analyzer.app
-> 业务逻辑直接运行
```

## 14. 功能地图

下面按“用户能做什么”来理解系统，而不是按代码目录。

### 14.1 分析类功能

- B 站停更分析
- 抖音停更/活跃分析
- B 站与抖音串行联合分析

### 14.2 数据采集类功能

- B 站 UID 全量视频抓取
- 抖音 UID 全量视频抓取
- 抖音喜欢视频缓存
- 抖音关注列表缓存与详情抓取

### 14.3 清理/维护类功能

- 抖音取消关注
- 抖音非当前关注缓存清理
- 抖音状态重置候选视图
- 抖音归档/恢复相关数据视图
- progress 与视频状态表同步

### 14.4 评分与输出类功能

- 抖音视频评分
- 抖音创作者评分
- 抖音精简 CSV 导出
- 飞书上传

### 14.5 展示与运维类功能

- GUI 配置默认值加载
- GUI 元数据协议
- 任务队列与状态轮询
- 日志聚合
- 健康检查

## 15. 哪些文件是“扩展点”

### 15.1 新增一个后端任务

通常需要改这些位置：

1. `backend/job_models.py`
2. `backend/task_runner.py`
3. 具体业务实现文件
4. 如果 GUI 需要用到，再改：
   - `gui_models.py`
   - `gui_backend_client.py`
   - `gui.py`
   - `backend/gui_schema.py`

### 15.2 新增一个 GUI 参数

通常需要改：

1. `backend/task_runner.py` 中的运行时字段映射
2. `backend/gui_schema.py` 中的 UI 元数据
3. `backend/config_defaults.py` 中的默认值输出
4. `gui.py` 中的控件与取值
5. `gui_models.py` 中的 `RunConfig`

### 15.3 新增一个数据查询接口

通常需要改：

1. `backend/gui_data.py` 或新增同类查询模块
2. `backend/api.py`
3. 若桌面 GUI 需要展示，再改 `gui_backend_client.py` 和 `gui.py`

### 15.4 新增一个平台级持久化表

优先看：

1. `common/platform_store.py`
2. `common/export_store.py`
3. `common/repositories.py`

## 16. 重要架构事实与开发注意事项

### 16.1 当前是“渐进重构”状态，不是完全重写

从代码边界可以看出，项目正在从“脚本直跑”迁移到“后端 API + GUI/Web”模式：

- 老入口仍在：`main.py`
- 新边界已建立：`backend/`
- 业务层还大量依赖环境变量和历史缓存格式

理解这一点很重要，因为你会看到：

- 一部分逻辑很工程化
- 一部分逻辑仍保留脚本式结构

这是当前架构演进过程中的正常现象。

### 16.2 GUI 已尽量后端化，但没有完全薄到只剩渲染

`gui.py` 仍然比较大，包含很多界面状态和线程控制代码。

但当前边界已经明确：

- 业务数据来源尽量走后端 API
- 长任务尽量走 `backend.job_models.JobKind + backend.task_runner`

后续开发应该继续沿这个方向，而不是把业务逻辑重新塞回 GUI。

### 16.3 抖音能力是系统复杂度最高的部分

原因包括：

- 浏览器自动化
- 多抓取模式
- 缓存与进度恢复
- 评分体系
- 归档与状态维护

如果新增跨平台能力，建议先在 B 站链路验证结构，再迁移到抖音链路。

### 16.4 不要随意打破 `douyin-downloader-main` 的隔离边界

该目录是独立子项目。

当前主系统的设计意图是：

- 主分析系统维护自己的 API、GUI、缓存和评分链路
- 下载器子项目保持隔离

除非需求明确，否则不要把主系统核心能力直接改成依赖该子项目内部实现。

## 17. 推荐的后续重构方向

下面这些不是“当前已完成事实”，而是从现有代码边界推导出的合理演进方向：

1. 把 `task_runner` 的环境变量桥接逐步替换为显式配置对象传递。
2. 把取消机制从全局 stop flag 改成 job 级取消令牌。
3. 继续缩小 `gui.py`，把更多查询与规则迁移到后端。
4. 为 `backend/gui_data.py` 中的查询逻辑补更多测试。
5. 逐步统一 B 站与抖音侧缓存/状态表的数据模型。

## 18. 总结

从整体上看，这个系统已经具备比较清晰的分层：

- `gui.py / frontend/index.html` 负责交互
- `backend/` 负责 API、任务编排和 GUI 协议
- `bilibili_analyzer/` 与 `douyin_analyzer/` 负责核心业务
- `common/` 负责共享存储与运行控制
- `data/` 与 `runtime/` 承载实际运行状态

如果后续要继续开发，最重要的原则是：

- 新功能优先接入 `backend/` 任务边界
- GUI 尽量只做展示和触发
- 数据落盘优先复用 `common/` 中已有的状态与导出能力
- 不要把临时脚本式逻辑反向扩散回主交互层

如果你是 AI / Agent，可以把本文件当作“项目导航页”，再按第 2 节的顺序深入具体代码。
