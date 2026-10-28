"""Evidence contracts for role preparation; independent of the database and AI SDKs."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RoleTarget(Contract):
    book_key: Name
    name: Name
    # Callers supply only aliases already disambiguated for this target.
    aliases: list[Name] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def distinct_aliases(self):
        self.aliases = list(dict.fromkeys(a for a in self.aliases if a != self.name))
        return self


class RoleScene(Contract):
    scene_id: Name
    book_key: Name
    chapter_number: int = Field(ge=1)
    char_start: int = Field(default=0, ge=0)
    kind: Literal["main", "extra"] = "main"
    split: Literal["source", "holdout"] = "source"
    # Whitespace is preserved because evidence offsets address this exact text.
    text: str = Field(min_length=1, max_length=20000)

    @model_validator(mode="after")
    def nonblank_text(self):
        if not self.text.strip():
            raise ValueError("scene text must not be blank")
        return self


class QuoteReference(Contract):
    scene_id: Name
    quote: str = Field(min_length=4, max_length=400)


class ParagraphReference(Contract):
    paragraph_id: Name
    opening: str | None = Field(default=None, max_length=80)


ObservationKind = Literal[
    "identity", "state", "speech", "action", "belief", "relationship",
    "value", "boundary", "counterexample",
]
Knowledge = Literal["experienced", "learned", "believed", "reader_only", "unknown", "not_applicable"]
Attribution = Literal["self_statement", "action", "narrator", "other_statement", "inference"]


class ExtractedObservation(Contract):
    kind: ObservationKind
    claim: Text
    attribution: Attribution
    knowledge: Knowledge
    speaker: Name | None = None
    evidence: list[QuoteReference | ParagraphReference] = Field(min_length=1, max_length=4)


class ExtractionBatch(Contract):
    character: Name
    observations: list[ExtractedObservation] = Field(default_factory=list, max_length=30)
    gaps: list[Text] = Field(default_factory=list, max_length=12)


class ParagraphObservation(ExtractedObservation):
    evidence: list[ParagraphReference] = Field(min_length=1, max_length=4)


class ParagraphExtractionBatch(ExtractionBatch):
    # Historical records and manual edits keep the wider legacy contract.
    observations: list[ParagraphObservation] = Field(default_factory=list, max_length=12)


class VerifiedQuote(Contract):
    scene_id: Name
    chapter_number: int = Field(ge=1)
    source_hash: str
    quote: str
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=1)
    quote_verified: Literal[True] = True
    match_method: Literal["exact", "format_normalized", "paragraph", "opening_sentence"] = "exact"
    requested_quote: str | None = None
    paragraph_id: str | None = None
    location_warning: str | None = None


class GroundedObservation(Contract):
    observation_id: str
    kind: ObservationKind
    claim: Text
    attribution: Attribution
    knowledge: Knowledge
    speaker: Name | None = None
    # Text matching does not prove the model identified the speaker or meaning.
    semantic_attribution: Literal["model_assigned"] = "model_assigned"
    evidence: list[VerifiedQuote]


class GroundedStatement(Contract):
    text: Text
    evidence_ids: list[Name] = Field(min_length=1, max_length=8)


class BehaviorRule(Contract):
    situation: Text
    response: Text
    exceptions: list[Text] = Field(default_factory=list, max_length=4)
    evidence_ids: list[Name] = Field(min_length=1, max_length=8)


class RoleProfile(Contract):
    character: Name
    identity: list[GroundedStatement] = Field(default_factory=list, max_length=6)
    current_state: list[GroundedStatement] = Field(default_factory=list, max_length=6)
    behavior_rules: list[BehaviorRule] = Field(default_factory=list, max_length=12)
    expression: list[GroundedStatement] = Field(default_factory=list, max_length=8)
    relationships: list[GroundedStatement] = Field(default_factory=list, max_length=10)
    known_facts: list[GroundedStatement] = Field(default_factory=list, max_length=12)
    beliefs: list[GroundedStatement] = Field(default_factory=list, max_length=8)
    voice_evidence_ids: list[Name] = Field(default_factory=list, max_length=8)
    limitations: list[Text] = Field(default_factory=list, max_length=12)
