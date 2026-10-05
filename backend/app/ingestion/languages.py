"""Map file extensions to programming languages."""

from pathlib import Path

EXTENSION_LANGUAGES: dict[str, str] = {
    ".py": "Python",
    ".pyi": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".go": "Go",
    ".rs": "Rust",
    ".c": "C",
    ".h": "C",  # ambiguous with C++; good enough for a summary
    ".cpp": "C++",
    ".cc": "C++",
    ".hpp": "C++",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
    ".sh": "Shell",
}


def detect_language(path: str | Path) -> str | None:
    """Return the language for a file path, or None if it is not a known source file."""
    return EXTENSION_LANGUAGES.get(Path(path).suffix.lower())
