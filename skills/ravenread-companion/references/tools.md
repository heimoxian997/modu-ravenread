# RavenRead 工具调用

这是结构示例，人物与情节均为合成。实际以 MCP 返回的 schema、ID、版本和章节为准。

## 查事件与分支

```json
{"filters":{"query":"甲某与乙某为何失去信任","view":"graph","limit":5}}
```

query 可以换成“甲某为什么不相信乙某”：人物关联、关键词与已就绪的向量一起检索，不要求整句一致。无法保证任意问法都命中。看 retrieval.semantic 可区分 ready（已参与）、partial（部分就绪）、building/warming（后台处理中）、unavailable（人物/关键词兜底）。索引后台增量维护，不需要 agent 建索引。

按人物导航不必组织完整问句：

```json
{"filters":{"entities":["甲某"],"view":"graph","limit":5}}
```

仅浏览线名用 `list_storylines(filters={entities:["甲某"],limit:5})`，再按返回的 note_id 展开。剧情线参与人物会结合有效引用事件补齐。

`note_query` 默认图视图：`graph.nodes` 是本页节点，`graph.edges` 是本页可见节点间的连接；`items` 是同一页的兼容表示，不要重复阅读两遍。
`known_by_chapter` 是结论可知边界，`chapter_number` 是发生章，`evidence_chapters` 是原文证据章。

按 `note_id` 展开剧情线；按 `note_id + node_id` 查看该节点及相邻分支。按 entry 的 `note_id + version` 读证据详情。`view=events` 只取精简章节事件，`view=entry` 取证据笔记；`view=storyline` 取剧情线。
显式 entities 是 AND 筛选；仅传 query 时，自动提取的人物线索按 OR 寻找相关记录。多人问题不要默认要求每条笔记都包含所有人。

翻页时保留筛选条件并原样传回 `cursor`。章节范围含两端；历史查询用 `as_of_chapter`，不能大于安全章。

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
其他 payload 类型：

- relation：a、b、label、state_at_chapter。label 用工具 schema 枚举。
- fact：subject、predicate、value。predicate 用 schema 枚举。
- address：speaker、target、address。

解释性记录增加 `basis=interpretation` 和 `perspective`。新记录无 ID；修订保留原 `note_id`，填当前 `expected_version` 和 `change_reason`。

## 剧情线

storyline 输入包括 title、category(plot/relationship)、entities、overview、stage、nodes、edges，可选 open_questions。
节点：`{node_id, chapter_number, conclusion, branch?, evidence:[{note_id, version}]}`。
连接：`{source, target, relation, basis, reason?}`，端点是本线 node_id。

例如节点 `discover` 分别连接 `investigate-a` 与 `investigate-b`，两者再连接 `merge`，即可表达分叉和汇合。该示例只说明 ID 结构，不意味着真实剧情存在这些关系。
ID 一旦使用就保留；修订节点文字或证据时不换 ID。新建或整体重整提交完整 nodes/edges；局部修订使用下面的 storyline_patch，服务端保留其余分支。

## 局部更新剧情图

note_upsert 的 note 可用 `{kind:"storyline_patch", note_id:实际线ID, expected_version:当前版本, change_reason:"修订原因", nodes:[要改的完整节点], edges:[新增或替换的连接]}`。
可选 remove_node_ids、remove_edges（source/target/relation 三元组），以及 title/overview/stage/open_questions。节点更新必须带 node_id。
服务端合并并检查版本，无需重抄未改动节点和连接。分批核对关联错误时，其他待修订节点继续遮蔽，不会被当作正确结论返回。

精确查线时返回 storyline 头信息；`view=edges` 可分页读该线连接。若节点被遮蔽，用 `view=storyline` 按 node_id 审阅相邻的旧连接，旧连接只能用于纠错核对，不作为事实依据。

## 自行修订笔记

先 `note_query(filters={note_id:实际ID})`。entry 更新后，返回的 `affected_storylines` 提示哪些线需要核对。
若读取到 `needs_revision`，不要沿用被遮蔽的旧结论；读最新 entry，再据原文重写节点结论和连线，提交新的剧情线版本。

## 基础数据纠错

将 `{source_ref, reason, evidence:[{chapter_number, quote}], replacement?}` 作为 `report_correction` 的 `report` 参数。
source_ref 来自摘要、人物变化、人物卡 source_refs、称谓候选 source_refs 或 lore 召回；`correction_fields` 列出允许修改的字段。
有把握时 replacement 只包含确实要改的字段；不确定具体字段替换时可以只报告，用户在审核页编辑。不得提交任意数据库路径或修改小说原文。
称谓目前支持 `replacement={remove:true}`，仅移除该章错误归属，不猜测新主人。

基础修订使用同章或更早证据；晚章的新认识应作为晚章笔记保存。审核未通过前基础内容不会改变。应用后若提示 index_updating，可读原文，等待重建完成。
