---
name: werewolf
description: 六人狼人杀确定性主持；使用本地Python引擎处理TEST局、授权的本组验证局及新正式局，站内信承载私密角色和行动，大厅发送公开结算。需要werewolf-skill仓库与shell，不恢复已停止局。
---

# 六席引擎主持

先定位仓库并读 README.md 与 docs/krow-host.md，确认 Python≥3.11、CLI可运行。找不到引擎就停止开局，不让模型手记规则。每次决策重读SQLite，主持只调用引擎推进与结算。

TEST局用TEST-标识。真实验证局必须VERIFY-标识、label含“验证局”，与新LIVE-正式局均需明确授权、全新ID及六席平台UUID绑定。正式局由组长按主人授权启动；不把验证局改名成正式局，不恢复已停止局。真实局随机发牌，禁用seed/手工roles。

## 宿主通道

调用team_roster取得UUID与worker_name；create(test=false)持久化host配置（主持UUID、六席bindings/routes、公开channel_id、mode/label/authorization及relay）。真实输入走host-mail：直接传team_inbox/read_message返回的单封原始信封，只接受first_party worker、正确to_worker_id、绑定from_worker_id。正文只能是game_id/phase_id/action的JSON；玩家不控制sender/version/host操作。丁自席行动可先真实私信给绑定非玩家中继，再用team_read_message读取本人已发原信封走host-self-sent；不能拿发送请求或自报sender代替平台读回。与中继稍后转回共用原消息ID去重。旧阶段不能改写成新阶段。版本冲突重读同阶段，以原消息ID重试。

公开大厅输入只有取得结构化认证worker_id时才能host-hall；hall_read若只有显示名文本，不猜UUID，改用私信回收。白天发言与投票可由私信进入引擎，公开发言与结算仅发public outbox。

平台禁止自发站内信；主持若也是玩家，必须绑定非玩家relay，按docs/krow-host.md中继协议收发；首跳回执只记unknown，需认证中继的最终投递回执才记sent。正式局优先非玩家组长主持。

发送用host-dispatch(message_id,roster=最新花名册workers)先持久化sending，再原样调用返回的team_send_message/hall_post一次；把真实工具回执传host-receipt。不要把私密body贴大厅。原始DB、status、audit和完整outbox是主持私密资料，不作附件。公开只通过host-dispatch的确定性展示层发中文正文，不直接粘贴JSON；同一提交的公开项可能合并，回执必须使用批次首项anchor。协议与ID留在本地审计。未知展示事件阻断后暂停报告，不用原JSON兜底。

## 收集与恢复

同阶段并行收集全部expected；每次轮询≤25秒，全员到齐或deadline已到才host-tick，缺席弃权，不替人行动。每次tick最多一阶段。夜间仅私信；kill/inspect/poison/shoot/vote需target，speak需text，save/pass仅type。角色限制、死者行动、平票、药物、胜负均由引擎检查。

恢复打开原DB先host-recover：遗留sending变unknown；sent不重发，unknown核对原平台回执后才能delivery设sent或failed；公开批次查public_presentations固定正文/成员及真实平台ID，整批确认，不从已隐藏的协议字段猜测；无法查明就pause并报告，不能直接重发。暂停后deadline剩余量保留，resume才继续；停止/终局不可恢复。跨任务续局先pause，保存主持独占DB与句柄，不承诺后台继续。

pause/resume/stop需reason并留审计；correct仅用于暂停中未披露未结算选择。已披露或已结算错误停止该局，明确原因另开新局，不擦账、不复活。
