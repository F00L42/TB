# 代码生产评测报告

## 基本信息

| 项目 | 值 |
|------|-----|
| 代码仓 | https://codehub-dg-y.huawei.com/GeneralRDTools/CodeBenchRepos.git |
| 分支 | 26_ExperienceAssuranceService_0222e644 |
| 基准Commit | 0222e6444d6c7d7d93a8ba5d37884ef3f28438fe |
| 评测时间 | 2026-09-09 16:54:23 |

## 需求描述

在ExperienceAssuranceService微服务中新增InstanceUtils工具类，提供以下功能：
- `queryAllInstanceIds()`：通过IR接口（POST /rest/oss/inner/experienceassurance/v1/policy）获取所有实例ID
- `queryCurrentInstanceId()`：通过POD_NAME环境变量获取当前实例ID

## 代码生成情况

| 检查项 | 结果 |
|--------|------|
| 应生成文件数 | 1 |
| 实际生成文件数 | 1 |
| 多余文件 | 无 |
| 缺失文件 | 无 |

### 生成文件列表

1. `implements/common/src/main/java/com/huawei/oss/wireless/experienceassuranceservice/common/impl/utils/InstanceUtils.java`

### 生成代码问题

| 序号 | 问题描述 | 严重程度 |
|------|----------|----------|
| 1 | `import com.huawei.oss.rest.util.RespHeaderMap` - 该类不存在，正确应为 `Map<String, String>` | 致命 |
| 2 | 未使用项目中的 `StringUtil.isEmpty()` 工具方法 | 一般 |
| 3 | `queryAllInstanceIds()` 异常处理逻辑与预期不同：捕获 ServiceException 后 rethrow 而非静默返回空列表 | 严重 |
| 4 | `queryCurrentInstanceId()` 缺少 try-catch 异常处理 | 严重 |
| 5 | `parseInstanceId()` 缺少 `StringUtil.isEmpty()` 判空 | 一般 |
| 6 | POLICY_URL 路径差异：`experienceassuranceservice` vs `experienceassurance` | 一般 |

## 编译结果

| 检查项 | 结果 |
|--------|------|
| 编译状态 | **失败** |
| 错误详情 | cannot find symbol: class RespHeaderMap (line 13) |

## 测试执行结果

| 检查项 | 结果 |
|--------|------|
| test_patch应用 | 未执行（编译失败） |
| DT执行 | 未执行（编译失败） |

## 评测结论

| 项目 | 值 |
|------|-----|
| 评测等级 | **不合格** |
| 失败步骤 | 步骤4：编译验证 |

### 总结

AI生成的代码存在致命编译错误：引入了不存在的 `RespHeaderMap` 类，导致编译完全失败。此外，代码逻辑与预期 patch 存在多处不一致，即使修复编译错误，测试用例也大概率会失败。主要问题包括异常处理策略错误（rethrow vs 静默返回）、缺少必要的 try-catch、URL路径不一致等。

### 已完成的步骤

1. 步骤0：参数解析与Settings文件保存
2. 步骤1：代码仓克隆与回退
3. 步骤2：Patch信息提取
4. 步骤3：AI代码生成

### 报告位置

- JSON报告：`/root/.local/share/opencode/log/evaluation_report_20260909165423.json`
- Markdown报告：`/root/.local/share/opencode/log/evaluation_report_20260909165423.md`
