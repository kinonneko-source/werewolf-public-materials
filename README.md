# 狼人杀公开实现与证据材料

分享入口：https://github.com/kinonneko-source/werewolf-public-materials

本仓库汇集**实际历史实现快照**、已放行的七份公开原件、离线阅读合辑及本轮本地验证证据。它不是市场插件上线或生产服务已接通的证明。仅有本地 TEST 演示；历史三局均为 VERIFY，正式 LIVE 原件未取得。

## 内容与来源

| 路径 | 来源与用途 |
|---|---|
| `implementation/99a6ca7/` | `kinonneko-source/werewolf-skill` 固定提交 `99a6ca7ac02bcae38ea8fa0ca8833f80093df12c` 的真实源码快照；历史主局对应版本 |
| `implementation/46ef0d5/` | 同一来源固定提交 `46ef0d5e23419fa0243ea2b698781b467414d666`；历史工程局对应版本 |
| `materials/` | 来源任务 b26c2cd7-3ebe-45e0-9f74-2018f53cdbb1 已放行七原件；逐字节比对旧HTML内嵌原件 |
| `狼人杀公开材料合辑.html` | 在既有七原件合辑上增加真实仓库入口；单文件离线可读及内嵌原件下载保留 |
| `evidence/` | 本轮真实离线测试/演示原始日志、终端输出截图、核验记录 |
| `config.test.example.json` | 新增脱敏 TEST 配置；非历史生产配置，不含平台账号或身份映射 |
| `SHA256SUMS` | 公开文件哈希清单（不含自身） |

上游是私有研发仓库，读者无需访问上游即可使用本仓库完整快照。上游各版本原有 `LICENSE` 原样保留（MIT，Copyright 2026 kinonneko-source）；未为第三方 SDK、模型、公开对局原话或其他材料新增许可证。未复制上游 git 历史、SDK wheel、模型权重或其源码。历史 README 中“本次/已验证/PR未合并”均按当时版本语境理解；本次状态见本页及本轮日志。

## 实现路径与依赖

可信主持 Agent 读平台认证消息 → `werewolf.host` 校验平台UUID与席位绑定 → `werewolf.engine` 按确定性规则推进 → SQLite事务存状态/事件/outbox → `werewolf.presentation` 白名单公开展示 → 主持调用平台工具并写真实回执。CLI是 `python -m werewolf`，操作规范在各快照 `skills/werewolf/SKILL.md` 和 `docs/krow-host.md`。不是独立后台网络服务，也不是完整 A2A 协议端点。

核心为 Python ≥3.11 标准库，无 pip 依赖；本轮实际 Python 版本见 `evidence/verification.json`。可选 SDK `krow-agent-sdk==0.9.5.27` 及 wheel SHA 在每份 `requirements-sdk.txt`，核心演示不需要安装它；该文件只固定SDK本体，没有传递依赖锁，本轮未安装/调用 SDK。没有 Jev/TypeSafe/Qwen 程序实现或权重随包；七原件中的相关实验结论只是历史材料，不能用狼人杀测试替代其复现。

## 启动与复现

```sh
git clone https://github.com/kinonneko-source/werewolf-public-materials.git
cd werewolf-public-materials/implementation/99a6ca7
python3 -m unittest discover -s tests -v
python3 examples/run_test_game.py
python3 -m werewolf --help
```

`run_test_game.py` 是上游原有脚本，每个操作启动全新CLI进程、临时SQLite、固定测试角色，无网络发送，最后检查好人胜和审计。人工创建一个独立TEST状态可在仓库根目录执行：

```sh
cd implementation/99a6ca7
python3 -m werewolf --db runtime/public-demo.db create < ../../config.test.example.json
printf '%s' '{"game_id":"TEST-public-example"}' | python3 -m werewolf --db runtime/public-demo.db public
```

重复创建同一局会拒绝覆盖，重试请换全新数据库或明确的新局标识。`status/audit/outbox` 和SQLite含主持私密信息，只供本机可信主持；不要上传。需要验证工程局快照时把上面目录改为 `46ef0d5`。此处仅复现本地TEST；不新开真实局，不发送平台消息。

## 已验证与复现边界

本轮测试结果逐项在 `evidence/verification.json` 及原始 `.log`；截图只展示该日志的实际输出，不是历史真实对局画面。本轮核对源码快照、七份原件字节、离线HTML、匿名远程访问与文件哈希。历史局数、接纳/送达计数及模型实验均来自七原件，本轮没有重新执行历史局或模型调用。

真实平台运行还需合法宿主身份、六席认证UUID与路由、频道授权、工具可见性、人工在场调度；不能把公开模板填上姓名就当认证。当前会话结束进程终止，无跨轮后台承诺。未知投递不能盲重发；SQLite事务不提供跨网络 exactly-once。终局不自动公开角色、夜间动作或逐人选票。

## 卡点与缺件

- 市场上线、托管持续调度、原生工具注册、通用模型API/自动调用及端到端提速，本轮均未验证，也不以源码存在声称完成。
- 未取得可公开的历史平台运行截图、完整原始宿主命令日志及当时环境锁；公开JSON是已放行历史事件投影，不是完整DB或原始夜信。已向来源持有人请求补件，取得后仍须审查才能公开。
- 历史主局与工程局版本不同，后续稳定接纳回执/schema修复不在这里倒推为历史能力。
- `config.test.example.json` 是本轮脱敏本地TEST示例；历史实际六席配置含身份/私密关联，不公开，不用生成配置冒充历史原件。
- 原始宿主DB、角色/夜间动作/逐人票据不在公开范围；来源限制详见 `materials/记录覆盖与披露边界.json`。

公开前对待提交路径进行密钥模式扫描、文本与文件清单复核；不含运行数据库、真实账号配置、工作台环境或无关附件。原件原文及其历史编号按已放行范围保留。具体复核结果见 `evidence/release-review.md`。
