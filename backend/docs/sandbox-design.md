# 沙箱与变更回传设计

## 目标

本设计用于约束当前项目中的高风险能力，例如：

- Shell 命令执行
- 文件创建、修改、删除
- 测试、构建、脚本运行
- 生成代码补丁或二进制产物

设计目标如下：

- 普通问答和只读分析不承担沙箱开销
- 高风险执行默认隔离，不直接操作宿主机
- 代码修改可以以 `diff/patch` 的形式安全回传
- 文档、图片、报告等二进制或产物文件可以白名单回传
- 沙箱生命周期清晰，可复用，可回收，可审计

## 非目标

本设计当前不覆盖以下内容：

- 远程多租户调度
- 分布式构建集群
- 联网依赖下载策略的细粒度放行
- 面向恶意攻击者的完全对抗级隔离

当前重点是把项目从“宿主机直接执行 + 权限确认”升级到“默认隔离执行 + 审计回传”。

## 设计原则

- `session != sandbox`
- 沙箱按需懒创建，不在创建 session 时预创建
- 宿主机工作区对沙箱默认只读
- 沙箱只在独立输出区写入
- 文本修改通过 `patch` 回传
- 非文本产物通过白名单文件清单回传
- 删除动作单独审批，不自动落地到宿主机
- 普通问答、只读代码分析不进入沙箱

## 总体架构

系统分为四层：

1. SessionManager
2. ToolRouter
3. SandboxManager
4. ResultSync Pipeline

### SessionManager

负责维护会话级上下文，不负责真正执行高风险操作。

建议会话状态包含：

- `session_id`
- `workspace_id`
- `sandbox_handle`
- `sandbox_state`
- `last_active_at`
- `approval_context`

初始状态下：

- `sandbox_handle = null`
- `sandbox_state = not_created`

也就是说，创建会话并不创建沙箱。

### ToolRouter

负责根据工具能力和参数决定执行路径。

核心职责：

- 判断当前调用是否只读
- 判断是否会写文件、删文件、运行命令、起进程
- 判断路径是否落在允许的工作区范围内
- 判断是否需要进入沙箱
- 判断是否需要审批

ToolRouter 的输出建议统一为：

- `route`
- `sandbox_profile`
- `approval_required`
- `result_mode`

其中：

- `route` 可取 `host_readonly`、`sandbox_edit`、`sandbox_exec`
- `sandbox_profile` 可取 `none`、`edit`、`exec`
- `approval_required` 表示是否需要用户确认
- `result_mode` 可取 `none`、`patch`、`artifact`、`patch_and_artifact`

### SandboxManager

负责沙箱的懒创建、复用、升级、回收和销毁。

核心职责：

- 首次高风险调用时创建沙箱
- 同一 session 内复用已有沙箱
- 在需要时升级沙箱能力
- 空闲超时自动回收
- 会话结束立即销毁

### ResultSync Pipeline

负责把沙箱中的结果安全带回宿主机。

核心职责：

- 生成变更清单
- 生成文本 `diff/patch`
- 生成产物白名单清单
- 做路径合法性检查
- 做冲突检测
- 应用 patch
- 接收产物

## 生命周期设计

### 1. Session 创建

只创建轻量会话上下文，不创建沙箱。

原因：

- 大量会话可能只做问答或只读分析
- 预创建沙箱会制造大量空容器
- 预创建工作副本会浪费 CPU、IO 和磁盘

### 2. 首次工具调用

由 ToolRouter 判定：

- 若为只读调用，则直接在宿主机只读环境执行
- 若为修改或执行类调用，则申请沙箱

### 3. 沙箱懒创建

当且仅当以下情况发生时创建沙箱：

- 第一次写文件
- 第一次运行 shell
- 第一次跑测试、构建、脚本
- 第一次需要生成 patch 或产物

### 4. 沙箱复用

同一 session 中，只要工作区未变化，就应复用同一个沙箱上下文。

原因：

- 避免每一步都重复准备环境
- 保留中间文件、测试缓存、构建缓存
- 减少重复复制工作区的开销

### 5. 沙箱回收

建议策略：

- 空闲 10 到 20 分钟自动回收
- 存活总时长 30 到 60 分钟自动回收
- session 结束立即销毁

## 沙箱形态设计

### 结论

不建议真正维护“两套彼此独立的沙箱实例”来分别服务编辑和执行。

更好的做法是：

- 逻辑上分为两种沙箱配置：`edit` 和 `exec`
- 物理上每个 session 同时只维护一个活动沙箱
- 当风险等级提升时，对现有沙箱做“能力升级”或“重建迁移”

这样不会出现两个沙箱同时争用同一 session 状态的冲突。

### edit 配置

适用于：

- 改代码
- 改配置
- 生成文档草稿
- 生成 patch

特点：

- 不允许运行任意 shell
- 默认禁网
- 允许访问只读输入工作区
- 允许写输出工作区

### exec 配置

适用于：

- 执行 shell
- 运行测试
- 构建项目
- 运行脚本
- 解析不可信输入

特点：

- 允许运行受控命令
- 默认禁网
- 更严格资源限制
- 仍然只允许读输入区、写输出区

### 是否会发生冲突

会出现“等级提升”问题，但不应出现“并行冲突”问题。

典型场景：

1. 当前 session 先进入 `edit` 模式
2. 后续用户又要求运行测试
3. 系统此时需要从 `edit` 升级到 `exec`

推荐处理策略：

- 若 `edit` 与 `exec` 底层运行时兼容，则直接升级权限配置
- 若不兼容，则保留输出工作区，销毁旧沙箱并基于现有输出副本重建 `exec` 沙箱

因此，系统要支持的是“单沙箱升级”，不是“多沙箱并存”。

### 推荐实现

建议一开始直接只实现一种统一运行时，只是通过不同 profile 控制能力：

- `profile = edit`
- `profile = exec`

这样实现最简单，也最不容易产生状态漂移。

## 工作区投影设计

沙箱不直接改宿主机源码目录。

推荐目录模型：

- `/input/workspace`：宿主机工作区，只读挂载
- `/output/workspace`：沙箱工作副本，可写
- `/output/artifacts`：沙箱产物输出目录，可写
- `/tmp`：临时目录，可写

### 输入区

只读挂载宿主机工作区。

作用：

- 给沙箱提供原始文件视图
- 作为生成 diff 的对照基线

### 输出区

所有实际修改都发生在 `/output/workspace` 中。

建议策略：

- 首次进入修改模式时，再创建输出工作副本
- 可以先复制整个项目，也可以按需复制

为了降低实现复杂度，第一版建议：

- 首次修改时复制整个工作区到 `/output/workspace`

后续再优化为按需复制。

## ToolRouter 设计

### 工具标签

每个工具应当具备一组能力标签：

- `read_only`
- `writes_files`
- `deletes_files`
- `executes_code`
- `runs_shell`
- `spawns_process`
- `uses_network`
- `produces_artifacts`
- `needs_workspace`

### 路由规则

#### host_readonly

适用于：

- 问答
- 只读文件读取
- 只读搜索
- 只读代码分析

要求：

- 不写文件
- 不起进程
- 不联网
- 不改系统状态

#### sandbox_edit

适用于：

- 写代码
- 改配置
- 改文本
- 生成 patch
- 生成普通产物

要求：

- 不直接写宿主机
- 改动只发生在 `/output/workspace`

#### sandbox_exec

适用于：

- shell
- 测试
- 构建
- 执行脚本
- 安装依赖

要求：

- 必进沙箱
- 默认需要审批

### 参数二次判定

ToolRouter 不能只看工具名，还要看参数。

重点检查：

- 路径是否位于允许工作区内
- 是否试图访问宿主机绝对路径
- 是否涉及敏感文件
- 是否请求删除
- 是否请求执行命令
- 是否要求联网

### 路由建议表

| 工具类型 | 路由 | 是否审批 | 回传方式 |
|---|---|---|---|
| `read_file` / `grep` / `search` | `host_readonly` | 否 | 无 |
| `write_file` / `edit_file` | `sandbox_edit` | 视策略而定 | `patch` |
| `delete_file` / `move_file` | `sandbox_edit` | 是 | `patch` + 删除清单 |
| `execute_shell` | `sandbox_exec` | 是 | `patch/artifact` |
| `run_tests` / `build` | `sandbox_exec` | 是 | `patch/artifact` |
| 生成图片/文档/报告 | `sandbox_edit` 或 `sandbox_exec` | 视策略而定 | `artifact` |

## diff / patch 设计

### diff 是什么

这里的 `diff` 指“原始工作区”和“沙箱输出工作副本”之间的文本差异。

比较对象：

- 基线：`/input/workspace`
- 结果：`/output/workspace`

### diff 怎么实现

推荐实现分三步：

1. 扫描两个目录树
2. 识别新增、修改、删除的文件
3. 对文本文件生成统一格式 diff，对二进制文件生成产物记录

### 第一步：目录扫描

分别扫描：

- `/input/workspace`
- `/output/workspace`

得到两份文件清单，记录：

- 相对路径
- 文件大小
- hash
- 文件类型

扫描后可以快速判断：

- 仅存在于输出区：新增文件
- 仅存在于输入区：删除文件
- 两边都存在但 hash 不同：候选修改文件

### 第二步：文本/二进制分类

候选修改文件需要先分类：

- 文本文件：进入 diff 流程
- 二进制文件：进入 artifact 流程

可按以下方式判断：

- 扩展名白名单
- MIME type
- 尝试按 UTF-8 解码

建议第一版使用“扩展名白名单 + UTF-8 解码回退”的混合策略。

### 第三步：生成 unified diff

对文本文件，生成标准 unified diff。

输出示例结构：

```diff
--- a/backend/app/tools/shell.py
+++ b/backend/app/tools/shell.py
@@ -10,7 +10,10 @@
-old line
+new line
```

推荐实现方式：

- Python 内置 `difflib.unified_diff`
- 或直接调用 `git diff --no-index`

### 推荐实现选择

第一版推荐使用 Python 内置 `difflib`，原因：

- 不依赖宿主机是否装有 git
- 跨平台更容易控制
- 便于在服务端直接生成结构化结果

实现思路：

1. 读取基线文本内容
2. 读取输出区文本内容
3. 按行切分
4. 调用 `difflib.unified_diff`
5. 产出统一 diff 文本

### 删除怎么体现在 diff 中

删除可以体现在 diff 中，但不建议仅凭 diff 自动删除宿主机文件。

推荐做法：

- diff 中显示删除
- 另外单独生成 `deleted_files` 清单
- 宿主机应用阶段对删除清单单独审批

### 新增文件怎么体现在 diff 中

新增文本文件：

- 直接生成以 `/dev/null` 为旧文件的 unified diff

新增二进制文件：

- 不进 diff
- 进入 artifact manifest

### change_set 结构

在生成 diff 之前，先生成结构化变更清单。

建议字段：

- `added_text_files`
- `modified_text_files`
- `deleted_text_files`
- `added_binary_files`
- `modified_binary_files`
- `deleted_binary_files`
- `touched_sensitive_files`
- `requires_approval`

这个结构用于宿主机侧的策略检查，不直接用于展示。

### artifact manifest 结构

用于非文本产物和二进制文件。

建议字段：

- `path`
- `kind`
- `size`
- `sha256`
- `source`
- `target_hint`

例如：

- 图片
- PDF
- DOCX
- XLSX
- ZIP
- SQLite

## 宿主机回传与落地

### 文本文件回传

流程：

1. 沙箱生成 `change_set`
2. 沙箱生成 unified diff
3. 宿主机校验路径是否合法
4. 宿主机检查是否触碰敏感文件
5. 宿主机检查当前文件是否已发生变化
6. 校验通过后再应用 patch

### 二进制与产物回传

流程：

1. 沙箱生成 artifact manifest
2. 宿主机按白名单接收文件
3. 校验大小、hash、类型
4. 确认目标落地路径
5. 审批后复制到宿主机目标目录

### 冲突检测

必须防止沙箱执行期间用户又修改了宿主机文件。

建议做法：

- 沙箱创建输出工作副本时记录基线 hash
- 应用 patch 前重新计算宿主机当前文件 hash
- 若与基线不一致，则标记为冲突，不自动覆盖

### 为什么不能直接覆盖

直接覆盖存在明显风险：

- 可能覆盖用户后续改动
- 可能把沙箱中的无关临时文件带回宿主机
- 无法做精细审计
- 删除操作风险过高

因此宿主机只应接受：

- 文本 patch
- 白名单产物
- 单独审批后的删除动作

## Docker 运行时建议

建议第一版使用 Docker 作为统一沙箱运行时。

建议参数：

- `--network none`
- 非 root 用户
- 只读根文件系统
- 内存限制
- CPU 限制
- `pids-limit`
- 超时控制

挂载建议：

- 工作区只读挂载到 `/input/workspace`
- 输出目录挂载到 `/output`
- 临时目录挂载到 `/tmp`

## 审批模型

审批不是沙箱的替代品，而是最后一道人工闸门。

建议以下动作需要审批：

- 执行 shell
- 运行测试或构建
- 删除文件
- 修改敏感路径
- 覆盖已有二进制文件
- 请求联网

普通只读操作不需要审批。

## 推荐的最小可行版本

第一版建议只实现以下能力：

1. Session 不预创建沙箱
2. 首次修改或执行时懒创建沙箱
3. 每个 session 只维护一个活动沙箱
4. 统一运行时，使用 `edit` / `exec` 两个 profile
5. 首次修改时复制整个工作区到输出副本
6. 文本修改通过 `difflib.unified_diff` 回传
7. 二进制文件通过 artifact manifest 回传
8. 删除动作单独审批
9. 冲突时拒绝自动应用 patch

这套能力已经可以覆盖：

- 代码编辑
- 配置修改
- 文档生成
- 测试执行
- 构建验证
- 大多数日常安全场景

## 后续演进方向

后续可以逐步增强：

- 从整仓复制优化到按需复制
- 支持更细粒度的网络白名单
- 为 patch 应用增加局部接受能力
- 增加敏感路径策略表
- 增加二进制产物预览与签名校验
- 增加沙箱缓存层，减少冷启动开销

## 总结

本设计的关键结论如下：

- session 创建时不应预创建沙箱
- 沙箱应按需懒创建，并在 session 内复用
- 不应维护两个并行沙箱，而应维护一个可升级的活动沙箱
- `diff` 的本质是输入工作区与输出工作副本之间的文本差异
- 文本通过 unified diff 回传，二进制通过 artifact manifest 回传
- 宿主机永远不接受未审计的直接覆盖

这套设计的核心心智模型是：

- 沙箱负责安全地试
- 宿主机负责谨慎地收
