from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Token:
    kind: str
    value: str
    line: int
    col: int


class LexError(Exception):
    pass


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    line = 1
    col = 1
    i = 0
    n = len(source)

    while i < n:
        ch = source[i]

        if ch == "\n":
            line += 1
            col = 1
            i += 1
            continue

        if ch.isspace():
            col += 1
            i += 1
            continue

        if ch == ";":
            while i < n and source[i] != "\n":
                i += 1
            continue

        if ch == "(":
            tokens.append(Token("LPAREN", "(", line, col))
            i += 1
            col += 1
            continue

        if ch == ")":
            tokens.append(Token("RPAREN", ")", line, col))
            i += 1
            col += 1
            continue

        if ch == '"':
            start_line, start_col = line, col
            i += 1
            col += 1
            buf: list[str] = []
            while i < n and source[i] != '"':
                if source[i] == "\\" and i + 1 < n:
                    nxt = source[i + 1]
                    if nxt == "n":
                        buf.append("\n")
                    elif nxt == "t":
                        buf.append("\t")
                    elif nxt == "\\":
                        buf.append("\\")
                    elif nxt == '"':
                        buf.append('"')
                    else:
                        buf.append(nxt)
                    i += 2
                    col += 2
                else:
                    if source[i] == "\n":
                        line += 1
                        col = 1
                    else:
                        col += 1
                    buf.append(source[i])
                    i += 1
            if i >= n:
                raise LexError(f"Unterminated string at line {start_line}:{start_col}")
            i += 1
            col += 1
            tokens.append(Token("STR", "".join(buf), start_line, start_col))
            continue

        if ch.isdigit() or (ch == "-" and i + 1 < n and source[i + 1].isdigit()):
            start_col = col
            buf2: list[str] = [ch]
            i += 1
            col += 1
            while i < n and source[i].isdigit():
                buf2.append(source[i])
                i += 1
                col += 1
            tokens.append(Token("INT", "".join(buf2), line, start_col))
            continue

        start_col = col
        buf3: list[str] = []
        while i < n and not source[i].isspace() and source[i] not in '();"':
            buf3.append(source[i])
            i += 1
            col += 1
        if buf3:
            tokens.append(Token("SYM", "".join(buf3), line, start_col))
            continue

        raise LexError(f"Unexpected character {ch!r} at line {line}:{col}")

    return tokens


@dataclass
class IntLiteral:
    value: int


@dataclass
class StrLiteral:
    value: str


@dataclass
class Symbol:
    name: str


@dataclass
class SetqForm:
    name: str
    expr: ASTNode


@dataclass
class DefunForm:
    name: str
    params: list[str]
    body: list[ASTNode]


@dataclass
class IfForm:
    pred: ASTNode
    then_branch: ASTNode
    else_branch: ASTNode | None


@dataclass
class LoopForm:
    condition: ASTNode
    body: list[ASTNode]


@dataclass
class PrintForm:
    expr: ASTNode


@dataclass
class VarSumForm:
    values: list[int]


@dataclass
class VarMulForm:
    values: list[int]


@dataclass
class VarSubForm:
    values: list[int]


@dataclass
class PrintCharForm:
    expr: ASTNode


@dataclass
class ReadForm:
    pass


@dataclass
class BinopForm:
    op: str
    left: ASTNode
    right: ASTNode


@dataclass
class CmpForm:
    op: str
    operands: list[ASTNode]


@dataclass
class CallForm:
    name: str
    args: list[ASTNode]


@dataclass
class BeginForm:
    forms: list[ASTNode]


@dataclass
class MemrefForm:
    addr: ASTNode


@dataclass
class MemsetForm:
    addr: ASTNode
    value: ASTNode


@dataclass
class IretForm:
    pass


@dataclass
class EiForm:
    pass


@dataclass
class DiForm:
    pass


ASTNode = (
    IntLiteral
    | StrLiteral
    | Symbol
    | SetqForm
    | DefunForm
    | IfForm
    | LoopForm
    | PrintForm
    | PrintCharForm
    | ReadForm
    | BinopForm
    | CmpForm
    | CallForm
    | BeginForm
    | MemrefForm
    | MemsetForm
    | IretForm
    | EiForm
    | DiForm
    | VarSumForm
    | VarMulForm
    | VarSubForm
)


BINOPS = {"+", "-", "*", "/", "mod"}
CMPOPS = {"=", "<", ">", "<=", ">=", "!=", "not", "and", "or"}


class ParseError(Exception):
    pass


def _parse_sexpr(tokens: list[Token], pos: int) -> tuple[object, int]:
    if pos >= len(tokens):
        raise ParseError("Unexpected end of input")
    tok = tokens[pos]
    if tok.kind == "LPAREN":
        items: list[object] = []
        pos += 1
        while pos < len(tokens) and tokens[pos].kind != "RPAREN":
            item, pos = _parse_sexpr(tokens, pos)
            items.append(item)
        if pos >= len(tokens):
            raise ParseError("Missing closing paren")
        return items, pos + 1
    if tok.kind == "RPAREN":
        raise ParseError(f"Unexpected ')' at {tok.line}:{tok.col}")
    if tok.kind == "INT":
        return ("INT", int(tok.value)), pos + 1
    if tok.kind == "STR":
        return ("STR", tok.value), pos + 1
    if tok.kind == "SYM":
        return ("SYM", tok.value), pos + 1
    raise ParseError(f"Unknown token {tok.kind}")


def _to_ast(sexpr: object) -> ASTNode:
    if isinstance(sexpr, tuple):
        kind, val = sexpr
        if kind == "INT":
            assert isinstance(val, int)
            return IntLiteral(val)
        if kind == "STR":
            assert isinstance(val, str)
            return StrLiteral(val)
        if kind == "SYM":
            assert isinstance(val, str)
            return Symbol(val)
        raise ParseError(f"Bad atom: {sexpr}")

    if not isinstance(sexpr, list):
        raise ParseError(f"Bad sexpr: {sexpr!r}")

    if not sexpr:
        raise ParseError("Empty list ()")

    head = sexpr[0]
    if not isinstance(head, tuple) or head[0] != "SYM":
        raise ParseError(f"List head must be symbol, got {head!r}")
    op = head[1]
    args = sexpr[1:]

    if op == "setq":
        if len(args) != 2:
            raise ParseError("setq expects 2 args")
        name = _expect_symbol_name(args[0])
        return SetqForm(name=name, expr=_to_ast(args[1]))

    if op == "defun":
        if len(args) < 3:
            raise ParseError("defun expects (defun name (params) body...)")
        name = _expect_symbol_name(args[0])
        params_sexpr = args[1]
        if not isinstance(params_sexpr, list):
            raise ParseError("defun params must be a list")
        params = [_expect_symbol_name(p) for p in params_sexpr]
        body = [_to_ast(b) for b in args[2:]]
        return DefunForm(name=name, params=params, body=body)

    if op == "if":
        if len(args) not in (2, 3):
            raise ParseError("if expects 2 or 3 args")
        else_branch = _to_ast(args[2]) if len(args) == 3 else None
        return IfForm(
            pred=_to_ast(args[0]),
            then_branch=_to_ast(args[1]),
            else_branch=else_branch,
        )

    if op == "loop":
        if not args:
            raise ParseError("loop expects (while cond) and body")
        while_clause = args[0]
        if (
            not isinstance(while_clause, list)
            or len(while_clause) != 2
            or not isinstance(while_clause[0], tuple)
            or while_clause[0] != ("SYM", "while")
        ):
            raise ParseError("loop expects (while cond) as first arg")
        condition = _to_ast(while_clause[1])
        body = [_to_ast(b) for b in args[1:]]
        return LoopForm(condition=condition, body=body)

    if op == "print":
        if len(args) != 1:
            raise ParseError("print expects 1 arg")
        return PrintForm(expr=_to_ast(args[0]))

    if op == "print-char":
        if len(args) != 1:
            raise ParseError("print-char expects 1 arg")
        return PrintCharForm(expr=_to_ast(args[0]))

    if op == "read":
        if args:
            raise ParseError("read expects 0 args")
        return ReadForm()

    if op == "begin":
        return BeginForm(forms=[_to_ast(b) for b in args])

    if op == "memref":
        if len(args) != 1:
            raise ParseError("memref expects 1 arg")
        return MemrefForm(addr=_to_ast(args[0]))

    if op == "memset":
        if len(args) != 2:
            raise ParseError("memset expects 2 args")
        return MemsetForm(addr=_to_ast(args[0]), value=_to_ast(args[1]))

    if op == "iret":
        return IretForm()

    if op == "ei":
        return EiForm()

    if op == "di":
        return DiForm()

    if op == "vsum":
        if len(args) < 1:
            raise ParseError("vsum expects at least one integer argument")
        values = []
        for a in args:
            if not (isinstance(a, tuple) and a[0] == "INT"):
                raise ParseError("vsum arguments must be integer literals")
            values.append(int(a[1]))
        return VarSumForm(values=values)

    if op == "vmul":
        if len(args) < 1:
            raise ParseError("vmul expects at least one integer argument")
        values = []
        for a in args:
            if not (isinstance(a, tuple) and a[0] == "INT"):
                raise ParseError("vmul arguments must be integer literals")
            values.append(int(a[1]))
        return VarMulForm(values=values)

    if op == "vsub":
        if len(args) < 2:
            raise ParseError("vsub expects at least two integer arguments")
        values = []
        for a in args:
            if not (isinstance(a, tuple) and a[0] == "INT"):
                raise ParseError("vsub arguments must be integer literals")
            values.append(int(a[1]))
        return VarSubForm(values=values)

    if op in BINOPS:
        if len(args) < 2:
            raise ParseError(f"{op} expects at least 2 args")
        result = _to_ast(args[0])
        for a in args[1:]:
            result = BinopForm(op=op, left=result, right=_to_ast(a))
        return result

    if op in CMPOPS:
        return CmpForm(op=op, operands=[_to_ast(a) for a in args])

    return CallForm(name=op, args=[_to_ast(a) for a in args])


def _expect_symbol_name(sexpr: object) -> str:
    if isinstance(sexpr, tuple) and sexpr[0] == "SYM":
        assert isinstance(sexpr[1], str)
        return sexpr[1]
    raise ParseError(f"Expected symbol, got {sexpr!r}")


def parse(source: str) -> list[ASTNode]:
    tokens = tokenize(source)
    pos = 0
    forms: list[ASTNode] = []
    while pos < len(tokens):
        sexpr, pos = _parse_sexpr(tokens, pos)
        forms.append(_to_ast(sexpr))
    return forms
