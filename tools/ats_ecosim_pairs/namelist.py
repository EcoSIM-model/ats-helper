"""Reading and rewriting EcoSIM (Fortran) namelist files.

Standard library only. A namelist file is a set of groups

    &ecosim
    case_name = 'ERT6_SP'
    forc_periods=1980,1989,6,1990,2021,1,2003,2021,0   ! comments start with !
    /

`parse` turns the text into {group: {key: value}} with strings, booleans, integers, floats and lists;
`rewrite` replaces the values of keys in the text and leaves everything else (comments, order) alone.
"""

import re


def _strip_comment(line):
    """the line without its ! comment (a ! inside quotes is kept)"""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == "!":
            return line[:i]
    return line


def _split_values(text):
    items, cur, quote = [], "", None
    for ch in text:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
        elif ch in "'\"":
            cur += ch
            quote = ch
        elif ch == ",":
            items.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        items.append(cur.strip())
    return items


def _value(item):
    low = item.lower()
    if item[:1] in "'\"":
        return item[1:-1]
    if low in (".true.", ".t.", "t"):
        return True
    if low in (".false.", ".f.", "f"):
        return False
    try:
        return int(item)
    except ValueError:
        pass
    try:
        return float(item.lower().replace("d", "e"))
    except ValueError:
        return item


KEY_RE = re.compile(r"(?m)^[ \t]*([A-Za-z_]\w*)[ \t]*=")


def parse(text):
    """{group: {key: value}}; a value with several items is a list"""
    lines = [_strip_comment(line) for line in text.splitlines()]
    groups, name, body = {}, None, []
    for line in lines:
        stripped = line.strip()
        if name is None:
            if stripped.startswith("&"):
                name = stripped[1:].split()[0].lower()
                body = []
                rest = stripped[1 + len(name):].strip()
                if rest:
                    body.append(rest)
            continue
        if stripped == "/":
            groups[name] = _parse_body("\n".join(body))
            name = None
        else:
            body.append(line)
    return groups


def _parse_body(body):
    out = {}
    hits = list(KEY_RE.finditer(body))
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(body)
        items = [_value(v) for v in _split_values(body[m.end():end])]
        out[m.group(1)] = items[0] if len(items) == 1 else items
    return out


def fortran(value):
    """a Python value in namelist syntax"""
    if isinstance(value, bool):
        return ".true." if value else ".false."
    if isinstance(value, (list, tuple)):
        return ",".join(fortran(v) for v in value)
    if isinstance(value, str):
        return "'" + value + "'"
    return repr(value)


def rewrite(text, values, group="ecosim"):
    """the text with the given keys of the group set to new values.

    A value runs from the key to the next key or the end of the group, so a list over several lines is
    replaced as a whole. A key that is not in the group is added at its end."""
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines)
                  if _strip_comment(line).strip().lower().startswith("&" + group)), None)
    if start is None:
        raise ValueError(f"no &{group} group in the namelist")
    end = next(i for i in range(start + 1, len(lines)) if _strip_comment(lines[i]).strip() == "/")
    block = lines[start + 1:end]
    for key, value in values.items():
        first = next((i for i, line in enumerate(block)
                      if re.match(rf"[ \t]*{re.escape(key)}[ \t]*=", _strip_comment(line))), None)
        new = f"{key} = {fortran(value)}\n"
        if first is None:
            block.append(new)
            continue
        last = first + 1
        while last < len(block):
            code = _strip_comment(block[last]).strip()
            if code and KEY_RE.match(block[last]):
                break
            if code == "" and not block[last].strip():       # a blank line ends the value
                break
            if code == "":                                   # a comment line ends it too
                break
            last += 1
        block[first:last] = [new]
    return "".join(lines[:start + 1] + block + lines[end:])
