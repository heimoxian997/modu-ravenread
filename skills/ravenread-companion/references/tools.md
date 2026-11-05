# RavenRead 工具调用

这是结构示例，人物与情节均为合成。实际以 MCP 返回的 schema、ID、版本和章节为准。

## 查事件与分支

```json
{"filters":{"query":"甲某与乙某为何失去信任","view":"graph","limit":5,"compact":true}}
```

query 可以换成“甲某为什么不相信乙某”：人物关联、关键词与已就绪的向量一起检索，不要求整句一致。无法保证任意问法都命中。看 retrieval.semantic 可区分 ready（已参与）、partial（部分就绪）、building/warming（后台处理中）、unavailable（人物/关键词兜底）。索引后台增量维护，不需要 agent 建索引。

按人物导航不必组织完整问句：

```json
{"filters":{"entities":["甲某"],"view":"graph","limit":5}}
```

仅浏览线名用 `list_storylines(filters={entities:["甲某"],limit:5})`，再按返回的 note_id 展开。剧情线参与人物会结合有效引用事件补齐。

`note_query` 默认图视图：`graph.nodes` 是本页节点，`graph.edges` 是本页可见节点间的连接。推荐 compact=true，图视图只返回graph，不重复返回items；为兼容旧调用，compact默认false。非图视图仍使用items。
`chapter_number` 是发生章，`evidence_chapters` 是原文证据章。`recorded_at_chapter` 是该版本写入时的安全章；当前 `known_by_chapter` 使用同一保守边界（knowledge_boundary_policy=write_scope），不自动推断结论最早成立章。晚章补记早章事件，仍可能隐藏于早期查询；不能仅凭早章引文把后来解释回填成早期已知。

精简事件也返回basis和perspective。节点返回自身basis/perspective，另有evidence_basis和按需返回的evidence_perspectives。basis取explicit（原文明示）、interpretation（解释推断）、mixed（混合依据）、unclassified（未标注），不代表概率。旧节点未标注时保持unclassified；引用包含推断时，节点不能按纯原文明示输出。新节点写interpretation/mixed必须注明perspective。

## 严格回看与事后整理

time_mode默认strict：as_of_chapter限制当时可见的版本。晚章补录的早章结论不会自动回填。strict下view=excerpts可以访问这些补录记录引用的目标范围原文：

```json
{"filters":{"view":"excerpts","as_of_chapter":12,"chapter_from":12,"chapter_to":12,"limit":5}}
```

摘录只从同书正文章重新核对原句，搜索和人物筛选仅使用原句，不使用后来标题、参与人元数据或解释。返回read_only、projection=evidence_only、basis_scope=quotation；recorded_later说明摘录来自后期补录。不会返回可写note_id/version，不能把excerpt_id当作entry引用。无摘录不代表没有正文，可继续按章读。翻页保持as_of_chapter与其他条件不变。

用户明确要求按当前认识回顾早期事件时：

```json
{"filters":{"query":"交付纸伞","as_of_chapter":12,"time_mode":"retrospective","view":"events"}}
```

retrospective的as_of_chapter仍限制发生章，笔记版本可以来自当前已读进度。time_scope.event_as_of_chapter是事件边界，knowledge_as_of_chapter是可使用知识的边界；written_after_as_of为true说明版本晚于事件回看章。它可能含后来认识，不能说成当时已知。仍不得超过当前实际阅读进度。模式、回看章和筛选都参与cursor校验；不能用strict的cursor切换到retrospective。review/references/excerpts使用strict模式。

## 查询范围与分页

按 `note_id` 展开剧情线；按 `note_id + node_id` 查看该节点及相邻分支。按 entry 的 `note_id + version` 读证据详情。`view=events` 只取精简章节事件，`view=entry` 取证据笔记；`view=storyline` 取剧情线。
显式 entities 默认 AND，可设 `entity_match=any`；仅传 query 时，自动提取的人物线索按 OR 寻找相关记录。多人问题不要默认要求每条笔记都包含所有人。返回的 matched_by 说明关键词/语义/人物来源，match_scope=entity_only 只用于导航。matched_counts 区分事件、剧情线和展开节点数量。

默认 relevance=focused，仅人物相关的记录在navigation_candidates单列，navigation_count表示导航记录数；不会把“某人怎么知道”当成任意人物事件的匹配。具体问题先补充要确认的事实。语义 min_similarity 默认0.6，可设0.45–1，返回semantic_similarity为原始余弦值，不是正确概率。focused图查询只返回实际命中的局部节点；relevance=broad可恢复宽泛人物与相邻节点浏览，仍需核对原文。

先读整条线目录用 `note_query(filters={note_id:实际线ID,view:"directory"})`，默认最多40个精简节点，仍受返回体积限制；然后按 node_id 展开证据。事件反查用 `note_query(filters={note_id:实际事件ID,view:"references",version:实际证据版本})`，version 可省略以查看任意版本引用；待修订引用会标记 needs_revision，已删除线不返回。

翻页时保留筛选条件并原样传回 `cursor`。章节范围含两端；历史查询用 `as_of_chapter`，不能大于安全章。

limit按视图区分：directory默认40、最多40，其他视图默认5、最多10；省略或null使用该视图默认值。目录cursor固定首次查询的安全章与笔记版本快照；阅读进度前进后仍读取旧范围，退到快照安全章之前则拒绝。其他查询继续检查原阅读范围。invalid_cursor返回reason（如reading_scope_shrank、filters_changed、wrong_book）和可用的changed_fields；不能通过改view/limit复用旧cursor。

## 正文读取与搜索

`read_chapter` 默认2400字，max_chars可设200–6000；`read_chapters` 默认6000字，可设200–12000。尽量在后半页的段落边界收尾，超长段落按硬上限切开，不丢字。返回 total_chars、returned_chars、consumed_chars、remaining_chars；每段有从0开始的 segment_index 和章内 start_offset/end_offset。字符数包括返回的换行，不等同于 token。续读保持额度和范围不变。

`search_text` 支持 exact、hybrid、auto。auto 先精确原词，再尝试已观察且无歧义的人物别名，最后有索引时走混合检索。retrieval 返回 requested_mode、index_status、steps 和必要的 fallback_reason；实际 mode=exact 时不能当作已做语义兜底。matched_by=exact/alias/semantic 说明召回途径，不保证结论正确。

search_text的limit为1–8；hybrid默认2，exact以及auto精确阶段默认5。get_reading_context返回chapter_kind/is_extra、main_text_end_chapter和safe_main_chapter，正文末章不一定等于目录当前章号。

命中附短 passage_ref，`read_passage` 可设置 radius（命中前后0–6000字）、max_chars（200–6000）和 to_chapter_end。结果的 hit_offset 是命中在本段中的位置；语义检索标的是召回子块，并非逐字命中用户问题。窗口外的后续段 hit_offset=null。chapter_cursor 可交给同章默认2400字的 read_chapter 接着读。片段 cursor 续读时保持 radius/max_chars/to_chapter_end 不变。

短 passage_ref 是单后端进程内的有界缓存引用，有效期24小时，最多保留2048条；重启、过期或淘汰后返回 invalid_cursor，重新搜索即可。原签名格式仍可读取；跨书引用或原文变化仍拒绝。

## 新建章节事件

将下列对象作为 `note_upsert` 的 `note` 参数：

```json
{
  "kind":"entry",
  "chapter_number":12,
  "title":"归还信物",
  "claim":"甲某把信物归还乙某，乙某收下。",
  "basis":"explicit",
  "payload":{"type":"event","action":"归还信物","participants":["甲某","乙某"]},
  "evidence":[{"chapter_number":12,"quote":"甲某将信物放回乙某掌心。"}]
}
```

quote 必须换成真实读取、在指定正文章唯一出现的 4–120 字原文。新 claim 最多 180 字。
单章 entry 的所有 evidence.chapter_number 必须与 entry.chapter_number 一致。后章揭示前事时，另写后章的认知或确认事件；剧情节点还会核对被引用笔记中的实际证据章，不能仅修改事件章号来绕过。
其他 payload 类型：

- relation：a、b、label、state_at_chapter。label 用工具 schema 枚举。
- fact：subject、predicate、value。predicate 用 schema 枚举。
- address：speaker、target、address。

解释性记录增加 `basis=interpretation` 和 `perspective`。新记录无 ID；修订保留原 `note_id`，填当前 `expected_version` 和 `change_reason`。

动机记录可用 `payload={type:"fact",subject:已知称呼,predicate:"动机",value:有依据的动机结论}`。同一主要变化及其直接结果可合一条，独立变化才拆开。写前可 `find_note_duplicates(filters={chapter_number:实际发生章,entities:[已知人物],query:拟写结论,quote:实际引文})`，query/quote/entities 至少一项。chapter_number可省略，或改用chapter_from/to检索区间，均省略则检索已读范围；结果含实际事件章，候选不自动合并。

引文失败返回errors，每项含field、evidence_index（从0开始）、chapter_number及提交的quote；quote_not_found可能带closest_passage（实际正文候选、上下文和章内位置）。候选仅供重新摘录，当前写入仍整体失败。实体名失败可带candidates（拼写相似度、来源章和上下文），只来自当前书已读正文；无候选时不编造，不能把拼写相近当作同一人物。

## 剧情线

storyline 输入包括 title、category(plot/relationship)、entities、overview、stage、nodes、edges，可选 open_questions。
节点：`{node_id, chapter_number, conclusion, basis?, perspective?, branch?, evidence:[{note_id, version}]}`。basis默认unclassified；interpretation/mixed须perspective。节点性质与引用事件性质分别处理，不能仅凭原文引用自动宣称结论原文明示。
连接：`{source, target, relation, basis, reason?}`，端点是本线 node_id。

例如节点 `discover` 分别连接 `investigate-a` 与 `investigate-b`，两者再连接 `merge`，即可表达分叉和汇合。该示例只说明 ID 结构，不意味着真实剧情存在这些关系。
ID 一旦使用就保留；修订节点文字或证据时不换 ID。新建或整体重整提交完整 nodes/edges；局部修订使用下面的 storyline_patch，服务端保留其余分支。

## 局部更新剧情图

note_upsert 的 note 可用 `{kind:"storyline_patch", note_id:实际线ID, expected_version:当前版本, change_reason:"修订原因", nodes:[要改的完整节点], edges:[新增或替换的连接]}`。
可选 remove_node_ids、remove_edges（source/target/relation 三元组），以及 title/overview/stage/open_questions。节点更新必须带 node_id。
服务端合并并检查版本，无需重抄未改动节点和连接。分批核对关联错误时，其他待修订节点继续遮蔽，不会被当作正确结论返回。

精确查线时返回 storyline 头信息；`view=edges` 可分页读该线连接。若节点被遮蔽，用 `view=storyline` 按 node_id 审阅相邻的旧连接，旧连接只能用于纠错核对，不作为事实依据。

用户要求删除或合并整条线时 `delete_storyline(note_id=实际线ID,expected_version=当前版本)`；保留事件，返回删除版本。`list_deleted_storylines` 可分页取当前安全范围内的删除版本；`restore_storyline` 使用该版本恢复，旧证据仍需核对。删除/恢复后重新查询列表，旧cursor失效。不要通过移除全部节点留空壳。entry 撤回仍使用 note_upsert 的 status=retracted 和 change_reason。

## 自行修订笔记

先 `note_query(filters={note_id:实际ID})`。entry 更新后，返回的 `affected_storylines` 提示哪些线需要核对。
若读取到 `needs_revision`，不要沿用被遮蔽的旧结论。线级overview/stage只是返回时遮蔽，数据库原内容保留；storyline_patch不传这些字段就会保留，无须重写。

先 `note_query(filters={view:"review",note_id:实际线ID,node_id:实际节点ID})`，返回review_only、previous_conclusion及每个引用的新旧版本/结论差异；这部分旧内容只用于纠错审阅。再读当前entry的完整证据。原节点结论仍成立时：

```json
{"update":{"note_id":"实际线ID","expected_version":2,"change_reason":"核对新证据，原结论仍成立","nodes":[{"node_id":"实际节点ID","evidence":[{"note_id":"实际事件ID","version":3}],"conclusion_unchanged":true}]}}
```

将此对象作为refresh_storyline_evidence参数。服务端只更新已有事件的版本，不允许替换为别的事件；保留节点结论、分支、连接和线级元信息。节点未全部核对时，其余节点继续遮蔽；事件又被修订会拒绝旧版本。结论有变化则使用storyline_patch，不能机械确认旧结论。

## 基础数据纠错

将 `{source_ref, reason, evidence:[{chapter_number, quote}], replacement?}` 作为 `report_correction` 的 `report` 参数。
source_ref 来自摘要、人物变化、人物卡 source_refs、称谓候选 source_refs 或 lore 召回；`correction_fields` 列出允许修改的字段。
有把握时 replacement 只包含确实要改的字段；不确定具体字段替换时可以只报告，用户在审核页编辑。不得提交任意数据库路径或修改小说原文。
称谓目前支持 `replacement={remove:true}`，仅移除该章错误归属，不猜测新主人。

基础修订使用同章或更早证据；晚章的新认识应作为晚章笔记保存。审核未通过前基础内容不会改变。应用后若提示 index_updating，可读原文，等待重建完成。
