from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
import yaml

from lisp_cisc.compiler import (
    Compiler,
    write_data_binary,
    write_data_hex_dump,
    write_instr_binary,
    write_instr_hex_dump,
)
from lisp_cisc.lang import parse
from lisp_cisc.machine import _coerce_char, load_schedule, simulate, unpack_words_be

GOLDEN_DIR = Path(__file__).parent / "golden"
REPO_ROOT = Path(__file__).parent.parent
LOG_HEAD_LINES = 150
LOG_TAIL_LINES = 150
LOG_SKIP_MARKER = "SKIPPING LOG..."

_LOG_INTR_HEAD = 30
_LOG_INTR_WINDOW = 60
_LOG_INTR_TAIL = 50


class LiteralString(str):
    pass


class QuotedString(str):
    pass


class GoldenDumper(yaml.SafeDumper):
    pass


def _literal_representer(dumper: yaml.Dumper, data: LiteralString) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


def _quoted_representer(dumper: yaml.Dumper, data: QuotedString) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')


GoldenDumper.add_representer(LiteralString, _literal_representer)  # type: ignore[arg-type]
GoldenDumper.add_representer(QuotedString, _quoted_representer)  # type: ignore[arg-type]


def _golden_files() -> list[Path]:
    return sorted(GOLDEN_DIR.glob("*.yaml"))


def _head(text: str, n_lines: int) -> str:
    return "\n".join(text.splitlines()[:n_lines])


def _representative_log(log: list[str]) -> str:
    if len(log) <= LOG_HEAD_LINES + LOG_TAIL_LINES:
        return "\n".join(log)

    int_indices = [i for i, line in enumerate(log) if "| INT " in line or "| INT_" in line]
    if int_indices and int_indices[0] >= _LOG_INTR_HEAD:
        win_start = max(_LOG_INTR_HEAD, int_indices[0] - 5)
        win_end = min(len(log) - _LOG_INTR_TAIL, win_start + _LOG_INTR_WINDOW)
        tail_start = len(log) - _LOG_INTR_TAIL
        parts: list[str] = [*log[:_LOG_INTR_HEAD], LOG_SKIP_MARKER, *log[win_start:win_end]]
        if win_end < tail_start:
            parts += [LOG_SKIP_MARKER, *log[tail_start:]]
        else:
            parts += log[win_end:]
        return "\n".join(parts)

    return "\n".join([*log[:LOG_HEAD_LINES], LOG_SKIP_MARKER, *log[-LOG_TAIL_LINES:]])


def _listing(instr_hex: str, data_hex: str, instr_lines: int, data_lines: int) -> str:
    instr_part = _head(instr_hex, instr_lines)
    data_part = _head(data_hex, data_lines)
    if data_part:
        return f"[.text]\n{instr_part}\n\n[.data]\n{data_part}"
    return f"[.text]\n{instr_part}\n\n[.data]"


def _dump_golden(path: Path, spec: dict[str, object], actual: dict[str, object]) -> None:
    new_spec: dict[str, object] = {}
    for key in ("name", "description"):
        if key in spec:
            new_spec[key] = spec[key]
    if "source_file" in spec:
        new_spec["source_file"] = QuotedString(str(spec["source_file"]))
    elif "source" in spec:
        new_spec["source"] = LiteralString(str(spec["source"]).rstrip("\n"))
    if "input_file" in spec:
        new_spec["input_file"] = QuotedString(str(spec["input_file"]))
    elif spec.get("input"):
        new_spec["input"] = spec["input"]
    if "max_ticks" in spec:
        new_spec["max_ticks"] = spec["max_ticks"]
    new_spec["trace_mode"] = QuotedString(str(spec.get("trace_mode", "brief")))

    for key in (
        "instr_hex_head_lines",
        "data_hex_head_lines",
    ):
        if key in spec:
            new_spec[key] = spec[key]

    new_spec["out_lst"] = LiteralString(str(actual["out_lst"]).rstrip("\n") + "\n")
    new_spec["out_output"] = LiteralString(str(actual["out_output"]))
    new_spec["out_log"] = LiteralString(str(actual["out_log"]).rstrip("\n") + "\n")
    new_spec["stats"] = actual["stats"]

    path.write_text(
        yaml.dump(
            new_spec,
            Dumper=GoldenDumper,
            sort_keys=False,
            allow_unicode=True,
            width=120,
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize("golden", _golden_files(), ids=lambda p: p.stem)
def test_golden(golden: Path, tmp_path: Path, update_goldens: bool) -> None:
    spec = yaml.safe_load(golden.read_text(encoding="utf-8"))

    source: str
    if "source_file" in spec:
        source = (Path(__file__).parent.parent / spec["source_file"]).read_text("utf-8")
    else:
        source = spec["source"]

    if "input_file" in spec:
        schedule = load_schedule(REPO_ROOT / spec["input_file"])
    else:
        schedule = [(int(t), _coerce_char(c)) for t, c in spec.get("input", [])]
    max_ticks = int(spec.get("max_ticks", 200_000))

    forms = parse(source)

    compiler = Compiler()
    program = compiler.compile_program(forms)

    instr_bin_path = tmp_path / "instr.bin"
    write_instr_binary(program.instrs, instr_bin_path)
    instr_bin = instr_bin_path.read_bytes()
    assert len(instr_bin) == len(program.instrs) * 4
    assert unpack_words_be(instr_bin) == program.instrs

    data_bin_path = tmp_path / "data.bin"
    write_data_binary(program.data, data_bin_path)
    data_bin = data_bin_path.read_bytes()
    assert len(data_bin) == len(program.data) * 4
    assert unpack_words_be(data_bin) == program.data

    hex_path = tmp_path / "instr.hex"
    write_instr_hex_dump(program.instrs, hex_path)
    instr_hex = hex_path.read_text(encoding="utf-8")

    data_hex_path = tmp_path / "data.hex"
    write_data_hex_dump(program.data, data_hex_path)
    data_hex = data_hex_path.read_text(encoding="utf-8")

    result = simulate(program.instrs, program.data, schedule, max_ticks=max_ticks)

    instr_lines = int(spec.get("instr_hex_head_lines", 25))
    data_lines = int(spec.get("data_hex_head_lines", 25))

    actual: dict[str, object] = {
        "out_lst": _listing(instr_hex, data_hex, instr_lines, data_lines),
        "out_output": result.output,
        "out_log": _representative_log(result.log),
        "stats": {
            "instr_count": result.instr_count,
            "tick_count": result.tick_count,
        },
    }

    if update_goldens:
        _dump_golden(golden, spec, actual)
        return

    expected_output = spec.get("out_output", spec.get("output"))
    assert (
        result.output == expected_output
    ), f"Output mismatch:\nExpected: {expected_output!r}\nActual:   {result.output!r}"

    if "stats" in spec:
        actual_stats = cast(dict[str, object], actual["stats"])
        for k, v in spec["stats"].items():
            assert actual_stats[k] == v, f"stat {k}: {actual_stats[k]} != {v}"

    if "out_lst" in spec:
        assert cast(str, actual["out_lst"]).strip() == spec["out_lst"].strip()
    else:
        if "instr_hex_head" in spec:
            assert _head(instr_hex, instr_lines).strip() == spec["instr_hex_head"].strip()

        if "data_hex_head" in spec:
            assert _head(data_hex, data_lines).strip() == spec["data_hex_head"].strip()

    if "out_log" in spec:
        assert cast(str, actual["out_log"]).strip() == spec["out_log"].strip()
    else:
        if "log_head" in spec:
            assert "\n".join(result.log[:LOG_HEAD_LINES]).strip() == spec["log_head"].strip()

        if "log_tail" in spec:
            assert "\n".join(result.log[-LOG_TAIL_LINES:]).strip() == spec["log_tail"].strip()
