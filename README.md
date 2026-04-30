# AI Agent 演示平台

基于 Claude API 的 AI Agent 全流程演示，展示真实 Agent 的各个核心模块如何协同工作。

## 功能概览

- 实时对话，流式 SSE 响应
- 可视化 Pipeline，每个模块的状态实时更新
- 工具调用（Function Call）：知识库检索、实时新闻、数学计算、时间查询
- 三层记忆体系：Short-term / Long-term / Memory Bank
- 输入输出双向安全护栏
- 任务规划（CoT / ReAct / Plan-and-Execute）
- 回答质量自动评估

---

## 目录结构

```
chip/
├── server.py              # FastAPI 服务器，Pipeline 串联入口
├── index.html             # 前端界面
├── requirements.txt       # Python 依赖
├── memories.json          # 持久化记忆（运行后自动生成）
├── memory_bank.json       # Memory Bank 文档库（运行后自动生成）
└── modules/
    ├── memory.py          # 三层记忆体系
    ├── memory_bank.py     # Memory Bank（知识文档库）
    ├── rag.py             # 静态知识库 + 检索函数
    ├── news.py            # 实时 RSS 新闻抓取
    ├── guardrails.py      # 输入/输出安全护栏
    ├── tools.py           # 工具定义与执行
    ├── planning.py        # 任务类型分析
    ├── prompt.py          # 系统提示词构建
    └── evaluation.py      # 回答质量评估
```

---

## 快速开始

### 环境要求

- Python 3.11+
- Anthropic API Key（[获取地址](https://console.anthropic.com/)）

### 安装依赖

```bash
pip install -r requirements.txt
```

### 启动服务器

```bash
python server.py
```

### 访问界面

浏览器打开 [http://localhost:3000](http://localhost:3000)，点击右上角输入 API Key 即可使用。

---

## Pipeline 流程

每次对话按以下顺序执行：

```
用户输入
  │
  ├─ Step 1  Guardrails     输入安全检查（拦截注入攻击、危险指令）
  ├─ Step 2  Memory         三层记忆检索，结果注入 Prompt
  ├─ Step 3  Prompt         构建系统提示词
  ├─ Step 4  Context        统计上下文窗口占用
  ├─ Step 5  Planning       判断任务类型（CoT / ReAct / Plan-and-Execute）
  ├─ Step 6  Orchestrator   复杂任务时启动，调度 SubAgent（按需）
  ├─ Step 7  LLM 循环       推理 → 工具调用 → 工具执行 → 再推理（最多 5 轮）
  ├─ Step 8  Guardrails     输出安全检查（过滤敏感内容）
  ├─ Step 9  Memory 更新    自动提取会话事实
  └─ Step 10 Evaluation     回答质量评分
```

---

## 模块说明

### Memory（三层记忆）

| 层级 | 实现 | 存储内容 | 生命周期 |
|------|------|----------|----------|
| Short-term (RAM) | Python dict | 对话历史、临时事实 | 会话结束清空 |
| Long-term (HDD) | `memories.json` | 个人事实、用户偏好 | 永久持久化 |
| Memory Bank (HDD) | `memory_bank.json` | 知识文档、长内容 | 永久 + 语义检索 |

检索机制：TF-IDF 余弦相似度，支持中文单字 + bigram 分词。

### Tools（可用工具）

| 工具 | 触发场景 | 说明 |
|------|----------|------|
| `search_knowledge_base` | 询问 AI Agent 相关概念 | 检索内置知识库（17 个 Agent 模块定义） |
| `get_news` | 询问今日新闻、最新资讯 | RSS 抓取实时新闻，支持 general / technology / business / world / science |
| `calculate` | 涉及数学计算 | 安全表达式求值，防注入 |
| `get_datetime` | 询问时间日期 | 返回当前日期时间 |
| `save_to_memory` | 要求记住简短事实 | 写入 Long-term Memory |
| `add_to_memory_bank` | 要求记住较长文档 | 写入 Memory Bank，建立向量索引 |
| `search_memory_bank` | 询问之前存入的文档 | TF-IDF 语义检索 Memory Bank |

### Guardrails（安全护栏）

**输入护栏** 拦截：
- 数据库/文件系统破坏性操作（`rm -rf`、`DROP TABLE` 等）
- 提示词注入攻击（`ignore all instructions`、角色覆盖等）

**输出护栏** 过滤：
- API Key 泄露（`sk-ant-api` 等）
- 疑似密码信息

### Planning（任务规划）

根据用户输入自动选择推理模式：

| 模式 | 触发关键词 | 特点 |
|------|-----------|------|
| CoT / ReAct | 普通问题 | LLM 自主决定是否调用工具 |
| Plan-and-Execute | 分析、报告、制定、规划、总结… | 启用 Orchestrator + SubAgent |

### Evaluation（质量评估）

自动评分维度：
- 基础分 0.72
- 回答长度 > 80 字：+0.05
- 回答长度 > 250 字：+0.05
- 有工具调用：+0.10
- 使用列表/加粗等结构化格式：+0.03
- 上限 0.99（满分留给人工审核）

---

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/chat` | 主对话接口，返回 SSE 流 |
| `GET` | `/api/memories` | 查看 Long-term Memory 内容 |
| `GET` | `/api/memory-bank` | 查看 Memory Bank 所有文档 |

### POST /api/chat 请求体

```json
{
  "message":   "你好",
  "sessionId": "user-123",
  "apiKey":    "sk-ant-..."
}
```

---

## 网络说明

如果处于企业网络环境（SSL 拦截），服务器已内置 `verify=False` 绕过自签名证书验证，无需额外配置。

新闻功能依赖以下 RSS 源（在企业网络中已验证可用）：

| 类别 | 来源 |
|------|------|
| general | Hacker News Front Page |
| technology | MIT Technology Review |
| business | Hacker News Best |
| science | Nature |
| world | Hacker News Front Page |

---

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端框架 | FastAPI + Uvicorn |
| LLM | Claude Haiku（`claude-haiku-4-5-20251001`） |
| 流式响应 | Server-Sent Events (SSE) |
| 向量检索 | TF-IDF 余弦相似度（纯 Python 实现） |
| HTTP 客户端 | httpx（同步 + 异步） |
| 持久化 | JSON 文件 |
