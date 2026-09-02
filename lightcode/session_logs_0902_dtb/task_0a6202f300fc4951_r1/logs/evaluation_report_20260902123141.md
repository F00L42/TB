# 代码生产评测报告

## 基本信息

| 项目 | 值 |
|------|-----|
| Git URL | https://codehub-dg-y.huawei.com/GeneralRDTools/CodeBenchRepos.git |
| 分支 | 2_DataSmartCubeService_ece9bf60 |
| Base Commit | ece9bf60d190e3d99b5855c06229fc59898bb97c |
| 评测时间 | 20260902123141 |

## 需求描述

模板名称唯一性校验和已发布模板修改限制功能：
1. 在创建新模板和修改现有模板时校验模板名称唯一性
2. 排除当前正在修改的模板本身
3. 已发布模板禁止直接修改
4. 新增 TEMPLATE_NAME_EXISTS 错误码
5. 新增按模板名称查询的DAO方法

## 代码生成情况

| 项目 | 结果 |
|------|------|
| 生成文件数 | 7/7 |
| 多余文件 | 无 |
| 缺失文件 | 无 |

### 代码问题

| 文件 | 行号 | 问题描述 |
|------|------|---------|
| IntelligentTemplateDelegateImpl.java | 73 | 使用了 `template.getBasicInfo().getStatus()` 应为 `template.getStatus()`，BasicInfo 类没有 getStatus() 方法 |
| IntelligentTemplateDelegateImpl.java | 82-85 | 缺少 `reportTemplateDao.selectByTemplateId(reportTemplatePO.getTemplateId())` 调用 |

## Test Patch 应用

| 项目 | 结果 |
|------|------|
| 应用方式 | 未应用 |
| 原因 | 主代码编译失败，未执行test_patch应用 |

## 编译结果

| 项目 | 结果 |
|------|------|
| 编译状态 | **失败** |
| 错误信息 | `cannot find symbol: method getStatus() location: class BasicInfo` |

### 错误详情

子agent 在 `generateReportTemplate` 方法中错误地调用了 `template.getBasicInfo().getStatus()`，
而 `ReportTemplate` 类本身就有 `getStatus()` 方法，不需要通过 `getBasicInfo()` 访问。
此外，还缺少 `reportTemplateDao.selectByTemplateId(reportTemplatePO.getTemplateId())` 调用。

## DT 执行结果

| 项目 | 值 |
|------|-----|
| 执行状态 | 未执行 |
| 总测试数 | 0 |
| 通过数 | 0 |
| 失败数 | 0 |
| 跳过数 | 0 |
| 通过率 | 0% |

## 评测结论

| 项目 | 结果 |
|------|------|
| **评级** | **不合格** |
| 失败步骤 | 步骤4：编译验证 |

**总结**：编译失败。子agent生成的代码存在2处错误：
1. `generateReportTemplate` 方法中错误地调用了 `template.getBasicInfo().getStatus()` 而非 `template.getStatus()`
2. 缺少 `reportTemplateDao.selectByTemplateId()` 调用

代码无法通过编译，未执行 test_patch 应用和 DT 测试。

## 报告位置

- JSON: `/root/.local/share/opencode/log/evaluation_report_20260902123141.json`
- Markdown: `/root/.local/share/opencode/log/evaluation_report_20260902123141.md`