"""PROTOTYPE — throwaway. Lua literal / table emission helpers."""
import re

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


class Raw(str):
    """A string that is already Lua source and must not be quoted again."""


def lit(s):
    """Lua string literal. Prefers a long-bracket form when it avoids escaping."""
    if s is None:
        s = ""
    s = str(s)
    if ("\\" in s or '"' in s) and "\n" not in s and not s.startswith("["):
        for n in range(0, 5):
            eq = "=" * n
            closer = f"]{eq}]"
            if closer in s:
                continue
            if n == 0 and s.endswith("]"):
                continue        # `[[a]]]` would close early
            return Raw(f"[{eq}[{s}]{eq}]")
    out = "".join(_ESCAPES.get(c, c) for c in s)
    return Raw('"' + out + '"')


def val(v):
    if isinstance(v, Raw):
        return str(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v) if isinstance(v, float) else str(v)
    if isinstance(v, (list, tuple)):
        return "{ " + ", ".join(val(x) for x in v) + " }"
    if isinstance(v, dict):
        return tbl(list(v.items()))
    if v is None:
        return "nil"
    return str(lit(v))


def key(k):
    if IDENT.match(k):
        return k
    return f"[{lit(k)}]"


def tbl(fields, indent=None):
    """fields: [(name, value)]. Skips None values."""
    items = [(k, v) for k, v in fields if v is not None]
    if not items:
        return "{}"
    if indent is None:
        return "{ " + ", ".join(f"{key(k)} = {val(v)}" for k, v in items) + " }"
    pad = " " * indent
    inner = ",\n".join(f"{pad}  {key(k)} = {val(v)}" for k, v in items)
    return "{\n" + inner + f"\n{pad}}}"


def nested_tbl(tree, indent=0):
    """Render a nested dict (built by `insert`) as a multi-line Lua table."""
    pad = " " * indent
    lines = ["{"]
    for k, v in tree.items():
        if isinstance(v, dict):
            lines.append(f"{pad}  {key(k)} = {nested_tbl(v, indent + 2)},")
        else:
            lines.append(f"{pad}  {key(k)} = {val(v)},")
    lines.append(pad + "}")
    return "\n".join(lines)


def insert(tree, path, value):
    """path: ['general','col','active_border']. Returns False on a type clash."""
    node = tree
    for part in path[:-1]:
        nxt = node.get(part)
        if nxt is None:
            nxt = {}
            node[part] = nxt
        elif not isinstance(nxt, dict):
            return False
        node = nxt
    leaf = path[-1]
    if isinstance(node.get(leaf), dict):
        return False
    node[leaf] = value
    return True


def has_path(tree, path):
    node = tree
    for part in path[:-1]:
        node = node.get(part)
        if not isinstance(node, dict):
            return False
    return path[-1] in node
