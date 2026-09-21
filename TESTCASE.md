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

## 7. POST /api/review - 文本审校（复核）

**描述：** 调用本地大模型对文本进行审校复核。可单独传入 `text`（对全文主动审校、找出漏报），或一并传入 `typos` / `sensitive_hits`（对工具结果逐条复核、剔除误报，并补充漏报）。

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
  "suggestions": "（模型输出的原始复核文本，含【复核结论】与【漏报】）",
  "issues": [
    {
      "type": "grammar",
      "original": "错别字",
      "corrected": "错字",
      "line": 1, "start": 0, "end": 0,
      "suggestion": "语法/语义修正：建议改为「错字」",
      "verdict": "grammar_confirmed"
    }
  ],
  "error": null
}
```

> 说明：`suggestions` 为模型原始输出（供人工查看）；`issues` 为结构化复核结论，每项带 `verdict`（typo_confirmed / typo_rejected / sensitive_confirmed / sensitive_rejected / grammar_confirmed）。当同时传入 `typos` / `sensitive_hits` 时，issues 会包含工具发现的逐条复核结果与模型主动发现的漏报项。

**预期响应（模型不可达）：**
```json
{
  "model": "qwen3.5:9b",
  "reachable": false,
  "suggestions": "",
  "issues": [],
  "error": "Connection refused"
}
```

---

## 8. POST /api/pipeline - 全流程处理

**描述：** 串联纠错 → 敏感词 → 审校（复核）三步流程；可选「二次校验」(`enable_audit`) 与「复核开关 / 最终修复」(`enable_final_suggestion`)。

**请求 - 完整流程（开启复核开关）：**
```http
POST /api/pipeline HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "text": "你号，这里有一个炸弹",
  "enable_audit": false,
  "enable_final_suggestion": true
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
  "issues": [
    {
      "type": "typo",
      "original": "你号",
      "corrected": "你好",
      "line": 1, "start": 0, "end": 2,
      "suggestion": "确认修改：建议改为「你好」",
      "verdict": "typo_confirmed"
    },
    {
      "type": "sensitive",
      "word": "炸弹",
      "category": "涉枪涉爆",
      "line": 1, "start": 5, "end": 7,
      "suggestion": "确认敏感词：删除（描述购买行为）",
      "verdict": "sensitive_confirmed"
    }
  ],
  "review_suggestions": "（review 步骤的原始输出文本）",
  "audit": null,
  "final_suggestion": "你好，这里有一个炸弹"
}
```

**请求 - 关闭复核开关（仅返回复核意见）：**
```http
POST /api/pipeline HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "text": "你号，这里有一个炸弹",
  "enable_final_suggestion": false
}
```

**预期响应：**
```json
{
  "original": "你号，这里有一个炸弹",
  "corrected_text": "你好，这里有一个炸弹",
  "has_issues": true,
  "typos": [ { "line": 1, "start": 0, "end": 2, "original": "你号", "corrected": "你好" } ],
  "sensitive_words": [ { "word": "炸弹", "category": "涉枪涉爆", "line": 1, "start": 5, "end": 7 } ],
  "issues": [ { "type": "typo", "original": "你号", "corrected": "你好", "line": 1, "start": 0, "end": 2, "suggestion": "确认修改：建议改为「你好」", "verdict": "typo_confirmed" } ],
  "review_suggestions": "（review 步骤的原始输出文本）",
  "audit": null,
  "final_suggestion": null
}
```

**请求 - 开启二次校验（enable_audit=true）：**
```http
POST /api/pipeline HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
  "text": "你号，这里有一个炸弹",
  "enable_audit": true
}
```

**预期响应（差异点）：** 与上面相比，`audit` 字段不再为 `null`，而是包含二次校验结果 `audit_suggestions`（按 issue 序号映射的复核结论）；`issues` 中每条的 `suggestion` / `verdict` 会被二次校验结果覆盖。

---

## 9. 长文本多场景压测（3000–6000 字）

> 目的：验证 review / pipeline 在真实长文档下的稳定性与质量，暴露并修复提示词与配置问题。
> 不是单元测试，需要本地 Ollama 可达（`qwen3.5:9b`，端口 11434）；MacBert 纠错与 Aho-Corasick
> 敏感词因依赖原生库不在测试沙箱内运行，由 harness 注入等价「工具检测结果」驱动真实 pipeline 代码。

**测试样例（`tests/longtext/`，每个 3000–3058 字）：**
| 文件 | 场景 | 植入问题 |
|------|------|----------|
| `wechat_article.txt` | 公众号文章（个人成长） | 4 错别字（即然/在也不能/变的/密诀）+ 2 处语义重复（大约…左右、目的…为了） |
| `news_report.txt` | 新闻报道（法治） | 4 错别字（讯速/布署/防犯/做案）+ 炸药(实体敏感,应确认)/兼职(误报,应拒) + 2 处冗余（据…介绍显示、约…余） |
| `novel_chapter.txt` | 小说章节（都市） | 3 错别字（按奈/飘渺/松驰） |
| `tech_blog.txt` | 技术博客（网关复盘） | 3 错别字（参于/部属/掉用）+ 破解(语境敏感,应拒) + 1 处缺主语（通过…从而…使） |

**驱动方式（`scripts/longtext_test.py`）：** 读取 `samples_meta.json` 中植入的错别字/敏感词，自动定位位置并构造
`ErrorLocation` / `SensitiveHit`，用轻量 fake 注入 `TextPipeline`，对每篇跑 `pipeline.run(enable_final_suggestion=True)`
（走 工具→复核→最终修复 全链路）与一次独立 `reviewer.review(text)`（纯漏报扫描），结果写入 `tests/longtext/report.json`。

**验证结论（temp=0.2，num_ctx=16384，max_tokens=8192，串行执行）：**
- 基础设施正常：4 篇均无上下文溢出、无报错；最终修复文本完整保留（长度比 ~1.0，首尾锚点 ok），长文本不截断。
- 明确错别字全部 `typo_confirmed`（讯速/布署/防犯/做案/参于/部属/掉用/按奈/松驰/即然/在也不能/变的/密诀）。
- 敏感误报可识别：中性/比喻用法的「兼职」「破解」判 `非敏感`（误报剔除）正确。
- 仍存在的边界/局限（属 LLM 不确定性 + 策略取舍，非代码 bug）：
  1. `飘渺→缥缈` 被模型视作通用异形词而 `typo_rejected`：语言学上可接受，提示词难稳定纠正。
  2. 新闻中「炸药」被 `sensitive_rejected`：属「报道确需提及」的误报判定，对新闻合理；但若部署要求
     任何敏感实质都不出现在输出，应在最终修复环节对 `sensitive_confirmed` 之外也做策略性兜底（见 README 长文本说明）。
  3. `破解` 在多次运行间偶有 `sensitive_confirmed` / `sensitive_rejected` 抖动：语境类敏感词判定受 temperature 影响，
     已下调 `temperature=0.2` 降低抖动，高利害场景建议开启 `enable_audit` 二次校验。
  4. 漏报（语法/语义）召回偏弱且不稳定（每篇 0–2 条），植入的「大约…左右」「据…介绍显示」「通过…从而…使」
     等语病常被漏检：当前为尽力而为，强依赖模型；若需稳定语法检查，建议后续接规则/专用语法模型做独立 pass。


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
  "issues": [],
  "review_suggestions": "（review 步骤的原始输出文本）",
  "audit": null,
  "final_suggestion": null
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
