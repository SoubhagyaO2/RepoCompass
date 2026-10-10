"""The evidence schema, version 1 (plan: evaluation/plans/m12-evidence-api.md).

Every field is always present (`None` when not applicable). Floats are rounded to 6 decimals only
when serialised; ranking never sees a rounded value.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, PlainSerializer

SCHEMA_VERSION = 1

Float6 = Annotated[float, PlainSerializer(lambda v: round(v, 6), return_type=float)]
SymbolStatus = Literal["exact", "module_level", "unparsed"]
Relationship = Literal["calls", "called_by", "inherits", "imports", "imported_by", "defines"]

NOTES = {
    "scores": (
        "rrf is the fused ranking score. bm25 and embedding scores are raw values of different "
        "kinds: they are not comparable with each other, nor across queries."
    ),
    "graph": (
        "Graph annotations describe structural relationships only. Their presence is not "
        "evidence that the related symbol is relevant to the query. Edges with rule "
        "'unique-name' are name matches (audited precision 93%)."
    ),
    "lines": "1-based, inclusive, counted in the file version identified by file_sha256.",
}


class ComponentScore(BaseModel):
    rank: int  # among the component's unique files
    score: Float6  # the component's raw score
    line_start: int
    line_end: int


class Scores(BaseModel):
    rrf: Float6
    bm25: ComponentScore | None
    embedding: ComponentScore | None


class SymbolInfo(BaseModel):
    qualified_name: str
    name: str
    kind: Literal["function", "method", "class"]
    node_id: str  # the graph node
    line_start: int  # the symbol's full extent (a class header chunk is shorter than its class)
    line_end: int


class GraphRelationship(BaseModel):
    relationship: Relationship
    other: str  # graph node id of the other end
    other_file: str
    line: int | None  # line of the statement that created the edge (in the calling/importing file)
    rule: str  # how the edge was resolved


class GraphLink(BaseModel):
    """A relationship to the symbol or file of another item of the same response."""

    relationship: Relationship
    other_rank: int
    line: int | None
    rule: str


class GraphDegree(BaseModel):
    calls: int = 0
    called_by: int = 0
    inherits: int = 0
    imports: int = 0
    imported_by: int = 0
    defines: int = 0


class GraphEvidence(BaseModel):
    node: str  # the graph node the annotations are about (the symbol, else the file)
    degree: GraphDegree
    relationships: list[GraphRelationship]  # at most MAX_RELATIONSHIPS
    truncated: bool
    links_to_other_results: list[GraphLink]


class EvidenceItem(BaseModel):
    rank: int
    file: str
    file_sha256: str
    category: str
    line_start: int
    line_end: int
    symbol: SymbolInfo | None
    symbol_status: SymbolStatus
    scores: Scores
    graph: GraphEvidence | None
    text: str | None


class IndexInfo(BaseModel):
    repository: str
    revision: str | None
    view: str
    files: int
    chunks: int
    config_sha256: str
    manifest_sha256: str  # hash of the configuration and every indexed file's hash


class RetrievalInfo(BaseModel):
    method: str
    rrf_k: int
    depth: int
    components: list[str]


class EvidenceResponse(BaseModel):
    schema_version: int = SCHEMA_VERSION
    query: str
    index: IndexInfo
    retrieval: RetrievalInfo
    notes: dict[str, str] = NOTES
    evidence: list[EvidenceItem]
    timing_ms: float | None = None  # outside the deterministic part: never compare it


# --- schema v2 (Milestone 14): additional chunks per file ------------------------------------


class ChunkComponentScore(BaseModel):
    rank: int  # 1-based position in the component's chunk list (not among files)
    score: Float6  # the component's raw score


class ChunkScores(BaseModel):
    rrf_chunk: Float6  # sum over components of 1 / (60 + position); used only to choose and order
    bm25: ChunkComponentScore | None
    embedding: ChunkComponentScore | None


class AdditionalChunk(BaseModel):
    """Another chunk of an already retrieved file, taken from what the components returned."""

    rank_in_file: int  # 1 = the best additional chunk of this file
    line_start: int
    line_end: int
    symbol: SymbolInfo | None
    symbol_status: SymbolStatus
    scores: ChunkScores
    text: str | None


class EvidenceItemV2(EvidenceItem):
    """Every v1 field unchanged, plus the additional chunks (possibly none)."""

    additional_chunks: list[AdditionalChunk]


class EvidenceResponseV2(EvidenceResponse):
    schema_version: int = 2
    extra_chunks: int  # the N the request asked for (the most chunks added per file)
    evidence: list[EvidenceItemV2]  # type: ignore[assignment]
