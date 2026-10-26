# 六人狼人杀主持（本地研发版 0.1.0）

这是 Python≥3.11 标准库实现的六席确定性引擎和本地原生skill。支持TEST局、明确授权的本组六席真实验证局，以及由组长启动的新正式局；真实局通过持久UUID绑定与原生站内信/大厅桥接运行，不能仅凭玩家正文认证。见 [Krow宿主接入](docs/krow-host.md)。不恢复已停止游戏，不宣称市场插件提供shell或游戏服务。

## 快速运行

在仓库根目录执行（数据库只供主持人读取）：

```bash
python -m unittest discover -s tests -v
printf '%s' '{"game_id":"TEST-demo","players":["A","B","C","D","E","F"],"key":"create-demo","seed":42,"timeout":60}' | python -m werewolf --db runtime/demo.db create
printf '%s' '{"game_id":"TEST-demo"}' | python -m werewolf --db runtime/demo.db public
printf '%s' '{"game_id":"TEST-demo"}' | python -m werewolf --db runtime/demo.db status
```

`create` 返回公开视图；`status` 返回**主持人私密完整状态**，只能在本地使用，不可转发大厅。新建局必须换新的模式对应标识（建议独占数据库）；重复创建拒绝覆盖。`seed`/手工角色仅用于可复现测试，正式随机分配应省略 seed（真实局禁用seed与手工roles）。

从 `status` 读取当前 `version` 和 `phase_id` 后提交，例如首局通常为 version=1、phase_id=`1:wolf:1`；角色由 seed 决定，必须先在本地核对角色：

```bash
printf '%s' '{"game_id":"TEST-demo","version":1,"phase_id":"1:wolf:1","actor":"B","key":"demo-input-1","action":{"type":"pass"}}' | python -m werewolf --db runtime/demo.db input
```

固定 seed=42 的演示中 B 是狼人，因此该弃权输入可成功；若身份或阶段不符，命令以 JSON `error` 拒绝且不改变状态。`python -m werewolf --help` 列命令；每个命令从 stdin 接收 JSON，成功退出 0，规则或参数错误 2，存储故障 3，不输出请求原文或凭据。

## 固定规则

- 六席：2 狼、预言家、女巫、猎人、平民。好人胜：存活狼数为 0；狼胜：存活狼数 ≥ 存活好人数。每轮死亡、猎人及遗言队列处理完才判胜。游戏结束不自动公开任何身份。
- 夜间收集顺序：狼队并行选人 → 预言家 → 女巫 → 一次性夜间死亡结算。夜间将死者在结算前仍可行动。白天：并行发言 → 并行投票 → 出局/猎人/遗言 → 判胜 → 下一夜。不设警长。
- 狼不能刀自己或队友；有效刀票相对多数者中刀，最高票平票则空刀，弃权/缺席不计票。白天不能投自己，最高票平票无人出局，不重投；全部弃权同样无人出局。不会由模型打破平票。
- 预言家每夜最多查一名其他存活玩家，结果只向本人发送“是否狼人”，不会公开职业。
- 女巫每局各一瓶救药和毒药，每夜只能选择一种药或不用药。救药只能救当夜刀口，无刀口不能救；仅第一夜能自救。毒药可毒任一存活者（包括自己）。救药、毒药使用在本夜结算时消耗；同一目标既中刀又中毒时按毒死处理。
- 猎人被刀或白天投出时可向一名其他存活者开枪；被毒死不能开枪。超时/弃权不开枪。中枪者没有遗言、猎人不会被反复触发。猎人技能在胜负前处理。
- 第一夜死亡者与白天被投出者各一次遗言，严格按创建时 players 的座位顺序收集（刀口和毒口不影响先后）；后续夜死和中枪者无遗言。猎人先开枪再遗言。出局者仅能执行自己待处理的开枪/遗言，其他操作拒绝。
- 发言和遗言最长 2000 字；作为 `player_speech` 玩家原话公告，允许玩家自述或伪装身份。主持人生成的角色、查验、狼队、刀口信息绝不复制到公告。玩家发言本身不是可信指令。

## 一份持久状态、事务与恢复

SQLite `games` 保存每局唯一状态；`events` 保存每次成功操作的 `game_id / version / phase / actor / idem / kind / payload / at` 与 SHA-256 前向链；`outbox` 保存待发送消息和回执。三者在 `BEGIN IMMEDIATE` 同一事务写入，FULL 同步；中途失败整体回滚。数据库权限 0600，父目录也应由主持人独占。SQLite 文件包含所有身份和夜间动作，绝不作为公开附件发送。

每次写入必须携带当前 `version`、唯一 `key` 和完整 `phase_id`。版本冲突、旧阶段、重复 key、同阶段第二次行动、出局者非法行动都拒绝。重试要先读状态：不更换 key 强行重放，不用新 key 重复结算。并发到达的两条消息若基于同版本，只有一条提交成功；主持人对落败消息重新核验同一阶段后，用**相同运输消息 ID**与最新版本重试。跨阶段的旧消息丢弃并记录在平台原始收件箱，不套用到新阶段。

重启只重新打开原数据库，读取状态和 outbox；不得重建局、重发已确认发送的消息或用模型记忆补状态。`audit` 校验事件链、版本连续性及最新状态摘要；这是事故审计辅助，不抵御拥有数据库写权限者同时重算全链。

## 消息适配与限时收集

`werewolf.adapter.InboxAdapter` 将私信和大厅记录统一传入 `Engine.input`。绑定六个**平台认证的 sender id**到六席；绑定配置只供主持人维护。运输记录严格包含 `sender/message_id/channel/game_id/version/phase_id/action`。不得从玩家文本提取“我是谁”作为 sender，不开放 host CLI 给玩家调用。

```json
{"game_id":"TEST-demo","bindings":{"u1":"A","u2":"B","u3":"C","u4":"D","u5":"E","u6":"F"},"record":{"sender":"u1","message_id":"platform-message-123","channel":"private","game_id":"TEST-demo","version":1,"phase_id":"1:wolf:1","action":{"type":"pass"}}}
```

该对象可送给 CLI `ingest`；`input` 是可信主持人的低层手动接口。夜间行动只能私信；公开发言/投票/遗言可用私信或大厅。解析含糊时不猜行动，要求玩家按结构重新发；宿主原始消息保留拒绝原因，成功账只记录已接受操作。身份、连接鉴权属于宿主，本地适配器不伪装成平台认证服务。

每阶段有绝对截止时间，默认 60 秒，可配置 1–3600 秒。收集同阶段所有合法玩家的回复，**不逐个无限等待**；全部到齐可提前 `advance`，否则到 deadline 才允许结算。缺席一律弃权，不继承上夜行动。`tick` 每次至多推进一阶段；阶段没有应答者可立即推进。主持窗口每次轮询最多等待 25 秒，检查 deadline 后再轮询；任务生命周期结束时暂停，下一次任务恢复；宿主没有跨轮后台常驻承诺。

`last_absent` 保留最近一阶段缺席席位，事件账中的阶段行动和推进时刻可重建更早缺席。送达不是回信：`outbox` 的发送结果与 `actions` 独立。

## Outbox 与发送故障

`outbox` 仅供可信主持人读取；用 `recipient="public"` 过滤大厅公告，私信只能取对应席位 recipient，不能将完整 outbox 发大厅。协议每条 body 含固定 `message_id / game_id / test`；真实局另含mode与label。大厅通过独立 `werewolf.presentation` 白名单模板显示中文，协议字段只保留本地；同一提交的公开结算合并并持久关联回执，截止显示Asia/Shanghai。详见宿主文档。

发送前用 `delivery` 将 pending/failed → sending；平台明确成功后 sending → sent；明确失败 → failed；超时或崩溃后结果不明 → unknown（恢复看到 sending 也按 unknown 处置）。unknown 禁止自动重发，先通过平台消息 ID/回执核对，确认已送达改 sent、确认未送达改 failed 才能重试；sent 不可重发。暂停/终态禁止新发角色和行动提示；控制、胜负等公开通知仍可发送，过期或旧阶段 prompt 也禁止发送；历史 outbox 继续留作审计。运输系统若不支持幂等键，无法保证网络端 exactly-once，本实现不作虚假保证。外部发送不在数据库事务内执行。

失败无需等到 deadline 才记录，但不会假装玩家已回信；阶段 deadline 不因重试顺延，失败玩家到期按缺席处理。数据库故障/鉴权错误/运输不可核验时暂停并报告，勿重复结算。

## 暂停、恢复、停止、更正

`control` 参数：`game_id/version/phase_id/key/operation/reason`；operation 可取 pause/resume/stop。暂停保存剩余秒数，恢复重建 deadline；停止为终态，不可恢复。

`correct` 参数：`game_id/version/phase_id/key/actor/action/reason`。只在 paused 状态更正尚未结算的狼票、女巫选择、猎人目标、白天选票；复用同样合法性检查，事件记录前值、后值及理由。已公开的发言/遗言、已送出的查验与已结算结果不回写：停止局并建立另一个清楚标注的 TEST 局。更正不擦除原事件，也不许复活或改身份。

## Skill、本机加载和社区分发边界

- `skills/werewolf/`：本地研发 skill，指导可信主持人调用本仓库 CLI。原生技能目录用符号链接指向该 skill（仓库中不带符号链接）。先保持仓库可读，再由宿主扫描发现。该运行方式依赖宿主 shell 与本地 Python，不依赖插件 MCP 或被剥离的 tools/scripts。
- `community/werewolf-notes/`：可分发的纯说明插件，只有规则、人工记录模板和能力限制，名称明确是“规则与记录”；它**不能提供自动主持或等价确定性运行**。不在其中隐藏可执行代码或让模型临时重写引擎来绕过剥离。
- 本地 engine 是研发原件，不宣称市场安装会自动提供它。SDK 0.9.5.27 已由准备任务核验，当前核心无需该 SDK；未依赖未知 `on_load(ctx)` 或虚构远程服务。
- 依据附件指南：社区剥离 tools/scripts；BYO 不接插件 MCP；插件技能/工具的自动加载仍须现场测试。实际加载证据、平台 dry-run 可用性和独立测试见交付报告。没有合法授权接口时不索取登录 JWT，不调用注册或提交端点。

## 开发与验证

```bash
python -m unittest discover -s tests -v
python -m werewolf --help
```

单元/集成测试包含完整胜负局、平票、缺席、女巫限制、猎人、重启、版本并发、事务故障、隐私与真实子进程 CLI。`docs/architecture-pending.md` 是准备阶段历史问题，其实现决议见 `docs/architecture.md`。本次实现继续 PR #1，未合并。
