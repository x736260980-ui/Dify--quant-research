# Dify--quant-research

**量化投研 + 双平台舆情（B站 & 抖音）情绪分析** — 基于 Dify 工作流的多源交叉验证投研辅助工具。

将金融量化数据（AkShare 技术因子）与双平台大众情绪（B站弹幕/评论 + 抖音评论）、宏观资讯（Web 检索）深度融合，通过 LLM（DeepSeek）进行交叉验证，输出结构化、可视化的投研参考报告。

![工作流概览](https://github.com/user-attachments/assets/8695565d-9fde-459b-a077-262b36133e41)

---

## 系统架构

项目由 **四个独立微服务**（六个 API 端点）和一个 **Dify 工作流编排层** 组成：

```
                         Dify 工作流编排
  ┌──────────────────────────────────────────────────────────┐
  │   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐  │
  │   │ Web 检索  │  │ 量价因子  │  │ B站舆情   │  │抖音舆情 │  │
  │   └────┬─────┘  └────┬─────┘  └─────┬────┘  └───┬────┘  │
  │        └──────────────┼──────────────┼────────────┘       │
  │                       ▼              ▼                     │
  │            ┌──────────────────────────────┐               │
  │            │   LLM 交叉验证分析 (DeepSeek)  │               │
  │            └──────────────────────────────┘               │
  └──────────────────────────────────────────────────────────┘
         ↑              ↑              ↑              ↑
    8000 端口       8000 端口     8002/8003 端口   8005/8006 端口
   AkShare因子      联网搜索       B站并发探针      抖音探针
```

> **B站** 覆盖 Z 世代深度讨论，**抖音** 覆盖泛人群情绪扩散，双平台互补，舆情画像更完整。

---

## 微服务一览

| 服务 | 文件 | 端口 | 功能 |
|------|------|------|------|
| AkShare 因子计算 | [fast_api(akshare).py](fast_api(akshare).py) | 8000 | 股票/ETF/板块量价因子 + 趋势图 |
| B站 并发舆情探针 | [bilibili_ 评论api.py](bilibili_%20评论api.py) | 8002 | 批量并发获取元数据、弹幕、评论 |
| B站 搜索雷达 | [bilibili_ 搜索api.py](bilibili_%20搜索api.py) | 8003 | 关键词搜索，返回 BVID 与标题 |
| 抖音搜索 | [dy搜索api.py](dy搜索api.py) | 8005 | 浏览器自动化搜索抖音视频 |
| 抖音评论 | [dy评论api.py](dy评论api.py) | 8006 | a_bogus 签名协议获取评论 |

---

## 组件详情

### 1. 金融量价因子计算

**文件**: [fast_api(akshare).py](fast_api(akshare).py) · **Schema**: [akshare-schema.yml](akshare-schema.yml)

通过 AkShare 获取股票/ETF/板块的日线数据，计算核心技术因子并生成趋势图表。

**功能**:
- 前置本地缓存（1 小时失效），拦截高频重复请求
- 因子计算：**MACD、RSI(14)、MFI(14)、布林带**
- 自动生成含布林带的走势图（Matplotlib → `static/images/`）

```json
POST /api/finance/comprehensive_factors
{
  "symbol": "贵州茅台",
  "asset_type": "stock",
  "period_months": 3
}
```

**返回**: `analysis_summary`（诊断文本）、`raw_factors`（因子字典）、`trajectory`（收盘价轨迹）、`chart_url`（图表路径）

---

### 2. B站 并发舆情探针

**文件**: [bilibili_ 评论api.py](bilibili_%20评论api.py) · **Schema**: [bilibili_ 评论schema.yml](bilibili_%20评论schema.yml)

**核心改进**（相对旧版）：不再拆成三个端点逐个调用，而是合为一个批量并发接口。通过 `asyncio.gather` 同时对多个视频发起元数据、弹幕、评论请求，大幅提升数据采集效率。

```json
POST /api/bili/batch-full
{
  "bvids": ["BV1fqXZBoEty", "BV12LXgBqEbQ"],
  "max_limit": 200,
  "max_pages": 3
}
```

**返回**: 每个视频的 `meta`（基础指标）、`danmaku`（弹幕列表）、`comments`（结构化评论）

> 使用前需在文件中配置 B站 Cookie 凭证（SESSDATA、BILI_JCT、BUVID3）。

---

### 3. B站 搜索雷达

**文件**: [bilibili_ 搜索api.py](bilibili_%20搜索api.py) · **Schema**: [bilibili_ 搜索schema.yml](bilibili_%20搜索schema.yml)

供 LLM Agent 动态调用，根据关键词与排序策略搜索 B站视频。

| 参数 | 说明 |
|------|------|
| `keyword` | 搜索关键词 |
| `order_type` | `totalrank`（综合）/ `pubdate`（最新）/ `click`（最多点击） |
| `time_range` | 时长筛选：10（<10min）/ 20（10-30min）/ 40（30-60min）/ 70（>60min） |
| `page` | 页码 |

---

### 4. 抖音搜索

**文件**: [dy搜索api.py](dy搜索api.py) · **Schema**: [dy搜索schema.yml](dy搜索schema.yml)

基于 **DrissionPage** 接管本地 Chrome 浏览器，模拟用户滚动搜索抖音视频，拦截底层 API 返回数据。

- 自动启动 Chrome 远程调试模式（端口 9222）
- 滚动加载 + 网络包拦截，获取视频 ID、作者、点赞量、文案
- 遇到验证码时需手动在浏览器中完成

```json
POST /api/dy/search
{
  "keyword": "固态电池",
  "max_pages": 3
}
```

---

### 5. 抖音评论

**文件**: [dy评论api.py](dy评论api.py) · **Schema**: [dy评论schema.yml](dy评论schema.yml)

基于 **a_bogus 逆向签名** 直接请求抖音评论接口，不走浏览器。

- 启动时创建 Node.js 子进程池（3 进程）计算 a_bogus 签名
- 通过环境变量 `DY_COOKIES` 传入 Cookie 凭证
- 游标翻页，支持指定最大评论数

```json
POST /api/dy/comments
{
  "aweme_id": "7200000000000000000",
  "max_comments": 100
}
```

> **依赖**: 需要同级目录下的 `a_bogus_server.js`（签名计算进程）和 `.env` 文件（配置 `DY_COOKIES`）。

---

### 6. Dify 工作流编排

**文件**: [dify_file.yml](dify_file.yml)

在 Dify 平台完成量化数据与大模型推理的融合。工作流逻辑：

1. **主题确定** — LLM 提取用户意图，确定股票代码与搜索主题
2. **并行数据采集**（条件分支）：
   - **路径 A**：Web 检索 + AkShare 因子计算 → 技术面诊断
   - **路径 B**：B站搜索 → Agent 精选 3 个视频 → 迭代弹幕/评论/情绪分析
3. **交叉验证分析** — DeepSeek-reasoner 融合三个维度数据，输出结构化报告

**输出格式**：
- 核心预期差与战略定调
- 数据交叉验证分析（资金形态 + 事实情绪博弈）
- 实战推演与风险控制

> 各 FastAPI 保持独立部署，便于单独升级和复用。

---

## 快速开始

### 前置条件

- Python 3.8+
- Dify 平台（社区版或云版）
- B站 Cookie 凭证（仅舆情分析需要）
- 抖音 Cookie + `a_bogus_server.js`（仅抖音评论需要）
- Chrome 浏览器（仅抖音搜索需要）

### 安装

```bash
pip install fastapi uvicorn akshare pandas pandas-ta matplotlib bilibili-api-python DrissionPage requests python-dotenv
```

### 启动服务

```bash
# AkShare 因子服务（端口 8000）
python "fast_api(akshare).py"

# B站 并发舆情（端口 8002）
python "bilibili_ 评论api.py"

# B站 搜索（端口 8003）
python "bilibili_ 搜索api.py"

# 抖音搜索（端口 8005）— 会自动启动 Chrome
python "dy搜索api.py"

# 抖音评论（端口 8006）— 需要同级 a_bogus_server.js
python "dy评论api.py"
```

### 接入 Dify

1. 在 Dify 工作室中创建自定义工具
2. 导入对应的 OpenAPI Schema YAML 文件
3. 将工具节点接入工作流
4. 导入 [dify_file.yml](dify_file.yml) 获取完整工作流配置

> **Docker 部署注意**：容器内访问宿主机需使用 `http://host.docker.internal:8000`。抖音搜索依赖本地 Chrome，不适合容器化部署。

---

## 服务端口汇总

| 服务 | 说明 |
|------|------|
| AkShare 因子 / 也可部署联网搜索 | 可复用，注意冲突 |
| B站 并发舆情（batch-full） | 批量获取弹幕+评论 |
| B站 搜索雷达 | 关键词搜视频 |
| 抖音搜索 | 依赖本地 Chrome |
| 抖音评论 | 依赖 a_bogus 签名服务 |

---

## 依赖项

| 组件 | 关键库 |
|------|--------|
| 量化计算 | akshare, pandas, pandas-ta |
| 数据可视化 | matplotlib |
| API 框架 | fastapi, uvicorn, pydantic |
| B站接口 | bilibili-api-python |
| 抖音搜索 | DrissionPage (ChromiumPage) |
| 抖音评论 | requests, python-dotenv, Node.js (a_bogus) |
| 工作流编排 | Dify + DeepSeek LLM |

---

## 免责声明

本项目仅供**学术研究、分析逻辑验证及大模型工程化学习**使用。请严格遵守目标平台的相关协议与反爬虫规范，合理控制请求频次，禁止用于任何非法数据采集或商业牟利活动。

## 开源许可

[MIT License](LICENSE)
