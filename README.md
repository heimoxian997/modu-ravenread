# 墨读 · RavenRead

本地离线运行的中文长篇小说阅读与伴读工具。书只存在本机，不上传；模型调用走你自己配置的接口。

## 功能

- **导入**：上传 txt，自动推断章节规则，确认边界后写入书库
- **切块**：按章生成父块与子块，供检索使用
- **索引**：本地嵌入模型生成向量，写入本机 Qdrant
- **抽取**：按章调用模型生成摘要、剧情事件、出场人物、世界观条目；支持批量、断点续跑、单章重抽
- **实体层**：从抽取结果重建人物表、别名表、称谓观测；可以按章查询「这个称呼在这个位置指谁」
- **阅读**：按段落渲染整章，记住阅读位置；点人名看候选，也可以读当前段落前后原文
- **检索**：摘要检索支持 RRF 融合，并可在页面上对比重排前后的名次变化

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | FastAPI、SQLAlchemy 2.0（同步 Session）、SQLite、Pydantic v2 |
| 向量 | 本机 Qdrant（本地目录模式） |
| 嵌入 | bge-m3；重排 bge-reranker-v2-m3，两者都懒加载 |
| 前端 | Vue 3、Vite、Element Plus |
| 模型接入 | 任意 OpenAI 兼容接口，密钥加密后存本机 SQLite |

## 目录

```
backend/app/api/        路由
backend/app/services/   业务逻辑
backend/app/models/     ORM 模型
backend/app/schemas/    请求与响应结构
backend/tests/          pytest 用例
frontend/src/views/     页面
data/                   运行时数据，不入库
```

## 启动

```bash
# 配置
cp .env.example backend/.env

# 后端依赖（不含 AI 重依赖）
pip install -r backend/requirements.txt

# 需要本地嵌入或索引时再装，包体积大，按需
pip install -r backend/requirements-ai.txt

# 起后端，工作目录为 backend
uvicorn app.main:app --reload

# 起前端
cd frontend && npm install && npm run dev
```

前端在 5173，`/api` 代理到 8000。

没装 `requirements-ai.txt` 也能跑上传、解析、阅读，只是建不了向量索引、用不了重排。CI 只安装 `requirements.txt`，`torch` 这类依赖不参与测试。

## 接口

| 前缀 | 用途 |
|---|---|
| `/api/drafts` | 上传、解析草稿、确认入库 |
| `/api/books` | 书库、章节正文、当前位置附近的原文 |
| `/api/books/{id}/chunks` | 切块生成、概览与查看 |
| `/api/books/{id}/text-index` | 向量索引任务与检索 |
| `/api/books/{id}/summaries` | 章节摘要、结构编辑、批量任务、索引检索 |
| `/api/books/{id}/entities` | 人物、别名、称谓候选、世界观 |
| `/api/settings` | 嵌入状态、模型服务配置与连通性测试 |

## 测试

```bash
cd backend && pytest tests -q
```

276 个用例。测试在导入应用前把 `DATA_DIR` 指到临时目录，每个用例前重建表，不会碰 `data/` 里的真实书库。

## 数据

`data/` 里是正文、SQLite、Qdrant 向量库、模型权重和加密密钥，整体不进版本库。删掉这个目录等于清空，靠代码恢复不了。

## 许可

MIT
