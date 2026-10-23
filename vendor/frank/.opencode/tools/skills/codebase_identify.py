import os
import re
from collections import Counter
from ..skill import skill
from ..progress import push

LANG_MAP = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript React", ".jsx": "JavaScript React",
    ".java": "Java", ".go": "Go", ".rs": "Rust",
    ".rb": "Ruby", ".php": "PHP", ".c": "C", ".h": "C",
    ".cpp": "C++", ".hpp": "C++", ".cc": "C++", ".cxx": "C++",
    ".cs": "C#", ".swift": "Swift", ".kt": "Kotlin",
    ".scala": "Scala", ".r": "R", ".m": "Objective-C",
    ".mm": "Objective-C", ".pl": "Perl", ".pm": "Perl",
    ".lua": "Lua", ".sh": "Shell", ".bash": "Shell",
    ".zsh": "Shell", ".fish": "Shell", ".ps1": "PowerShell",
    ".sql": "SQL", ".html": "HTML", ".htm": "HTML",
    ".css": "CSS", ".scss": "SCSS", ".less": "Less",
    ".sass": "Sass", ".styl": "Stylus", ".vue": "Vue",
    ".svelte": "Svelte", ".astro": "Astro",
    ".yaml": "YAML", ".yml": "YAML", ".json": "JSON",
    ".xml": "XML", ".toml": "TOML", ".ini": "INI",
    ".cfg": "INI", ".conf": "INI", ".md": "Markdown",
    ".rst": "reStructuredText", ".tex": "LaTeX",
    ".dockerfile": "Dockerfile", "Dockerfile": "Dockerfile",
    ".tf": "Terraform", ".hcl": "Terraform",
    ".sqlite": "SQLite", ".db": "SQLite",
}

FRAMEWORK_PATTERNS = {
    "requirements.txt": [
        (r"^(django|flask|fastapi|bottle|pyramid|tornado|aiohttp)", "Python"),
        (r"^(react|vue|angular|next|nuxt|svelte|remix)", "JavaScript"),
    ],
    "package.json": [
        (r'"react"', "React"), (r'"vue"', "Vue"),
        (r'"@angular/core"', "Angular"), (r'"next"', "Next.js"),
        (r'"nuxt"', "Nuxt"), (r'"svelte"', "Svelte"),
        (r'"express"', "Express"), (r'"django"', "Django"),
        (r'"flask"', "Flask"), (r'"fastify"', "Fastify"),
        (r'"@nestjs/core"', "NestJS"), (r'"remix"', "Remix"),
        (r'"gatsby"', "Gatsby"), (r'"electron"', "Electron"),
    ],
    "Cargo.toml": [
        (r'actix-web', "Actix Web"), (r'rocket', "Rocket"),
        (r'axum', "Axum"), (r'warp', "Warp"),
        (r'tide', "Tide"), (r'poem', "Poem"),
    ],
    "go.mod": [
        (r'gin-gonic/gin', "Gin"), (r'labstack/echo', "Echo"),
        (r'gofiber/fiber', "Fiber"), (r'gorilla/mux', "Gorilla Mux"),
        (r'beego', "Beego"), (r'revel', "Revel"),
    ],
    "Gemfile": [
        (r'rails', "Ruby on Rails"), (r'sinatra', "Sinatra"),
        (r'hanami', "Hanami"), (r'rack', "Rack"),
    ],
    "composer.json": [
        (r'laravel', "Laravel"), (r'symfony', "Symfony"),
        (r'cakephp', "CakePHP"), (r'codeigniter', "CodeIgniter"),
        (r'phalcon', "Phalcon"),
    ],
}

ENTRY_POINTS = [
    "main.py", "app.py", "index.py", "server.py",
    "index.js", "index.ts", "app.js", "app.ts",
    "main.go", "main.rs", "main.c", "main.cpp",
    "server.js", "server.ts", "server.go",
    "cli.py", "cli.js", "cli.ts",
    "manage.py", "wsgi.py", "asgi.py",
    "next.config.js", "next.config.ts",
    "nuxt.config.js", "nuxt.config.ts",
    "svelte.config.js", "vite.config.ts",
    "webpack.config.js", "rollup.config.js",
]


def _scan_directory(path):
    files = []
    total_lines = 0
    skipped_dirs = {
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        "env", ".env", ".idea", ".vscode", ".next", ".nuxt",
        "dist", "build", "target", "vendor", ".cache", "tmp",
        "logs", ".terraform", ".serverless", "coverage",
        ".opencode",  # our own tooling
    }
    try:
        for root, dirs, names in os.walk(path):
            dirs[:] = [d for d in dirs if d not in skipped_dirs and not d.startswith(".")]
            for name in names:
                fpath = os.path.join(root, name)
                rel = os.path.relpath(fpath, path)
                try:
                    stat = os.stat(fpath)
                    if stat.st_size > 1_000_000:
                        files.append((rel, 0, "binary"))
                        continue
                    with open(fpath, "rb") as f:
                        chunk = f.read(1024)
                    if b"\0" in chunk:
                        files.append((rel, 0, "binary"))
                        continue
                    with open(fpath, "r", errors="ignore") as f:
                        lines = len(f.readlines())
                    total_lines += lines
                    ext = os.path.splitext(name)[1].lower()
                    lang = LANG_MAP.get(ext, LANG_MAP.get(name, "Other"))
                    files.append((rel, lines, lang))
                except (OSError, IOError):
                    continue
    except (OSError, IOError) as e:
        return [], 0, str(e)
    return files, total_lines, None


def _detect_frameworks(path):
    frameworks = []
    for fname, patterns in FRAMEWORK_PATTERNS.items():
        fpath = os.path.join(path, fname)
        if os.path.isfile(fpath):
            try:
                with open(fpath, "r", errors="ignore") as f:
                    content = f.read()
                for pattern, framework in patterns:
                    if re.search(pattern, content, re.MULTILINE):
                        frameworks.append(framework)
            except (OSError, IOError):
                continue
    return sorted(set(frameworks))


@skill(description="Analyze a codebase directory to detect languages, frameworks, structure, and entry points. Use this first to understand the target before running other scanners.")
async def codebase_identify(path: str = ".") -> str:
    push("codebase_identify", "start", f"scanning {path}")
    abspath = os.path.abspath(path)
    if not os.path.isdir(abspath):
        return '{"error": "directory not found"}'

    files, total_lines, err = _scan_directory(abspath)

    if err:
        return f'{{"error": "{err}"}}'

    lang_counts = Counter(f[2] for f in files if f[2] != "Other")
    lang_files = lang_counts.most_common()
    total_files = len(files)

    binary_files = sum(1 for f in files if f[2] == "binary")
    text_files = total_files - binary_files

    extensions = Counter(
        os.path.splitext(f[0])[1].lower() or os.path.basename(f[0]).lower()
        for f in files if f[2] != "binary"
    )

    pkg_managers = []
    for fname in ["requirements.txt", "package.json", "Cargo.toml", "go.mod",
                   "go.sum", "Gemfile", "composer.json", "Pipfile",
                   "pyproject.toml", "Cargo.lock", "yarn.lock", "pnpm-lock.yaml",
                   "bun.lockb"]:
        if os.path.isfile(os.path.join(abspath, fname)):
            pkg_managers.append(fname)

    entry_found = []
    for ep in ENTRY_POINTS:
        if os.path.isfile(os.path.join(abspath, ep)):
            entry_found.append(ep)

    frameworks = _detect_frameworks(abspath)

    result = {
        "path": abspath,
        "total_files": total_files,
        "text_files": text_files,
        "binary_files": binary_files,
        "total_lines": total_lines,
        "languages": [{"name": l, "files": c} for l, c in lang_files],
        "extensions": sorted(extensions.items()),
        "package_managers": pkg_managers,
        "frameworks": frameworks,
        "entry_points": entry_found,
    }

    out = __import__("json").dumps(result, indent=2)
    push("codebase_identify", "complete", f"{total_files} files, {total_lines} lines, {len(lang_files)} languages")
    return out
