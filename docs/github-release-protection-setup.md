# GitHub 发布保护配置

本文供仓库管理员在 GitHub 网站上执行。代码中的发布白名单已经包含 `jardenx`、`aiden-cao`、`robot-ux`，但合入代码不会自动创建审批规则、保护默认分支或修改仓库权限。以下复选框是待执行项，不代表已经配置。

如果 internal 和 public 仓库都保留发布能力，需要分别配置；它们的设置不会互相同步。不要为验证配置而直接触发真实发布。

## 1. 确认账号和套餐

在仓库 `Settings → Collaborators and teams` 确认三人可以访问仓库。作为 CODEOWNERS 的账号必须拥有仓库写权限；网站执行发布操作还需要相应的 Actions 权限。清理不必要的管理员和具有规则绕过能力的角色。

| 账号 | 代码校验使用的固定 GitHub ID |
|---|---|
| `jardenx` | `274764117` |
| `aiden-cao` | `104969608` |
| `robot-ux` | `81206731` |

- [ ] 三人的账号、仓库访问权限正确。
- [ ] 已确认当前套餐支持该仓库的 environment 审批保护。private/internal 仓库不能仅因存在 Environment 页面就认为支持 Required reviewers；GitHub Free、Pro、Team 的该项审批保护仅适用于 public 仓库。如果界面没有所需选项，应先解决套餐或组织策略限制。

## 2. 设置发布 Environment

进入仓库 `Settings → Environments`，创建或编辑以下环境。名称必须与 workflow 一致。

| Environment | 对应渠道 | Required reviewers |
|---|---|---|
| `npm-publish` | npm alpha、npm production | `jardenx`、`aiden-cao`、`robot-ux` |
| `pypi` | production PyPI | `jardenx`、`aiden-cao`、`robot-ux` |
| `testpypi` | Test PyPI | 配置独立审批；建议使用同一名单 |

对每个 environment 执行以下设置：

1. 在 `Deployment protection rules` 启用 `Required reviewers`，填写上表的人员。
2. 勾选 `Prevent self-review`。
3. 取消勾选 `Allow administrators to bypass configured protection rules`。如果该选项不可用，确认组织策略及套餐能否提供等效保护；不能把缺失的保护当成已启用。
4. 在 `Deployment branches and tags` 选择 `Selected branches and tags`，仅添加默认分支 `main` 的 Branch 规则，不添加同名 Tag 规则。
5. 保存，再重新打开页面确认设置仍然存在。

GitHub 的 Required reviewers 是名单中任意一人批准即可，不是三人全部批准。配合禁止自批，正常发布由一人发起，另一人批准。重跑人也受代码白名单检查，但 `Prevent self-review` 不等于禁止源码作者审批；审批人仍需独立核对代码。

当前 Test PyPI 同样从默认分支运行 workflow，通过 `source_ref` 选择待测分支，因此上述 Branch 规则适用。Test PyPI 没有增加三人发起白名单。

- [ ] 三个 environment 均已保存审批和分支规则。
- [ ] `npm-publish`、`pypi` 的审批名单只有指定三人。
- [ ] 禁止自批、禁止绕过均已实际生效。

## 3. 保护默认分支和发布代码

进入 `Settings → Rules → Rulesets → New ruleset → New branch ruleset`，创建针对默认分支 `main` 的规则。已有有效规则时，在原规则中补齐即可，不要用新规则覆盖已有更严格的保护。

| 设置 | 要求 |
|---|---|
| `Enforcement status` | `Active`，不能只使用 `Evaluate` |
| `Target branches` | 默认分支 `main` |
| `Bypass list` | 不添加常规发布人员或 Actions 的绕过权限；核对叠加的组织规则 |
| `Require a pull request before merging` | 开启，至少一名独立评审 |
| `Require review from Code Owners` | 开启 |
| `Dismiss stale pull request approvals when new commits are pushed` | 开启 |
| `Require approval of the most recent reviewable push` | 开启 |
| `Require status checks to pass` | 保留现有必要检查，并将下述发布回归检查设为必需 |
| `Block force pushes`、`Restrict deletions` | 开启 |

本次提供的 `.github/CODEOWNERS` 指定三人负责 `.github/`、发布脚本、包清单、锁文件和构建配置。CODEOWNERS 要在 PR 的目标分支上生效，且必须配合上述规则。首次引入该文件的 PR 应由管理员确认独立审查，不要假设文件会自动保护自己的首次合入。

发布回归 workflow 是 `Release workflow regressions`，job 是 `regression`。它在所有 PR 和 main 推送时运行，避免无关 PR 因路径过滤而永久等待必需检查。先让它在 PR 中成功运行，再在 Required status checks 中选择实际出现的检查名，并将来源限定为 GitHub Actions。

严格的“修改 main 必须通过 PR”会阻止 workflow 使用 GitHub API 自动提交版本号。不要为自动 bump 给 Actions 增加绕过规则。采用以下发布流程：

1. 通过经过审查的 PR 更新版本号，必要的依赖或锁文件变更也在 PR 中完成。
2. npm production 使用 `bump: current`；PyPI 的 `version` 填写已合入的 `_version.py` 版本。
3. workflow 在版本未变时复用构建 commit，不向 main 写版本提交。若请求自动 bump 且被规则拒绝，流程会在上传前失败。

- [ ] CODEOWNERS 已存在于目标分支，三人都有写权限，GitHub 未显示语法或账号错误。
- [ ] main 规则已启用，发布代码变更需要指定人员的独立审查。
- [ ] 必需检查实际会在受保护 PR 上运行。
- [ ] 发布人员已按“先版本 PR，再发布现有版本”的方式操作。

## 4. 清理 GitHub 中的旧凭据入口

进入 `Settings → Secrets and variables → Actions`，同时核对仓库和各 environment 的 secrets；组织级 secrets 需要组织管理员检查授权给本仓库的范围。

- [ ] 已确认 npm、PyPI、Test PyPI 各自的 Trusted Publisher 配置正确。
- [ ] 在明确授权的 OIDC 验证成功后，移除不再使用的 `NPM_TOKEN`、`NODE_AUTH_TOKEN`、`PYPI_API_TOKEN`、`TEST_PYPI_API_TOKEN` 等长期发布凭据，避免被其他 workflow 继续引用。
- [ ] 已检查其他有发布能力的 workflows，没有旧的 token 发布入口。

删除 GitHub secret 不会撤销 registry 中的 token。npm/PyPI 的 Trusted Publisher、直接发布成员、token 撤销需要在对应 registry 网站单独处理，见 [Releasing](releasing.md#permissions-and-required-registry-configuration)。

## 5. 验收

保存设置后，重新打开 Environment 和 Ruleset 页面核对，并记录执行人和实际完成时间。对以下行为进行验证时，先使用不上传包、不请求发布凭据的审批演练；真实 registry 发布需要另行授权。

- [ ] 非名单人员发起或重跑 npm alpha/prod、production PyPI 时被代码拒绝。
- [ ] 名单人员发起后，上传 job 在 environment 审批处等待。
- [ ] 发起人不能自批，另一名指定人员能够审批；未配置的人员不能代为批准。
- [ ] 非 main 分支或同名 tag 不能使用生产发布 environment。
- [ ] 修改发布 workflow 的 PR 需要指定 CODEOWNER 审批；补推修改后旧审批失效。
- [ ] 未开启 Actions 直接写 main 的绕过权限。
- [ ] 已另行核实 registry 发布权限和 OIDC，且没有用一次真实发布来替代上述权限检查。

这些规则约束正常发布人员和仓库贡献者。能够修改全部保护规则的管理员，以及授权人员串通，仍需要组织权限管理和审计承担。

## GitHub 官方说明

- [管理 environments、套餐限制和部署保护](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
- [Rulesets 可用规则与 PR 审批](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)
- [CODEOWNERS 权限、语法和生效分支](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)
