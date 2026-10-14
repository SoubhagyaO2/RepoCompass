"""The system prompt of the product (HTTP API and MCP server): the frozen M13 prompt plus one rule
that says what counts as evidence for a question about the code's language.

Why: asked what language a repository is written in, the model sometimes abstained because no
evidence item "states" it, although every item is a `.py` file of Python code. The rule makes the
path and code of the items shown count as evidence for that one kind of question, keeps the claim
to the files shown, and leaves frameworks and libraries to evidence that names or imports them.

Every published experiment used the frozen prompt (`app.answer.prompt`, unchanged). Results are
never reported for this prompt; its effect was only checked by hand (see docs/PROJECT_REPORT.md).
"""

import hashlib

from app.answer.prompt import SYSTEM_PROMPT

ADDED_RULE = """\
6a. The path of an evidence item is part of the evidence, and so is its code. A source file with a
   `.py` extension is Python, so when asked what language the code is written in you may answer
   from the paths and code of the items shown, and cite them. You can only speak for the files
   shown: say so instead of claiming it of the whole repository. Do not name a framework or
   library unless an item names or imports it; an extension or a file name alone does not show
   one. If a question has two parts and the evidence settles one of them, answer that part with
   status "partial" and put what is left in "missing"."""

_HEAD, _TAIL = SYSTEM_PROMPT.split("\n7. Respond")
PRODUCT_PROMPT = f"{_HEAD}\n{ADDED_RULE}\n7. Respond{_TAIL}"
PRODUCT_PROMPT_SHA256 = hashlib.sha256(PRODUCT_PROMPT.encode("utf-8")).hexdigest()
