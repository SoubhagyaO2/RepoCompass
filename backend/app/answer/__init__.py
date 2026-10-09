"""The grounded answer layer (Milestone 13).

Boundary (plan rules R1-R2): this package sees the repository only through an `EvidenceResponse`
and talks to a model only through `app.llm.base.LLMClient`. It does not import the index, the
vector store, the graph, the parser or anything that reads files or the network; a test checks it.
"""
