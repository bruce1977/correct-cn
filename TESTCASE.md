# TESTCASE.md - API 测试用例文档

> 服务地址：`http://localhost:8000`  
> 认证方式：`Authorization: Bearer <api_key>` 或 `X-API-Key: <api_key>`  
> 除 `/health` 外，所有 API 端点都需要 API Key 认证

---

## 1. GET /health - 健康检查

**描述：** 仅供判断系统状态使用，无需认证。

**请求：**
```http
GET /health HTTP/1.1
Host: localhost:8000
```

**预期响应：**
```json
{}
```

---

## 2. POST /api/correct - 文本纠错

**描述：** 对输入文本进行错别字矫正，返回纠正结果和位置信息。需要 API Key 认证。

**请求：**
```http
POST /api/correct HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "text": "你号，我按装了一个新软件"
}
```

**预期响应：**
```json
{
  "original": "你号，我按装了一个新软件",
  "corrected": "你好，我安装了一个新软件",
  "errors": [
    {
      "line": 1,
      "start": 0,
      "end": 2,
      "original": "你号",
      "corrected": "你好"
    },
    {
      "line": 1,
      "start": 3,
      "end": 5,
      "original": "按装",
      "corrected": "安装"
    }
  ],
  "model": "shibing624/macbert4csc-base-chinese"
}
```

**测试用例 2 - 多行文本：**
```http
POST /api/correct HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "text": "第一行正常\n第二行有错别字"
}
```

**预期响应：**
```json
{
  "original": "第一行正常\n第二行有错别字",
  "corrected": "第一行正常\n第二行有错别字",
  "errors": [],
  "model": "shibing624/macbert4csc-base-chinese"
}
```

**测试用例 3 - 无错误文本：**
```http
POST /api/correct HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "text": "这是一段完全正确的文本"
}
```

**预期响应：**
```json
{
  "original": "这是一段完全正确的文本",
  "corrected": "这是一段完全正确的文本",
  "errors": [],
  "model": "shibing624/macbert4csc-base-chinese"
}
```

**错误响应（模型不可用）：**
```json
{
  "detail": "Correction model unavailable: <错误信息>"
}
```

---

## 3. POST /api/sensitive/check - 敏感词检测

**描述：** 扫描文本中的敏感词，返回命中结果。需要 API Key 认证。

**请求：**
```http
POST /api/sensitive/check HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "text": "今天买了一个炸弹"
}
```

**预期响应：**
```json
{
  "is_sensitive": true,
  "count": 1,
  "sensitive_words": [
    {
      "word": "炸弹",
      "category": "涉枪涉爆",
      "line": 1,
      "start": 5,
      "end": 7
    }
  ]
}
```

**测试用例 2 - 带分类过滤：**
```http
POST /api/sensitive/check HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "text": "炸弹和敏感词A",
  "categories": ["涉枪涉爆"]
}
```

**预期响应：**
```json
{
  "is_sensitive": true,
  "count": 1,
  "sensitive_words": [
    {
      "word": "炸弹",
      "category": "涉枪涉爆",
      "line": 1,
      "start": 0,
      "end": 2
    }
  ]
}
```

**测试用例 3 - 多行文本：**
```http
POST /api/sensitive/check HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "text": "今天买了一个炸弹\n他在网上卖了枪支"
}
```

**预期响应：**
```json
{
  "is_sensitive": true,
  "count": 2,
  "sensitive_words": [
    {
      "word": "炸弹",
      "category": "涉枪涉爆",
      "line": 1,
      "start": 5,
      "end": 7
    },
    {
      "word": "枪支",
      "category": "涉枪涉爆",
      "line": 2,
      "start": 5,
      "end": 7
    }
  ]
}
```

**测试用例 4 - 无敏感词：**
```http
POST /api/sensitive/check HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "text": "今天天气真好"
}
```

**预期响应：**
```json
{
  "is_sensitive": false,
  "count": 0,
  "sensitive_words": []
}
```

---

## 5. GET /api/sensitive/dictionaries - 字典列表

**描述：** 返回已加载的敏感词字典文件及其词数统计。

**请求：**
```http
GET /api/sensitive/dictionaries HTTP/1.1
Host: localhost:8000
```

**预期响应：**
```json
{
  "directory": "data/sensitive",
  "count": 15,
  "dictionaries": [
    {
      "file": "COVID-19词库.txt",
      "category": "COVID-19词库",
      "words": 150
    },
    {
      "file": "涉枪涉爆.txt",
      "category": "涉枪涉爆",
      "words": 437
    },
    {
      "file": "暴恐词库.txt",
      "category": "暴恐词库",
      "words": 178
    }
  ]
}
```

---

## 6. POST /api/sensitive/refresh - 刷新字典

**描述：** 从远程源刷新敏感词字典或重新加载本地字典。

**请求 - 远程刷新：**
```http
POST /api/sensitive/refresh HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "remote": true,
  "force": false
}
```

**预期响应：**
```json
{
  "updated": [],
  "failed": [],
  "categories": ["涉枪涉爆", "暴恐词库", "政治类型", "..."],
  "total_words": 3500
}
```

**请求 - 强制远程刷新：**
```http
POST /api/sensitive/refresh HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "remote": true,
  "force": true,
  "files": ["涉枪涉爆.txt"]
}
```

**预期响应：**
```json
{
  "updated": ["涉枪涉爆.txt"],
  "failed": [],
  "categories": ["涉枪涉爆", "暴恐词库", "政治类型", "..."],
  "total_words": 3500
}
```

**请求 - 本地重新加载：**
```http
POST /api/sensitive/refresh HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "remote": false
}
```

**预期响应：**
```json
{
  "updated": [],
  "failed": [],
  "categories": ["涉枪涉爆", "暴恐词库", "政治类型", "..."],
  "total_words": 3500
}
```

---

## 7. POST /api/review - 文本审校

**描述：** 调用本地大模型对文本进行语法/用词审校。

**请求：**
```http
POST /api/review HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "text": "这段文字有一些语病和错别字"
}
```

**预期响应（模型可达）：**
```json
{
  "model": "qwen3.5:9b",
  "reachable": true,
  "suggestions": "1. 问题列表：\n- 【问题】无明显错别字\n- 【位置】全文\n- 【修改建议】文本基本正确\n\n2. 优化后的完整文本：\n这段文字有一些语病和错别字",
  "error": null
}
```

**预期响应（模型不可达）：**
```json
{
  "model": "qwen3.5:9b",
  "reachable": false,
  "suggestions": "",
  "error": "Connection refused"
}
```

---

## 8. POST /api/pipeline - 全流程处理

**描述：** 串联纠错 → 敏感词 → 审校三步流程，返回综合结果。

**请求 - 完整流程：**
```http
POST /api/pipeline HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "text": "你号，这里有一个炸弹"
}
```

**预期响应：**
```json
{
  "original": "你号，这里有一个炸弹",
  "corrected_text": "你好，这里有一个炸弹",
  "has_issues": true,
  "typos": [
    {
      "line": 1,
      "start": 0,
      "end": 2,
      "original": "你号",
      "corrected": "你好"
    }
  ],
  "sensitive_words": [
    {
      "word": "炸弹",
      "category": "涉枪涉爆",
      "line": 1,
      "start": 5,
      "end": 7
    }
  ],
  "review": {
    "model": "qwen3.5:9b",
    "reachable": true,
    "suggestions": "整体建议：表达可以更通顺。",
    "error": null
  },
  "summary": {
    "model": "qwen3.5:9b",
    "reachable": true,
    "suggestions": "最终建议：请按上述修改。",
    "error": null
  },
  "final_suggestion": "最终建议：请按上述修改。"
}
```

**请求 - 禁用 summary：**
```http
POST /api/pipeline HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "text": "你号，这里有一个炸弹",
  "enable_summary": false
}
```

**预期响应：**
```json
{
  "original": "你号，这里有一个炸弹",
  "corrected_text": "你好，这里有一个炸弹",
  "has_issues": true,
  "typos": [
    {
      "line": 1,
      "start": 0,
      "end": 2,
      "original": "你号",
      "corrected": "你好"
    }
  ],
  "sensitive_words": [
    {
      "word": "炸弹",
      "category": "涉枪涉爆",
      "line": 1,
      "start": 5,
      "end": 7
    }
  ],
  "review": {
    "model": "qwen3.5:9b",
    "reachable": true,
    "suggestions": "整体建议：表达可以更通顺。",
    "error": null
  },
  "summary": null,
  "final_suggestion": "整体建议：表达可以更通顺。"
}
```

**请求 - 无问题文本：**
```http
POST /api/pipeline HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "text": "这是一段完全正常的文本"
}
```

**预期响应：**
```json
{
  "original": "这是一段完全正常的文本",
  "corrected_text": "这是一段完全正常的文本",
  "has_issues": false,
  "typos": [],
  "sensitive_words": [],
  "review": {
    "model": "qwen3.5:9b",
    "reachable": true,
    "suggestions": "整体建议：文本无问题。",
    "error": null
  },
  "summary": {
    "model": "qwen3.5:9b",
    "reachable": true,
    "suggestions": "最终建议：文本质量良好，无需修改。",
    "error": null
  },
  "final_suggestion": "最终建议：文本质量良好，无需修改。"
}
```

---

## 9. POST /api/keys/reload - 重载 API Key

**描述：** 重新加载 API 密钥文件，无需重启服务。

**请求：**
```http
POST /api/keys/reload HTTP/1.1
Host: localhost:8000
```

**预期响应：**
```json
{
  "loaded": 3
}
```

---

## 10. 认证测试

**描述：** 测试 API Key 认证机制。

**请求 - 无认证（已配置 Key）：**
```http
POST /api/correct HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "text": "测试"
}
```

**预期响应：**
```json
{
  "detail": "Invalid or missing API key"
}
```
**状态码：** 401

**请求 - Bearer Token 认证：**
```http
POST /api/correct HTTP/1.1
Host: localhost:8000
Content-Type: application/json
Authorization: Bearer test-key-123

{
  "text": "测试"
}
```

**预期响应：** 正常返回纠错结果

**请求 - X-API-Key Header 认证：**
```http
POST /api/correct HTTP/1.1
Host: localhost:8000
Content-Type: application/json
X-API-Key: test-key-123

{
  "text": "测试"
}
```

**预期响应：** 正常返回纠错结果

---

## 11. 错误场景测试

### 11.1 无效请求体

**请求：**
```http
POST /api/correct HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "wrong_field": "测试"
}
```

**预期响应：**
```json
{
  "detail": [
    {
      "loc": ["body", "text"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```
**状态码：** 422

### 11.2 模型不可用

**请求（corrector 未加载）：**
```http
POST /api/correct HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "text": "测试"
}
```

**预期响应：**
```json
{
  "detail": "Correction model unavailable: <错误信息>"
}
```
**状态码：** 503

### 11.3 空文本

**请求：**
```http
POST /api/correct HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "text": ""
}
```

**预期响应：**
```json
{
  "original": "",
  "corrected": "",
  "errors": [],
  "model": "shibing624/macbert4csc-base-chinese"
}
```

### 11.4 超长文本

**请求：**
```http
POST /api/correct HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "text": "测试文本，长度超过预期限制..."
}
```

**预期响应：** 正常处理或返回 413 Payload Too Large

---

## 12. 并发测试

**描述：** 使用并发工具测试多个请求同时处理。

```bash
# 使用 Apache Bench (ab) 进行并发测试
ab -n 100 -c 10 -p request.json -T application/json http://localhost:8000/api/correct
```

**预期：** 所有请求正常响应，无 500 错误

---

## 13. 性能基准

| 接口 | 目标响应时间 | 备注 |
|------|-------------|------|
| GET / | < 10ms | 无需计算 |
| GET /health | < 50ms | 轻量查询 |
| POST /api/correct | < 2s | 模型推理耗时 |
| POST /api/sensitive/check | < 100ms | Aho-Corasick 算法 |
| GET /api/sensitive/dictionaries | < 10ms | 缓存查询 |
| POST /api/sensitive/refresh | < 5s | 可能涉及网络请求 |
| POST /api/review | < 10s | LLM 推理耗时 |
| POST /api/pipeline | < 15s | 三步流程串联 |

---

## 测试数据说明

### 敏感词测试数据

- **涉枪涉爆类：** 炸弹、枪支、炸药、雷管
- **暴恐类：** 轮功、李洪志
- **政治类：** 敏感词A、敏感词B

### 纠错测试数据

- **常见错别字：** 你号→你好、按装→安装、克苦→刻苦、他门→他们
- **位置报告：** 支持多行文本，每行独立计算偏移量

### 测试环境配置

```bash
# 使用 mock 模式测试（无需 ML 模型）
export CORRECTOR_MODE=mock

# 使用真实模型测试
export CORRECTOR_MODE=model
```
