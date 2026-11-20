"""Edits of ATS input files by parameter-list path, and ElementTree helpers.

Edits are applied to the text of the file, so everything else (comments, indentation, commented-out
blocks) is kept exactly. A path is the names of the nested ParameterLists below the root list and the
name of the Parameter, joined by "/", for example

    PKs/EcoSIM for ATS/engine input file

and "**" stands for any number of lists, for example

    state/evaluators/surface-LAI/**/function-constant/value

A path must match exactly one parameter; anything else is an error that names the path, so a
missing or ambiguous parameter is never skipped silently. Text inside XML comments is ignored.

Edit forms (dictionaries):
    {"set": PATH, "value": V}                      replace the value of the parameter
    {"set": PATH, "value": P, "relative_to_run_dir": true, "max_length": 128}
                                                   a file path, written relative to the run directory
    {"add_after": PATH, "params": [{"name": N, "type": T, "value": V}, ...]}
                                                   new parameters on the lines after the parameter
Values may contain "{name}" placeholders, filled from the variables of the test; a value that is
exactly one placeholder keeps its type. Values are written according to the parameter's own type
attribute (double, int, bool, string).
"""

import io
import os
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

TOKEN_RE = re.compile(
    r"<!--.*?-->|<ParameterList\b[^>]*?/>|<ParameterList\b[^>]*?>|</ParameterList\s*>|<Parameter\b[^>]*?/>",
    re.S)
NAME_RE = re.compile(r'\bname="([^"]*)"')
TYPE_RE = re.compile(r'\btype="([^"]*)"')
VALUE_RE = re.compile(r'(\bvalue=")([^"]*)(")')


class EditError(ValueError):
    pass


def scan(text):
    """[(path, start, end, tag)] of every Parameter outside comments; path excludes the root list"""
    stack, found = [], []
    for m in TOKEN_RE.finditer(text):
        tok = m.group(0)
        if tok.startswith("<!--"):
            continue
        if tok.startswith("</ParameterList"):
            if stack:
                stack.pop()
            continue
        name = NAME_RE.search(tok)
        if name is None:
            raise EditError(f"element without a name near offset {m.start()}: {tok[:60]}")
        if tok.startswith("<ParameterList"):
            if not tok.rstrip().endswith("/>"):
                stack.append(name.group(1))
            continue
        found.append((tuple(stack[1:]) + (name.group(1),), m.start(), m.end(), tok))
    return found


def _match(pattern, path):
    if not pattern:
        return not path
    head = pattern[0]
    if head == "**":
        return any(_match(pattern[1:], path[i:]) for i in range(len(path) + 1))
    return bool(path) and head == path[0] and _match(pattern[1:], path[1:])


def find_one(params, pattern):
    segments = pattern.split("/")
    hits = [p for p in params if _match(segments, list(p[0]))]
    if len(hits) != 1:
        raise EditError(f"path {pattern!r}: expected exactly one parameter, found {len(hits)}")
    return hits[0]


def resolve(value, variables):
    """fill {name} placeholders; a value that is only one placeholder keeps the variable's type"""
    if isinstance(value, str):
        whole = re.fullmatch(r"\{(\w+)\}", value)
        try:
            return variables[whole.group(1)] if whole else value.format(**variables)
        except KeyError as err:
            raise EditError(f"unknown variable {err} in value {value!r}; known: {sorted(variables)}")
    return value


def format_value(value, type_):
    if type_ == "double":
        return repr(float(value))
    if type_ == "int":
        return str(int(float(value)))
    if type_ == "bool":
        return "true" if (value is True or str(value).lower() == "true") else "false"
    return str(value)


def edit_text(text, edits, variables=None, run_dir=None):
    """apply the edits to the text of an input file; all paths are resolved on the original text"""
    variables = variables or {}
    params = scan(text)
    changes = []                                    # (start, end, replacement, order)
    for order, edit in enumerate(edits):
        if "set" in edit:
            _, start, end, tag = find_one(params, edit["set"])
            type_ = TYPE_RE.search(tag).group(1)
            value = resolve(edit["value"], variables)
            if edit.get("relative_to_run_dir"):
                if run_dir is None:
                    raise EditError(f"{edit['set']!r}: relative_to_run_dir needs a run directory")
                value = os.path.relpath(str(value), str(run_dir))
                limit = edit.get("max_length")
                if limit and len(value) > limit:
                    raise EditError(f"{edit['set']!r}: path longer than {limit} characters: {value}")
            new = escape(format_value(value, type_), {'"': "&quot;"})
            changes.append((start, end, VALUE_RE.sub(lambda m: m.group(1) + new + m.group(3), tag, count=1), order))
        elif "add_after" in edit:
            path, start, end, tag = find_one(params, edit["add_after"])
            indent = text[text.rfind("\n", 0, start) + 1:start]
            indent = indent if not indent.strip() else ""
            lines = []
            for p in edit["params"]:
                if any(q[0] == path[:-1] + (p["name"],) for q in params):
                    raise EditError(f"{edit['add_after']!r}: parameter {p['name']!r} already exists")
                value = format_value(resolve(p["value"], variables), p["type"])
                lines.append(f'\n{indent}<Parameter name="{p["name"]}" type="{p["type"]}" value="{escape(value, {chr(34): "&quot;"})}"/>')
            changes.append((end, end, "".join(lines), order))
        else:
            raise EditError(f"unknown edit {sorted(edit)}; use 'set' or 'add_after'")
    # later positions first; at one position the earlier edit is applied last so it comes first
    for start, end, new, _ in sorted(changes, key=lambda c: (c[0], c[3]), reverse=True):
        text = text[:start] + new + text[end:]
    return text


# ---------------------------------------------------------------------------
# ElementTree helpers (reading parameters out of a rendered input)
# ---------------------------------------------------------------------------
def parse_xml(text):
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    return ET.parse(io.StringIO(text), parser)


def plist(node, *names):
    for name in names:
        found = [c for c in node if c.tag == "ParameterList" and c.get("name") == name]
        if len(found) != 1:
            raise KeyError(f"ParameterList {name!r} not found under {node.get('name')!r}")
        node = found[0]
    return node


def param(node, name):
    found = [c for c in node if c.tag == "Parameter" and c.get("name") == name]
    if len(found) != 1:
        raise KeyError(f"Parameter {name!r} not found under {node.get('name')!r}")
    return found[0]


def constant_value(evaluator):
    """the value of the single function-constant in an independent-variable evaluator"""
    vals = [p for p in evaluator.iter("Parameter") if p.get("name") == "value"]
    if len(vals) != 1:
        raise ValueError(f"expected one constant in {evaluator.get('name')!r}")
    return vals[0]


def array_values(parameter):
    return [float(x) for x in parameter.get("value").strip("{}").split(",")]
