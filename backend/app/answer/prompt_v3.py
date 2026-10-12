"""Prompt-v3 candidates of Milestone 15 (plan section 4). All three keep rules 1-6 of the frozen
M13 prompt verbatim and replace only rule 7 (the response shape). They are written before any
model output on the development questions is seen, and they quote no benchmark question."""

import hashlib

from app.answer.prompt import SYSTEM_PROMPT

BASE = SYSTEM_PROMPT.split("\n7. Respond")[0]

SHAPE = """\
   {"premise": "<what the question assumes to exist or be true, in one sentence>",
    "premise_supported": true | false,
    "evidence_relation": "direct" | "related_only" | "none",
    "status": "answered" | "partial" | "insufficient_evidence",
    "claims": [{"text": "<one statement>", "citations": ["E1", "E3"]}],
    "missing": "<what the evidence does not cover, or an empty string>"}"""

C1 = f"""{BASE}
7. First state the premise of the question: what it assumes the repository has or does. Then say
   whether the evidence supports that premise. If the evidence does not show the assumed feature,
   set "premise_supported" to false and use status "insufficient_evidence" with no claims. Otherwise
   answer as before.
8. Respond with one JSON object and nothing else, with exactly this shape:
{SHAPE}
   "evidence_relation" is "direct" if the evidence shows the thing the question asks about, and
   "related_only" or "none" otherwise; a claim needs "direct" and a supported premise."""

C2 = f"""{BASE}
7. Before answering, work out the premise of the question: the feature, behaviour or component it
   takes for granted. Then compare it with the evidence:
   - "direct": an evidence item shows that very feature, behaviour or component (by name or by
     what the code does).
   - "related_only": the evidence shows something nearby (the same module, a similar mechanism, a
     function with a related name) but not the assumed feature itself.
   - "none": the evidence says nothing relevant.
   Similar vocabulary, a neighbouring function or a graph connection is not direct evidence. If
   the evidence is "related_only" or "none", or the premise is not shown to be true, set
   "premise_supported" to false where the premise itself is unsupported, use status
   "insufficient_evidence", write no claims, and say in "missing" what the evidence does not show.
   A question about something the repository really has, even a narrow one, is answered from the
   evidence as usual.
8. Respond with one JSON object and nothing else, with exactly this shape:
{SHAPE}"""

C3 = f"""{BASE}
7. Work in this order and record each step in the JSON fields.
   Step 1, "premise": write what the question assumes exists or is true.
   Step 2, "evidence_relation": look for an evidence item that shows exactly that. Answer "direct"
   only if you can point to such an item. Answer "related_only" if you can only find code that
   resembles it, sits next to it or shares words with it. Answer "none" if nothing is relevant.
   Step 3, "premise_supported": true only when step 2 is "direct".
   Step 4, "status": "answered" or "partial" only when the premise is supported and the relation is
   "direct". Otherwise "insufficient_evidence", with no claims and a "missing" that names what is
   assumed but not shown. Do not answer by describing the related code instead: that would
   present an assumption as a finding.
   Do not become cautious about real features: when the evidence does show what the question
   assumes, answer it fully, with citations.
8. Respond with one JSON object and nothing else, with exactly this shape:
{SHAPE}"""

CANDIDATES = {"c1": C1, "c2": C2, "c3": C3}


def sha256(name: str) -> str:
    return hashlib.sha256(CANDIDATES[name].encode("utf-8")).hexdigest()
