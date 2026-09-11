# 代码生产评测报告

## 基本信息

| 项目 | 内容 |
|------|------|
| 代码仓 | https://codehub-dg-y.huawei.com/GeneralRDTools/CodeBenchRepos.git |
| 分支 | 2_DataSmartCubeService_ece9bf60 |
| 基线版本 | ece9bf60d190e3d99b5855c06229fc59898bb97c |
| 评测时间 | 2026-09-11 16:52:41 |

## 需求描述

1. **模板名称唯一性校验**：创建/修改模板时校验名称唯一性，修改时排除自身
2. **已发布模板修改限制**：状态为"已发布"的模板禁止直接修改
3. **错误码配置**：新增 TEMPLATE_NAME_EXISTS 错误码（中英文）
4. **数据库查询**：新增按模板名称查询的 DAO/Mapper/SQL

## 代码生成情况

| 检查项 | 结果 |
|--------|------|
| 应生成文件数 | 7 |
| 实际生成文件数 | 7 |
| 多余文件 | 无 |
| 缺失文件 | 无 |
| 格式问题 | 无 |

### 生成的文件列表

1. `IntelligentTemplateDelegateImpl.java` - 添加名称校验和已发布检查逻辑
2. `CheckErrorCode.java` - 新增 TEMPLATE_NAME_EXISTS 常量
3. `ReportTemplateDao.java` - 新增 selectByTemplateName 方法
4. `ReportTemplateMapper.java` - 新增 selectByTemplateName 接口声明
5. `errorcode_en_US.properties` - 新增英文错误信息
6. `errorcode_zh_CN.properties` - 新增中文错误信息
7. `ReportTemplateMapper.xml` - 新增 selectByTemplateName SQL 查询

## test_patch 应用方式

| 应用方式 | 手动 apply |
|----------|-----------|
| 手动应用文件 | IntelligentTemplateDelegateImplTest.java, ReportTemplateDaoTest.java |

## 编译结果

| 阶段 | 状态 |
|------|------|
| 主代码编译 | SUCCESS |
| 测试代码编译 | SUCCESS |

## DT 执行结果

| 测试类 | 总数 | 通过 | 失败 | 跳过 |
|--------|------|------|------|------|
| IntelligentTemplateDelegateImplTest | 4 | 4 | 0 | 0 |
| ReportTemplateDaoTest | 2 | 2 | 0 | 0 |
| **合计** | **6** | **6** | **0** | **0** |

**通过率：100%**

### 通过的测试用例

| 测试方法 | 覆盖场景 |
|----------|---------|
| testGenerateReportTemplateWithNameAlreadyExists | 创建模板时名称重复抛出异常 |
| testModifyReportTemplateWithNameAlreadyExists | 修改模板时名称重复抛出异常 |
| testModifyReportTemplateWithNameUnchanged | 修改模板保持原名称正常通过 |
| testQueryReportTemplateWithOtherId | 查询不存在的模板返回null |
| testSelectByTemplateName | DAO按名称查询存在模板 |
| testSelectByTemplateNameNotFound | DAO按名称查询不存在模板返回null |

## 评测结论

**等级：优秀（通过率 >= 90%）**

AI 代码生成质量优秀，7个目标文件全部正确实现，编译一次通过，6个增量测试用例全部通过，功能覆盖完整。

## 报告文件

- JSON: `/root/.local/share/opencode/log/evaluation_report_20260911165241.json`
- Markdown: `/root/.local/share/opencode/log/evaluation_report_20260911165241.md`