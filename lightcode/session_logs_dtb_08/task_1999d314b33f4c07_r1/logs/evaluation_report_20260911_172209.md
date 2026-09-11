# 代码生产评测报告

## 基本信息

| 项目 | 内容 |
|------|------|
| 代码仓 | https://codehub-dg-y.huawei.com/GeneralRDTools/CodeBenchRepos.git |
| 分支 | 37_DVAIAgentService_11718ad6 |
| Base Commit | 11718ad6e7cc19909e969b988170555b87f8a63b |
| 评测时间 | 2026-09-11T17:22:09+08:00 |

## 需求描述

【分解给XOps技术组】【智能体】告警的分层标签支撑通用分析知识、领域分析知识，根因告警识别准确率提升

## Patch 文件清单

patch 共包含 **5** 个文件的变更：

| 序号 | 文件路径 | 类型 |
|------|----------|------|
| 1 | deployment/src/main/release/pub/knowledges/application.json | 配置文件 |
| 2 | service/src/main/java/.../IncidentServiceDelegateImpl.java | Java源文件 |
| 3 | service/src/main/resources/service-configure.properties | 配置文件 |
| 4 | service/src/test/java/.../intent/EsIndexUtilsTest.java | 测试文件 |
| 5 | service/src/test/java/.../intent/IndicesTest.java | 测试文件 |

ut_patch 共包含 **2** 个文件的变更：

| 序号 | 文件路径 | 类型 |
|------|----------|------|
| 1 | service/src/test/java/.../impl/ConfigLoadedTest.java | 测试文件 |
| 2 | service/src/test/resources/rest-stubs/mappings/index.json | 测试资源 |

## 代码生成情况

| 项目 | 结果 |
|------|------|
| AI生成文件数 | 3/5 |
| 多余文件 | 无 |
| 缺失文件 | EsIndexUtilsTest.java, IndicesTest.java |
| 格式问题 | 无 |

AI 生成的 3 个文件与预期 diff 完全一致：
- `application.json`：正确删除 `dv_knowledge_incident` 条目
- `IncidentServiceDelegateImpl.java`：正确修改变量值并删除 `FIELD_KNOWLEDGE_INDEX` 常量
- `service-configure.properties`：正确修改 `incident_case_knowledge` 值

**问题**：patch 分析阶段遗漏了 patch 中的 2 个测试文件（EsIndexUtilsTest.java, IndicesTest.java），AI 未被要求生成这些文件。

## test_patch 应用方式

- **方式**：git apply（成功）
- **手动 apply 文件**：无

## 编译结果

| 阶段 | 结果 |
|------|------|
| 主代码编译 (`mvn clean compile -DskipTests`) | SUCCESS |
| 测试代码编译 (`mvn test-compile`) | SUCCESS |

## DT 执行结果

| 测试类 | 测试方法 | 结果 |
|--------|----------|------|
| ConfigLoadedTest | testConfigLoaded | PASS |
| IndicesTest | testIndices | PASS |
| EsIndexUtilsTest | test_getEsIndex_whenNormalRequest_thenReturnIndexList | **FAIL** |

### 统计

| 指标 | 数值 |
|------|------|
| 总测试数 | 3 |
| 通过 | 2 |
| 失败 | 1 |
| 跳过 | 0 |
| **通过率** | **66.67%** |

### 失败详情

**EsIndexUtilsTest.test_getEsIndex_whenNormalRequest_thenReturnIndexList**

- **错误信息**：`expected:<32> but was:<0>` (Run 2-6); `expected:<[dv_knowledge_incident]> but was:<[archive_klg_index_dv]>` (Run 1)
- **根因**：EsIndexUtilsTest.java 未被生成/修改，测试文件仍期望旧索引名 `dv_knowledge_incident`，但代码已返回新值 `archive_klg_index_dv`。同时 mock 服务器的 index.json 已通过 ut_patch 更新为返回新索引名，导致后续 run 中结果为空。

## 评测结论

| 项目 | 结果 |
|------|------|
| **评级** | **合格** (通过率 66.67%，50% <= rate < 70%) |
| **总结** | AI 正确生成了 3 个主代码文件，diff 与预期完全一致。主代码和测试代码均编译通过。但由于 patch 分析阶段遗漏了 patch 内嵌的 2 个测试文件，导致 EsIndexUtilsTest 因断言值未更新而失败。最终 3 个测试中 2 个通过，通过率 66.67%。 |
