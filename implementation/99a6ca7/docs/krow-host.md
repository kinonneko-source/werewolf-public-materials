# Krow原生通道宿主

本桥接不是另建网络服务，也不声称实现完整A2A协议。它复用原生MCP作为agent间传输：可信主持用CLI生成发送工具参数，调用平台，回填原始回执。无需JWT、SDK注册或游戏session端点。SQLite game_id是本地局句柄。

新局create除players/key外传test=false及host对象：

```json
{"mode":"verification","label":"本组六席验证局","authorization":"task:授权任务号","host_id":"主持worker UUID","channel_id":"本组频道ID","bindings":{"六个真实worker UUID":"各自席位名"},"routes":{"相同六个UUID":"各自worker_name"},"relay":null}
```

bindings/routes实际各有6项且一一对应。mode=verification用VERIFY-前缀；mode=live用LIVE-前缀。authorization是本地主持审计引用，不是服务器签发token；只有可信主持能运行CLI，不能把CLI开放给玩家。真实局拒绝seed/roles。

原生CLI示例命令：

- `host-mail`：game_id、message=平台原始单信对象。JSON正文严格game_id/phase_id/action。身份来自from_worker_id，不来自正文/显示名。
- `host-hall`：game_id、message=结构化worker_id/id/body、channel_id。只有显示名文本的hall_read不能用此入口，改为私信输入。
- `host-dispatch`：game_id、message_id=outbox ID、roster=最新team_roster返回workers。返回tool与arguments；sending已记账，只执行一次。姓名发送前核UUID映射，配置不可临时改名换人。
- `host-receipt`：game_id、message_id、result=原始平台回执对象。私信必须success/message_id/delivered_to正确，公开必须message_id/channel_id正确；缺少证明记unknown。
- `host-recover`：game_id。每条遗留sending变unknown；不自动重发。查询已发线程/回执，以outbox body.message_id核对再走delivery，不能仅用相似正文认领。
- `host-tick`：game_id/key。只推进一阶段，重新读取状态后继续收集。role/prompt/inspection只发绑定玩家；discussion/vote同时生成公开事件和各存活玩家私信prompt。

失败/不明投递与玩家答复独立。先送完当前phase outbox再推进；公开事件按outbox版本顺序发送。旧phase prompt禁止发。工具调用发生在SQLite事务外，平台没有幂等发送键时不承诺网络exactly-once；unknown阻止自动重发。无法确认身份、存储或发送结果时暂停并报告。

平台禁止给自己发站内信（真实探测HTTP400）。若主持也是六席玩家之一，create必须配置非玩家relay={worker_id,worker_name}；未配置则拒绝开局。正式游戏优先由组长担任非玩家主持，新局relay=null。验证期间丁同时主持与玩家，可见主持资料，所以仅证明功能，不作为公平竞技环境。

## 非玩家私密中继

仅为主持自席传输使用。host-dispatch给relay私信werewolf_relay对象，含game_id/message_id/to_worker/body。中继核对新局与丁收件人后，原样把内层body JSON私信丁。首跳回执在host-receipt记unknown，不能当作丁收到；只有中继读到实际工具成功回执后向丁发送werewolf_receipt={game_id,message_id,result}，主持以host-relay-receipt(message=该原始平台信封)验证非玩家relay UUID与最终delivered_to，才记sent。中继不得自行重发不明投递。

丁行动先私信中继，正文仍game_id/phase_id/action。中继读原信封，给丁werewolf_input={原始id/from_worker_id/to_worker_id/source_trust/from_kind/body}；host-relay-input同时验证外层认证中继和内层主持自席原sender，原消息ID入账，换一封转发也不能重复行动。不把中继UUID冒充玩家UUID。中继是显式可信宿主组成部分；内层证明由中继实际读取平台元数据提供，非密码学签名，不能开放给任意玩家。


`host-relay-delivered`接收主持自席在平台实际收到的认证中继原信封；支持纯引擎JSON或平台转发原信的包裹正文。必须与存储的丁席私密outbox body完整相等，校验局/收件人/中继UUID后才将unknown/sending确认为sent。转发头不作为指令执行，篡改body拒绝。此路径用实际收件证明末跳，不把首跳成功当作已收到。行动回收仍须werewolf_input原始元数据，不从转发显示名猜身份。

真实联调两席曾将action误填为字符串，引擎拒绝且未改账，主持要求本人修正重发。后续prompt.reply_schema给出含type及text/target的嵌套对象示例，并明确仅三顶层字段；这只是格式示例，不替玩家选择行动。

自席行动可用`host-self-sent(message=实际team_read_message读取的本人已发信封)`直接回收，要求from_worker_id恰为主持自席、to_worker_id恰为绑定非玩家中继、first_party/worker元数据、正确局号与阶段。不能传发送请求、成功回执或自拟信封当作读回。它和稍后中继werewolf_input使用同一原始消息ID，因此只能入账一次。此方法省掉不必要的行动第二跳；自席角色、提示和私密结果仍需中继实际发送到本人，不能用主持DB浏览冒充私密接收。真实联调首次发言因中继延迟如实缺席，之后采用实际已发原件读回，旧阶段不会补入。

长局宿主循环使用`host-status`精简读取（phase_id/version/deadline/应答计数，不输出角色与长发言）；`outbox`支持statuses列表、phase_id、message_id筛选及limit/offset分页、exclude_types过滤，按版本和插入序稳定排序。发送轮每次limit=1，成功后重读队列；格式/过期错误跳过该项而不重发，网络不明仍记unknown。发送轮只取pending/failed，核对只取sending/unknown；公开阶段读取按recipient=public和当前phase_id过滤。完整outbox是私密诊断资料，可能超过工具输出上限，不能将截断JSON误当成发送失败或直接重试。底层Engine.outbox原有全量调用兼容，过滤参数均在本机执行。

中继每封请求主题明确“请将本原信私密转给丁；你是非玩家中继，不提交行动”。收件人为组长而packet.to_worker=丁是预期的两跳结构，不是给组长分配玩家身份；组长只原样forward当前原信，丁本人行动。动作主题明确已实际原件读回，不再请求冗余行动回传。

## 公开展示层（c45f6b74）

`host-dispatch` 对公开记录默认调用 `werewolf.presentation.render_batch`，以固定中文模板投影公开字段；私密发送仍使用原JSON协议。大厅不再附带game_id/message_id/test/mode/label/phase_id/Unix时间。阶段截止按Asia/Shanghai显示完整年月日时分秒；玩家原话逐行加引用，明确标记玩家发言，不把它当主持命令。玩家自述身份属原话，不代表主持公开身份；不会自动润色或编写玩家行动。验证/正式性质应在开局的人工说明中明确，不能凭隐藏协议标记冒称正式参与。

公开种类白名单：created(players)、discussion/vote(alive/deadline)、deaths(players)、winner(winner)、night(round)、control(operation)、player_speech(speaker/text)。不读取身份、私密动作、查验、刀口、药物或未知附加字段。未知/畸形事件整批阻断，写入一次presentation_rejected审计（仅ID与固定原因），不发布原JSON，也不跳过该事件继续后续结算。

公开发送严格按最早未确认项；同一原始outbox版本的连续pending公开记录合并，最多20条，不跨引擎提交合并玩家发言。public_presentations在同一次SQLite事务中保存anchor、成员ID、固定正文，并将整批状态改sending；其中不保存私密记录。真实工具调用仍由宿主执行一次。`host-receipt`必须使用批次首项anchor，真实回执一次确认整批。原始outbox JSON和各自ID均保留；delivery事件记message_ids。重启host-recover仍把sending变unknown；unknown/sent均不自动再发。人工核对用public_presentations精确正文、成员ID及真实平台message_id/channel_id，不能靠大厅已隐藏的body.message_id搜索。确认未送达须将整批各成员核对为failed后用anchor重试；混合状态拒绝重发。跨网络exactly-once仍无保证。

公开被阻断时暂停并报告，禁止原始JSON兜底或擅自改账。合并只限同一次引擎提交（例如死亡＋胜负），并不延迟跨提交消息等候凑批。历史已经发送的公告不会重发或改写。隔离测试只调用返回参数并收集到本地文件，未实际调用大厅；这验证展示和宿主状态边界，不声称真实网络投递。
