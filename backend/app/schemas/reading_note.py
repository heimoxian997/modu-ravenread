"""Public note contract: 1-based chapters and explicit, discriminated payloads."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


Name = Annotated[str, Field(min_length=1, max_length=80)]
Text = Annotated[str, Field(min_length=1, max_length=500)]


class Evidence(StrictModel):
    chapter_number: int = Field(ge=1, strict=True)
    quote: str = Field(min_length=4, max_length=120)


class Event(StrictModel):
    type: Literal["event"]
    action: str = Field(min_length=1, max_length=100)
    participants: list[Name] = Field(min_length=1, max_length=6)


class Relation(StrictModel):
    type: Literal["relation"]
    a: Name
    b: Name
    label: Literal["亲近", "利用", "敌对", "决裂", "主仆", "合作", "亲属", "暧昧", "单恋", "仇恨"]
    state_at_chapter: Text


class Fact(StrictModel):
    type: Literal["fact"]
    subject: Name
    predicate: Literal["身份", "外貌", "性格", "能力", "关系", "持有物", "位置", "状态", "目标", "动机", "过往", "秘密"]
    value: Text


class Address(StrictModel):
    type: Literal["address"]
    speaker: Name
    target: Name
    address: Name


class EntryWrite(StrictModel):
    kind: Literal["entry"]
    note_id: str | None = None
    expected_version: int | None = Field(default=None, ge=1, strict=True)
    chapter_number: int | None = Field(default=None, ge=1, strict=True, description="本条事件或状态发生章；新笔记请填写，一条只记录一个主要变化")
    change_reason: str | None = Field(default=None, min_length=1, max_length=200)
    status: Literal["active", "disputed", "retracted"] = "active"
    title: Name
    claim: Text
    basis: Literal["explicit", "interpretation"]
    perspective: Name | None = None
    payload: Annotated[Event | Relation | Fact | Address, Field(discriminator="type")]
    evidence: list[Evidence] = Field(min_length=1, max_length=5)
    question: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def atomic_entry(self):
        if self.chapter_number is not None and any(item.chapter_number != self.chapter_number for item in self.evidence):
            raise ValueError("单章事件只能引用发生章的原文；后章回忆、印证或解释请另建后章事件，再用剧情线连接")
        if self.chapter_number is not None and len(self.claim) > 180:
            raise ValueError("章节事件的 claim 最多180字；跨章发展请拆成事件，再通过剧情线连接")
        if self.chapter_number is not None and any(isinstance(value,str) and len(value)>180 for value in self.payload.model_dump().values()):
            raise ValueError("章节事件的内容字段最多180字，请拆分多个结论")
        if self.status != "active" and not self.change_reason:
            raise ValueError("标记争议或撤回时需填写 change_reason")
        return self


class EvidenceRef(StrictModel):
    note_id: str
    version: int = Field(ge=1, strict=True)


class StoryNode(StrictModel):
    node_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    branch: Name | None = None
    chapter_number: int = Field(ge=1, strict=True)
    conclusion: Text
    basis: Literal["explicit","interpretation","mixed","unclassified"] = Field(default="unclassified",description="节点结论自身的依据性质；引用原文事件不自动意味着节点结论也是原文明示。旧记录未标注。")
    perspective: Name | None = None
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def interpretation_viewpoint(self):
        if self.basis in {"interpretation","mixed"} and not self.perspective:
            raise ValueError("解释或混合依据的节点需注明perspective")
        return self


class StoryEdge(StrictModel):
    source: str = Field(min_length=1, max_length=64)
    target: str = Field(min_length=1, max_length=64)
    relation: Literal["follows", "causes", "clue_for", "reveals", "contradicts"]
    basis: Literal["explicit", "interpretation"] = "explicit"
    reason: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def explain_connection(self):
        if self.source == self.target:
            raise ValueError("节点不能连接自身")
        if self.relation != "follows" and not self.reason:
            raise ValueError("因果、线索、揭示或反驳连接需填写 reason；无法证明因果时只用 follows")
        return self


class StorylineWrite(StrictModel):
    kind: Literal["storyline"]
    note_id: str | None = None
    expected_version: int | None = Field(default=None, ge=1, strict=True)
    change_reason: str | None = Field(default=None, min_length=1, max_length=200)
    title: Name
    category: Literal["plot", "relationship"]
    entities: list[Name] = Field(min_length=1, max_length=8)
    overview: str = Field(min_length=1, max_length=300)
    stage: str = Field(min_length=1, max_length=100)
    open_questions: list[Name] = Field(default_factory=list, max_length=5)
    nodes: list[StoryNode] = Field(min_length=1, max_length=40)
    edges: list[StoryEdge] = Field(default_factory=list, max_length=80)
    question: str | None = Field(default=None, max_length=1000)


class EdgeKey(StrictModel):
    source: str = Field(min_length=1, max_length=64)
    target: str = Field(min_length=1, max_length=64)
    relation: Literal["follows", "causes", "clue_for", "reveals", "contradicts"]


class StorylinePatch(StrictModel):
    kind: Literal["storyline_patch"]
    note_id: str = Field(min_length=1, max_length=64)
    expected_version: int = Field(ge=1, strict=True)
    change_reason: str = Field(min_length=1, max_length=200)
    title: Name | None = None
    overview: str | None = Field(default=None, min_length=1, max_length=300)
    stage: str | None = Field(default=None, min_length=1, max_length=100)
    open_questions: list[Name] | None = Field(default=None, max_length=5)
    nodes: list[StoryNode] = Field(default_factory=list, max_length=40)
    remove_node_ids: list[Name] = Field(default_factory=list, max_length=40)
    edges: list[StoryEdge] = Field(default_factory=list, max_length=80)
    remove_edges: list[EdgeKey] = Field(default_factory=list, max_length=80)

    @model_validator(mode="after")
    def targeted_patch(self):
        if any(n.node_id is None for n in self.nodes):
            raise ValueError("节点修订必须带稳定 node_id")
        ids=[n.node_id for n in self.nodes]
        if len(ids)!=len(set(ids)) or len(self.remove_node_ids)!=len(set(self.remove_node_ids)):
            raise ValueError("同一修订中节点 ID 不能重复")
        if set(ids)&set(self.remove_node_ids):
            raise ValueError("不能同时更新和删除同一节点")
        if not any((self.title,self.overview,self.stage,self.open_questions is not None,self.nodes,self.edges,self.remove_node_ids,self.remove_edges)):
            raise ValueError("修订必须至少包含一项修改")
        return self


NoteWrite = Annotated[EntryWrite | StorylineWrite | StorylinePatch, Field(discriminator="kind")]


class NoteQuery(StrictModel):
    query: str | None = Field(default=None, min_length=1, max_length=200)
    entities: list[Name] = Field(default_factory=list, max_length=8)
    entity_match: Literal["all", "any"] = "all"
    note_id: str | None = None
    version: int | None = Field(default=None, ge=1, strict=True)
    view: Literal["entry", "storyline", "both", "events", "graph", "edges", "directory", "references", "review", "excerpts"] = "graph"
    time_mode: Literal["strict","retrospective"] = Field(default="strict",description="strict按as_of_chapter限制当时可见版本；retrospective以当前已读知识回看as_of_chapter内事件，可能含后来认识。严格回看后期补录原文用view=excerpts。")
    node_id: str | None = Field(default=None, min_length=1, max_length=64)
    chapter_from: int | None = Field(default=None, ge=1, strict=True)
    chapter_to: int | None = Field(default=None, ge=1, strict=True)
    as_of_chapter: int | None = Field(default=None, ge=1, strict=True)
    limit: int | None = Field(default=None, ge=1, le=40, strict=True,
        description="directory默认40、最多40；graph/events/entry/storyline/both/edges/references/review/excerpts默认5、最多10。省略或null使用该视图默认值。")
    compact: bool = Field(default=False,strict=True,description="true时图视图仅返回graph.nodes和edges，不重复返回items；其他视图仍返回items。")
    relevance: Literal["focused","broad"] = Field(default="focused",description="focused优先具体内容，人物导航另列；broad允许仅人物相关结果。按ID或仅entities浏览不受影响。")
    min_similarity: float = Field(default=0.6,ge=0.45,le=1,description="语义余弦相似度最低值，范围0.45–1；不是置信概率，也不用于融合排名分数。")
    cursor: str | None = None

    @model_validator(mode="after")
    def require_target(self):
        if self.time_mode=="retrospective" and self.view in {"review","references","excerpts"}:
            raise ValueError("review/references/excerpts使用strict模式；事后整理请用entry/events/graph/storyline/directory等视图")
        if self.view=="excerpts" and (self.node_id or self.version):
            raise ValueError("原文摘录不返回笔记版本或剧情节点，可按entry的note_id、原文query或章节筛选")
        if self.view == "references" and any((self.query,self.entities,self.node_id)):
            raise ValueError("引用反查使用 entry 的 note_id、可选 version/章节范围；不接受 query、entities 或 node_id")
        if self.view in {"directory","references","review"} and not self.note_id:
            raise ValueError("目录和引用反查必须指定 note_id")
        if self.view == "review" and (not self.node_id or self.version or self.query or self.entities or self.cursor):
            raise ValueError("节点审阅需提供当前剧情线note_id和node_id，不接受version/query/entities/cursor")
        if self.limit is None:self.limit = 40 if self.view=="directory" else 5
        if self.view != "directory" and self.limit > 10:
            raise ValueError("除目录视图外 limit 最多10")
        if self.version and not self.note_id:
            raise ValueError("指定 version 时必须提供 note_id")
        if self.node_id and not self.note_id:
            raise ValueError("按 node_id 展开时必须同时提供剧情线 note_id")
        if self.view == "edges" and not self.note_id:
            raise ValueError("查看连线时必须指定剧情线 note_id")
        if not any((self.query, self.entities, self.note_id, self.chapter_from, self.chapter_to,self.view=="excerpts" and self.as_of_chapter is not None)):
            raise ValueError("请提供 query、entities、note_id 或章节范围；浏览名称请用 list_storylines")
        if self.chapter_from and self.chapter_to and self.chapter_from > self.chapter_to:
            raise ValueError("chapter_from 不能大于 chapter_to")
        return self


class StorylineList(StrictModel):
    query: str | None = Field(default=None, min_length=1, max_length=200)
    time_mode: Literal["strict","retrospective"] = Field(default="strict",description="strict为当时视角，retrospective按当前已读认识回看as_of_chapter内事件。")
    entities: list[Name] = Field(default_factory=list, max_length=8)
    entity_match: Literal["all", "any"] = "all"
    relevance: Literal["focused","broad"] = "focused"
    min_similarity: float = Field(default=0.6,ge=0.45,le=1,description="语义余弦相似度最低值，范围0.45–1；不是置信概率。")
    as_of_chapter: int | None = Field(default=None, ge=1, strict=True)
    limit: int = Field(default=5, ge=1, le=10, strict=True)
    cursor: str | None = None


class ReviewedImport(StrictModel):
    legacy_id: int = Field(ge=1, strict=True)
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    note: EntryWrite


class StorylineDelete(StrictModel):
    expected_version: int = Field(ge=1, strict=True)


class StorylineMove(StrictModel):
    expected_version: int = Field(ge=1, strict=True)
    target_note_id: str = Field(min_length=1, max_length=64)
    target_expected_version: int = Field(ge=1, strict=True)
    connect_after: str | None = Field(default=None, min_length=1, max_length=64)


class StorylineAction(StrictModel):
    note_id: str = Field(min_length=1, max_length=64)
    expected_version: int = Field(ge=1, strict=True)


class NoteDuplicateQuery(StrictModel):
    chapter_number: int | None = Field(default=None,ge=1,strict=True,description="可选，限定单章；与chapter_from/chapter_to互斥。均省略时搜索已读范围。")
    chapter_from: int | None = Field(default=None,ge=1,strict=True)
    chapter_to: int | None = Field(default=None,ge=1,strict=True)
    entities: list[Name] = Field(default_factory=list, max_length=8)
    query: str | None = Field(default=None, min_length=1, max_length=180)
    quote: str | None = Field(default=None, min_length=4, max_length=120)
    limit: int = Field(default=5, ge=1, le=8, strict=True)

    @model_validator(mode="after")
    def has_clue(self):
        if self.chapter_number is not None and (self.chapter_from is not None or self.chapter_to is not None):
            raise ValueError("chapter_number不能与chapter_from/chapter_to同时使用")
        if self.chapter_from and self.chapter_to and self.chapter_from>self.chapter_to:
            raise ValueError("chapter_from不能大于chapter_to")
        if not any((self.entities,self.query,self.quote)):
            raise ValueError("请至少提供 entities、query 或 quote 中的一项查重线索")
        return self


class NodeEvidenceConfirmation(StrictModel):
    node_id: str = Field(min_length=1,max_length=64)
    evidence: list[EvidenceRef] = Field(min_length=1,max_length=8,description="审阅后的当前事件版本；只能更新已有事件的版本，不能换成其他事件。")
    conclusion_unchanged: Literal[True] = Field(description="确认已核对新证据，原节点结论仍成立；结论有变化时应使用storyline_patch。")


class StorylineEvidenceRefresh(StorylineAction):
    nodes: list[NodeEvidenceConfirmation] = Field(min_length=1,max_length=40)
    change_reason: str = Field(min_length=1,max_length=200)

    @model_validator(mode="after")
    def unique_nodes(self):
        if len({n.node_id for n in self.nodes})!=len(self.nodes):
            raise ValueError("同一节点不能重复确认")
        return self
