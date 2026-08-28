# Code Production Evaluation Report

## 基本信息

| 项目 | 值 |
|------|-----|
| Git URL | https://codehub-dg-y.huawei.com/GeneralRDTools/CodeBenchRepos.git |
| 分支 | 26_ExperienceAssuranceService_0222e644 |
| Base Commit | 0222e6444d6c7d7d93a8ba5d37884ef3f28438fe |
| 评测时间 | 2026-08-27T12:41:20+08:00 |
| 评测等级 | **不合格** |

## 需求描述

ExperienceAssuranceService新增查询所有实例ID与当前实例ID功能。实现`InstanceUtils`工具类：
- `queryAllInstanceIds()`: 通过IR接口获取所有实例ID
- `queryCurrentInstanceId()`: 通过环境变量POD_NAME获取当前实例ID
- `parseInstanceId(String)`: 解析实例名称获取整数ID

## 代码生成情况

| 检查项 | 结果 |
|--------|------|
| 文件生成 | 1/1 文件已生成 |
| 多余文件 | 无 |
| 缺失文件 | 无 |
| 格式问题 | 无 |

**生成的文件：**
- `implements/common/src/main/java/com/huawei/oss/wireless/experienceassuranceservice/common/impl/utils/InstanceUtils.java`

## 编译结果

### 状态: **失败**

**编译错误详情：**

| 行号 | 错误 | 说明 |
|------|------|------|
| 52 | `StringUtil.isNullOrEmpty()` cannot find symbol | StringUtil类只有`isEmpty()`方法 |
| 59 | `StringUtil.isNullOrEmpty()` cannot find symbol | StringUtil类只有`isEmpty()`方法 |
| 85 | `StringUtil.isNullOrEmpty()` cannot find symbol | StringUtil类只有`isEmpty()`方法 |
| 102 | `RestCaller.builder()` cannot find symbol | RestCaller使用`new Builder(url, method)`而非静态builder() |
| 121 | `StringUtil.isNullOrEmpty()` cannot find symbol | StringUtil类只有`isEmpty()`方法 |
| 129 | `StringUtil.isNullOrEmpty()` cannot find symbol | StringUtil类只有`isEmpty()`方法 |

## API不匹配问题

### 1. StringUtil API 错误（严重）

AI生成的代码使用了`StringUtil.isNullOrEmpty()`方法，但该项目的`StringUtil`类只有`isEmpty()`方法。

**AI生成（错误）：**
```java
if (!StringUtil.isNullOrEmpty(backendList) && backendList.startsWith(ALL_INSTANCES_PREFIX)) {
```

**正确写法：**
```java
if (!StringUtil.isEmpty(backendList) && backendList.startsWith(ALL_INSTANCES_PREFIX)) {
```

共 **6处** 此错误。

### 2. RestCaller API 错误（严重）

AI生成的代码使用了`RestCaller.builder()`静态方法，但实际API是`new RestCaller.Builder(url, method)`构造器模式。

**AI生成（错误）：**
```java
Map<String, List<String>> respHeaderMap = RestCaller.builder()
    .url(POLICY_URL)
    .method(RestMethod.POST)
    .addHeader(BACKEND_LIST_POLICY_HEADER, INSTANCE_ONLY_POLICY)
    .call()
    .getHeaderMap();
```

**正确写法：**
```java
Map<String, String> respHeaderMap = new RestCaller.Builder(POLICY_URL, RestMethod.POST)
    .header(BACKEND_LIST_POLICY_HEADER, INSTANCE_ONLY_POLICY).build().call().getRespHeaderMap();
```

### 3. 响应头Map类型不匹配

AI代码使用`Map<String, List<String>>`作为响应头Map类型，而测试代码mock的是`RestfulResponse.getRespHeaderMap()`返回`Map<String, String>`，导致测试无法通过。

### 4. getBackendList方法设计差异

- **参考patch**: `getBackendList(Map<String, String> respHeaderMap)` - 接收respHeaderMap参数，进行case-insensitive查找
- **AI生成**: `getBackendList()` - 不接受参数，内部发起HTTP请求

## DT执行结果

| 检查项 | 结果 |
|--------|------|
| test_patch应用 | **未执行**（编译失败） |
| 测试类数量 | 0 |
| 总测试数 | 0 |
| 通过数 | 0 |
| 失败数 | 0 |
| 通过率 | **0%** |

## 评测结论

**等级：不合格（0%）**

### 问题总结

1. **API幻觉**：AI生成了6处`StringUtil.isNullOrEmpty()`调用，而该API在项目中不存在
2. **API幻觉**：AI生成了错误的`RestCaller.builder()`链式调用，实际为`new RestCaller.Builder(url, method)`模式
3. **类型不匹配**：响应头Map类型错误 (`Map<String, List<String>>` vs `Map<String, String>`)
4. **方法设计偏差**：`getBackendList()`方法的参数和返回值类型与测试代码不兼容

### 根本原因

AI未能正确识别项目已有的第三方库API，基于通用经验"猜测"了API的调用方式。这在AI代码生成中是常见问题（API幻觉），但导致生成的代码完全无法编译。

### 代码结构评价

尽管编译失败，但AI生成的代码在结构层面表现尚可：
- 类名、包名、常量定义正确
- 公共方法签名正确
- 异常处理逻辑合理
- 代码组织清晰

但关键的API调用错误使得代码完全不可用，需要大量手动修复才能使用。

## 报告存储位置

- JSON报告: `/root/.local/share/opencode/log/evaluation_report_20260827124120.json`
- Markdown报告: `/root/.local/share/opencode/log/evaluation_report_20260827124120.md`
