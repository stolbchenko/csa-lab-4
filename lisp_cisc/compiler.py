from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from pathlib import Path

from lisp_cisc.isa import (
    DATA_LITERAL_BASE,
    DATA_STACK_TOP,
    DATA_VARIABLE_BASE,
    INSTR_INTERRUPT_HANDLER_BASE,
    INSTR_INTERRUPT_VECTOR_0,
    INSTR_PROGRAM_START,
    Opcode,
    Reg,
    decode,
    encode_word0,
    instruction_word_count_from_word0,
    mnemonic,
    to_unsigned32,
)
from lisp_cisc.lang import (
    ASTNode,
    BeginForm,
    BinopForm,
    CallForm,
    CmpForm,
    DefunForm,
    DiForm,
    EiForm,
    IfForm,
    IntLiteral,
    IretForm,
    LoopForm,
    MemrefForm,
    MemsetForm,
    PrintCharForm,
    PrintForm,
    ReadForm,
    SetqForm,
    StrLiteral,
    Symbol,
    VarMulForm,
    VarSubForm,
    VarSumForm,
    parse,
)

TEMP_BASE = 0x0500

INT_TEMP_BASE = 0x0580

PARAM_BASE = 0x0600

INTERRUPT_HANDLER_NAME = "_interrupt_handler"

STDLIB_PRINT_PSTR_PATH = Path(__file__).parent.parent / "programs" / "stdlib_print_pstr.lisp"


def _has_str_literal(forms: list[ASTNode]) -> bool:
    def check(node: object) -> bool:
        if isinstance(node, StrLiteral):
            return True
        if isinstance(node, list):
            return any(check(x) for x in node)
        try:
            return any(check(getattr(node, f.name)) for f in dataclass_fields(node))  # type: ignore[arg-type]
        except TypeError:
            return False

    return any(check(f) for f in forms)


def _load_stdlib_print_pstr() -> list[ASTNode]:
    return parse(STDLIB_PRINT_PSTR_PATH.read_text(encoding="utf-8"))


class CompileError(Exception):
    pass


@dataclass
class CompiledProgram:
    instrs: list[int] = field(default_factory=list)
    data: list[int] = field(default_factory=list)
    symbol_table: dict[str, int] = field(default_factory=dict)
    function_table: dict[str, int] = field(default_factory=dict)
    string_literals: dict[str, int] = field(default_factory=dict)
    has_interrupt_handler: bool = False


class Compiler:
    def __init__(self) -> None:
        self.instrs: list[int] = [0] * INSTR_PROGRAM_START
        self.data: list[int] = [0] * DATA_STACK_TOP

        self.next_var_addr = DATA_VARIABLE_BASE
        self.next_literal_addr = DATA_LITERAL_BASE
        self.next_param_addr = PARAM_BASE
        self.temp_idx = 0
        self.max_temp = 0
        self.current_temp_base = TEMP_BASE

        self.symbol_table: dict[str, int] = {}
        self.string_literals: dict[str, int] = {}
        self.function_table: dict[str, int] = {}
        self.function_params: dict[str, list[int]] = {}

        self.label_table: dict[str, int] = {}
        self.patch_list: list[tuple[int, str]] = []
        self.label_counter = 0

        self.has_interrupt_handler = False
        self.in_interrupt_handler = False
        self.print_int_label: str | None = None
        self.print_int_used = False

    def emit(self, word: int) -> int:
        addr = len(self.instrs)
        self.instrs.append(to_unsigned32(word))
        return addr

    def emit_w0(self, opcode: int, dst: int = 0, src: int = 0, mode: int = 0) -> int:
        return self.emit(encode_word0(opcode, dst, src, mode))

    def emit_2word(self, opcode: int, operand: int, dst: int = 0, src: int = 0, mode: int = 0) -> int:
        addr = self.emit_w0(opcode, dst, src, mode)
        self.emit(operand)
        return addr

    def emit_jump_to_label(self, opcode: int, label: str) -> int:
        addr = self.emit_w0(opcode)
        operand_addr = self.emit(0)
        self.patch_list.append((operand_addr, label))
        return addr

    def emit_call_to_label(self, label: str) -> int:
        addr = self.emit_w0(Opcode.CALL)
        operand_addr = self.emit(0)
        self.patch_list.append((operand_addr, label))
        return addr

    def new_label(self, hint: str = "L") -> str:
        self.label_counter += 1
        return f"__{hint}_{self.label_counter}"

    def place_label(self, label: str) -> None:
        if label in self.label_table:
            raise CompileError(f"Label {label!r} already placed")
        self.label_table[label] = len(self.instrs)

    def get_var_addr(self, name: str) -> int:
        if name not in self.symbol_table:
            self.symbol_table[name] = self.next_var_addr
            self.next_var_addr += 1
        return self.symbol_table[name]

    def store_string(self, s: str) -> int:
        if s in self.string_literals:
            return self.string_literals[s]
        addr = self.next_literal_addr
        self.string_literals[s] = addr
        self.data_write(addr, len(s))
        for i, ch in enumerate(s):
            self.data_write(addr + 1 + i, ord(ch))
        self.next_literal_addr += len(s) + 1
        return addr

    def data_write(self, addr: int, value: int) -> None:
        while len(self.data) <= addr:
            self.data.append(0)
        self.data[addr] = to_unsigned32(value)

    def alloc_temp(self) -> int:
        addr = self.current_temp_base + self.temp_idx
        self.temp_idx += 1
        self.max_temp = max(self.max_temp, self.temp_idx)
        return addr

    def free_temp(self) -> None:
        self.temp_idx -= 1
        assert self.temp_idx >= 0

    def active_temp_slots(self) -> list[int]:
        return [self.current_temp_base + i for i in range(self.temp_idx)]

    def alloc_param(self, fn_name: str, n: int) -> list[int]:
        addrs = list(range(self.next_param_addr, self.next_param_addr + n))
        self.next_param_addr += n
        self.function_params[fn_name] = addrs
        return addrs

    def compile_program(self, forms: list[ASTNode]) -> CompiledProgram:
        if _has_str_literal(forms):
            stdlib_forms = _load_stdlib_print_pstr()
            forms = stdlib_forms + list(forms)

        self.instrs[0] = encode_word0(Opcode.JMP)
        self.instrs[1] = INSTR_PROGRAM_START

        defuns: list[DefunForm] = []
        main_forms: list[ASTNode] = []
        for f in forms:
            if isinstance(f, DefunForm):
                defuns.append(f)
            else:
                main_forms.append(f)

        normal_defuns: list[DefunForm] = []
        interrupt_handler: DefunForm | None = None
        for d in defuns:
            self.alloc_param(d.name, len(d.params))
            for p, slot in zip(d.params, self.function_params[d.name], strict=False):
                self.symbol_table[f"_param_{d.name}_{p}"] = slot
            if d.name == INTERRUPT_HANDLER_NAME:
                interrupt_handler = d
            else:
                normal_defuns.append(d)

        self.label_table["__main_start"] = INSTR_PROGRAM_START
        while len(self.instrs) < INSTR_PROGRAM_START:
            self.instrs.append(0)

        self.emit_2word(Opcode.LOAD_IMM, DATA_STACK_TOP, dst=Reg.SP)

        for f in main_forms:
            self.compile_node(f)

        self.emit_w0(Opcode.HALT)

        for d in normal_defuns:
            self.compile_function(d)

        if self.print_int_used:
            self.emit_print_int_routine()

        if interrupt_handler is not None:
            while len(self.instrs) < INSTR_INTERRUPT_HANDLER_BASE:
                self.instrs.append(0)
            self.has_interrupt_handler = True
            self.instrs[INSTR_INTERRUPT_VECTOR_0] = INSTR_INTERRUPT_HANDLER_BASE
            self.compile_function(interrupt_handler, is_interrupt=True)

        for operand_addr, label in self.patch_list:
            if label not in self.label_table:
                raise CompileError(f"Undefined label: {label}")
            self.instrs[operand_addr] = to_unsigned32(self.label_table[label])

        return CompiledProgram(
            instrs=self.instrs,
            data=self.data,
            symbol_table=self.symbol_table,
            function_table=self.function_table,
            string_literals=self.string_literals,
            has_interrupt_handler=self.has_interrupt_handler,
        )

    def compile_function(self, defun: DefunForm, is_interrupt: bool = False) -> None:
        addr = len(self.instrs)
        self.function_table[defun.name] = addr
        self.label_table[defun.name] = addr

        prev_in_int = self.in_interrupt_handler
        self.in_interrupt_handler = is_interrupt

        prev_temp_base = self.current_temp_base
        prev_temp_idx = self.temp_idx
        if is_interrupt:
            self.current_temp_base = INT_TEMP_BASE
            self.temp_idx = 0

        saved_globals: dict[str, int | None] = {}
        param_slots = self.function_params.get(defun.name, [])
        for p, slot in zip(defun.params, param_slots, strict=False):
            saved_globals[p] = self.symbol_table.get(p)
            self.symbol_table[p] = slot

        for f in defun.body:
            self.compile_node(f)

        if is_interrupt:
            self.emit_w0(Opcode.IRET)
        else:
            self.emit_w0(Opcode.RET)

        for p in defun.params:
            prev = saved_globals.get(p)
            if prev is None:
                self.symbol_table.pop(p, None)
            else:
                self.symbol_table[p] = prev

        self.current_temp_base = prev_temp_base
        self.temp_idx = prev_temp_idx
        self.in_interrupt_handler = prev_in_int

    def compile_node(self, node: ASTNode) -> None:
        if isinstance(node, IntLiteral):
            self.emit_2word(Opcode.LOAD_IMM, to_unsigned32(node.value), dst=Reg.R0)
            return

        if isinstance(node, StrLiteral):
            addr = self.store_string(node.value)
            self.emit_2word(Opcode.LOAD_IMM, addr, dst=Reg.R0)
            return

        if isinstance(node, Symbol):
            addr = self.get_var_addr(node.name)
            self.emit_2word(Opcode.LOAD_MEM, addr, dst=Reg.R0)
            return

        if isinstance(node, SetqForm):
            self.compile_node(node.expr)
            addr = self.get_var_addr(node.name)
            self.emit_2word(Opcode.STORE_MEM, addr, src=Reg.R0)
            return

        if isinstance(node, BinopForm):
            self.compile_binop(node)
            return

        if isinstance(node, CmpForm):
            self.compile_cmp(node)
            return

        if isinstance(node, IfForm):
            self.compile_if(node)
            return

        if isinstance(node, LoopForm):
            self.compile_loop(node)
            return

        if isinstance(node, BeginForm):
            for f in node.forms:
                self.compile_node(f)
            return

        if isinstance(node, PrintForm):
            self.compile_print(node)
            return

        if isinstance(node, PrintCharForm):
            self.compile_node(node.expr)
            self.emit_2word(Opcode.OUT_PORT, 0x01, src=Reg.R0)
            return

        if isinstance(node, ReadForm):
            self.emit_2word(Opcode.IN_PORT, 0x00, dst=Reg.R0)
            return

        if isinstance(node, CallForm):
            self.compile_call(node)
            return

        if isinstance(node, MemrefForm):
            self.compile_node(node.addr)
            self.emit_w0(Opcode.MOVE_REG, dst=Reg.R1, src=Reg.R0)
            self.emit_w0(Opcode.LOAD_IND, dst=Reg.R0, src=Reg.R1)
            return

        if isinstance(node, MemsetForm):
            self.compile_node(node.value)
            value_temp = self.alloc_temp()
            self.emit_2word(Opcode.STORE_MEM, value_temp, src=Reg.R0)
            self.compile_node(node.addr)
            self.emit_w0(Opcode.MOVE_REG, dst=Reg.R1, src=Reg.R0)
            self.emit_2word(Opcode.LOAD_MEM, value_temp, dst=Reg.R0)
            self.emit_w0(Opcode.STORE_IND, dst=Reg.R1, src=Reg.R0)
            self.free_temp()
            return

        if isinstance(node, IretForm):
            self.emit_w0(Opcode.IRET)
            return

        if isinstance(node, EiForm):
            self.emit_w0(Opcode.EI)
            return

        if isinstance(node, DiForm):
            self.emit_w0(Opcode.DI)
            return

        if isinstance(node, VarSumForm):
            self.compile_varsum(node)
            return

        if isinstance(node, VarMulForm):
            self.compile_varmul(node)
            return

        if isinstance(node, VarSubForm):
            self.compile_varsub(node)
            return

        raise CompileError(f"Unsupported node: {type(node).__name__}")

    def compile_varsum(self, node: VarSumForm) -> None:
        n = len(node.values)
        if n == 0:
            raise CompileError("vsum requires at least one argument")
        if n > 255:
            raise CompileError("vsum supports at most 255 arguments")
        word0 = encode_word0(Opcode.SUM_IMM, dst=Reg.R0, mode=n)
        self.instrs.append(word0)
        for value in node.values:
            self.instrs.append(to_unsigned32(value))

    def compile_varmul(self, node: VarMulForm) -> None:
        n = len(node.values)
        if n == 0:
            raise CompileError("vmul requires at least one argument")
        if n > 255:
            raise CompileError("vmul supports at most 255 arguments")
        word0 = encode_word0(Opcode.VMUL_IMM, dst=Reg.R0, mode=n)
        self.instrs.append(word0)
        for value in node.values:
            self.instrs.append(to_unsigned32(value))

    def compile_varsub(self, node: VarSubForm) -> None:
        n = len(node.values)
        if n < 2:
            raise CompileError("vsub requires at least two arguments")
        if n > 255:
            raise CompileError("vsub supports at most 255 arguments")
        word0 = encode_word0(Opcode.VSUB_IMM, dst=Reg.R0, mode=n)
        self.instrs.append(word0)
        for value in node.values:
            self.instrs.append(to_unsigned32(value))

    def compile_binop(self, node: BinopForm) -> None:
        self.compile_node(node.right)
        temp = self.alloc_temp()
        self.emit_2word(Opcode.STORE_MEM, temp, src=Reg.R0)
        self.compile_node(node.left)
        if node.op == "+":
            opcode = Opcode.ADD_MEM
        elif node.op == "-":
            opcode = Opcode.SUB_MEM
        elif node.op == "*":
            opcode = Opcode.MUL_MEM
        elif node.op == "/":
            opcode = Opcode.DIV_MEM
        elif node.op == "mod":
            opcode = Opcode.MOD_MEM
        else:
            raise CompileError(f"Unknown binop: {node.op}")
        self.emit_2word(opcode, temp, dst=Reg.R0)
        self.free_temp()

    def compile_cmp(self, node: CmpForm) -> None:
        op = node.op
        if op in ("=", "<", ">", "<=", ">=", "!="):
            if len(node.operands) != 2:
                raise CompileError(f"{op} expects 2 operands")
            self.compile_node(node.operands[1])
            temp = self.alloc_temp()
            self.emit_2word(Opcode.STORE_MEM, temp, src=Reg.R0)
            self.compile_node(node.operands[0])
            self.emit_2word(Opcode.CMP_MEM, temp, dst=Reg.R0)
            self.free_temp()
            self.materialize_flags(op)
            return

        if op == "not":
            if len(node.operands) != 1:
                raise CompileError("not expects 1 operand")
            self.compile_node(node.operands[0])
            self.emit_2word(Opcode.CMP_IMM, 0, dst=Reg.R0)
            self.materialize_flags("=")
            return

        if op == "and":
            end = self.new_label("and_end")
            for i, opd in enumerate(node.operands):
                self.compile_node(opd)
                if i < len(node.operands) - 1:
                    self.emit_2word(Opcode.CMP_IMM, 0, dst=Reg.R0)
                    self.emit_jump_to_label(Opcode.JZ, end)
            self.place_label(end)
            return

        if op == "or":
            end = self.new_label("or_end")
            for i, opd in enumerate(node.operands):
                self.compile_node(opd)
                if i < len(node.operands) - 1:
                    self.emit_2word(Opcode.CMP_IMM, 0, dst=Reg.R0)
                    self.emit_jump_to_label(Opcode.JNZ, end)
            self.place_label(end)
            return

        raise CompileError(f"Unknown cmp op: {op}")

    def materialize_flags(self, op: str) -> None:
        true_label = self.new_label("cmp_true")
        end_label = self.new_label("cmp_end")

        if op == "=":
            self.emit_jump_to_label(Opcode.JZ, true_label)
        elif op == "!=":
            self.emit_jump_to_label(Opcode.JNZ, true_label)
        elif op == "<":
            self.emit_jump_to_label(Opcode.JN, true_label)
        elif op == ">":
            false_label = self.new_label("cmp_false")
            self.emit_jump_to_label(Opcode.JZ, false_label)
            self.emit_jump_to_label(Opcode.JN, false_label)
            self.emit_jump_to_label(Opcode.JMP, true_label)
            self.place_label(false_label)
        elif op == "<=":
            self.emit_jump_to_label(Opcode.JZ, true_label)
            self.emit_jump_to_label(Opcode.JN, true_label)
        elif op == ">=":
            self.emit_jump_to_label(Opcode.JP, true_label)
        else:
            raise CompileError(f"Cannot materialize op {op}")

        self.emit_2word(Opcode.LOAD_IMM, 0, dst=Reg.R0)
        self.emit_jump_to_label(Opcode.JMP, end_label)
        self.place_label(true_label)
        self.emit_2word(Opcode.LOAD_IMM, 1, dst=Reg.R0)
        self.place_label(end_label)

    def compile_if(self, node: IfForm) -> None:
        else_label = self.new_label("if_else")
        end_label = self.new_label("if_end")

        self.compile_node(node.pred)
        self.emit_2word(Opcode.CMP_IMM, 0, dst=Reg.R0)
        self.emit_jump_to_label(Opcode.JZ, else_label)

        self.compile_node(node.then_branch)
        self.emit_jump_to_label(Opcode.JMP, end_label)

        self.place_label(else_label)
        if node.else_branch is not None:
            self.compile_node(node.else_branch)
        else:
            self.emit_2word(Opcode.LOAD_IMM, 0, dst=Reg.R0)
        self.place_label(end_label)

    def compile_loop(self, node: LoopForm) -> None:
        loop_start = self.new_label("loop_start")
        loop_end = self.new_label("loop_end")
        self.emit_2word(Opcode.LOAD_IMM, 0, dst=Reg.R0)

        self.place_label(loop_start)
        self.compile_node(node.condition)
        self.emit_2word(Opcode.CMP_IMM, 0, dst=Reg.R0)
        self.emit_jump_to_label(Opcode.JZ, loop_end)

        for f in node.body:
            self.compile_node(f)

        self.emit_jump_to_label(Opcode.JMP, loop_start)
        self.place_label(loop_end)

    def compile_call(self, node: CallForm) -> None:
        if node.name not in self.function_params:
            raise CompileError(f"Undefined function: {node.name}")
        param_slots = self.function_params[node.name]
        if len(node.args) != len(param_slots):
            raise CompileError(f"{node.name} expects {len(param_slots)} args, got {len(node.args)}")

        active_temp_slots = self.active_temp_slots()
        for slot in active_temp_slots:
            self.emit_2word(Opcode.LOAD_MEM, slot, dst=Reg.R0)
            self.emit_w0(Opcode.PUSH, src=Reg.R0)

        for slot in param_slots:
            self.emit_2word(Opcode.LOAD_MEM, slot, dst=Reg.R0)
            self.emit_w0(Opcode.PUSH, src=Reg.R0)

        for arg, slot in zip(node.args, param_slots, strict=False):
            self.compile_node(arg)
            self.emit_2word(Opcode.STORE_MEM, slot, src=Reg.R0)

        self.emit_call_to_label(node.name)

        result_temp = self.alloc_temp()
        self.emit_2word(Opcode.STORE_MEM, result_temp, src=Reg.R0)
        for slot in reversed(param_slots):
            self.emit_w0(Opcode.POP, dst=Reg.R0)
            self.emit_2word(Opcode.STORE_MEM, slot, src=Reg.R0)
        for slot in reversed(active_temp_slots):
            self.emit_w0(Opcode.POP, dst=Reg.R0)
            self.emit_2word(Opcode.STORE_MEM, slot, src=Reg.R0)
        self.emit_2word(Opcode.LOAD_MEM, result_temp, dst=Reg.R0)
        self.free_temp()

    def compile_print(self, node: PrintForm) -> None:
        self._compile_print_expr(node.expr)

    def _compile_print_expr(self, node: ASTNode) -> None:
        if isinstance(node, StrLiteral):
            self.compile_print_string_literal(node.value)
            return
        if isinstance(node, IfForm):
            else_label = self.new_label("pif_else")
            end_label = self.new_label("pif_end")
            self.compile_node(node.pred)
            self.emit_2word(Opcode.CMP_IMM, 0, dst=Reg.R0)
            self.emit_jump_to_label(Opcode.JZ, else_label)
            self._compile_print_expr(node.then_branch)
            self.emit_jump_to_label(Opcode.JMP, end_label)
            self.place_label(else_label)
            if node.else_branch is not None:
                self._compile_print_expr(node.else_branch)
            self.place_label(end_label)
            return
        self.print_int_used = True
        if self.print_int_label is None:
            self.print_int_label = "__print_int"
        self.compile_node(node)
        self.emit_call_to_label(self.print_int_label)

    def compile_print_string_literal(self, s: str) -> None:
        addr = self.store_string(s)
        self.compile_call(CallForm(name="__print_pstr", args=[IntLiteral(value=addr)]))

    def emit_print_int_routine(self) -> None:
        assert self.print_int_label is not None
        self.label_table[self.print_int_label] = len(self.instrs)

        n_slot = TEMP_BASE + 200
        sign_slot = TEMP_BASE + 201
        ten_slot = TEMP_BASE + 203
        count_slot = TEMP_BASE + 204
        zero_label = self.new_label("pi_zero")
        nonzero_label = self.new_label("pi_nonzero")
        positive_label = self.new_label("pi_positive")
        loop_label = self.new_label("pi_loop")
        out_loop_label = self.new_label("pi_out_loop")
        out_done_label = self.new_label("pi_out_done")

        self.emit_2word(Opcode.STORE_MEM, n_slot, src=Reg.R0)
        self.emit_2word(Opcode.LOAD_IMM, 10, dst=Reg.R0)
        self.emit_2word(Opcode.STORE_MEM, ten_slot, src=Reg.R0)
        self.emit_2word(Opcode.LOAD_IMM, 0, dst=Reg.R0)
        self.emit_2word(Opcode.STORE_MEM, count_slot, src=Reg.R0)
        self.emit_2word(Opcode.LOAD_IMM, 0, dst=Reg.R0)
        self.emit_2word(Opcode.STORE_MEM, sign_slot, src=Reg.R0)

        self.emit_2word(Opcode.LOAD_MEM, n_slot, dst=Reg.R0)
        self.emit_2word(Opcode.CMP_IMM, 0, dst=Reg.R0)
        self.emit_jump_to_label(Opcode.JNZ, nonzero_label)
        self.emit_2word(Opcode.LOAD_IMM, ord("0"), dst=Reg.R0)
        self.emit_2word(Opcode.OUT_PORT, 0x01, src=Reg.R0)
        self.emit_jump_to_label(Opcode.JMP, out_done_label)

        self.place_label(nonzero_label)
        self.emit_2word(Opcode.LOAD_MEM, n_slot, dst=Reg.R0)
        self.emit_2word(Opcode.CMP_IMM, 0, dst=Reg.R0)
        self.emit_jump_to_label(Opcode.JP, positive_label)
        self.emit_2word(Opcode.LOAD_IMM, 1, dst=Reg.R0)
        self.emit_2word(Opcode.STORE_MEM, sign_slot, src=Reg.R0)
        self.emit_2word(Opcode.LOAD_IMM, 0, dst=Reg.R0)
        self.emit_2word(Opcode.SUB_MEM, n_slot, dst=Reg.R0)
        self.emit_2word(Opcode.STORE_MEM, n_slot, src=Reg.R0)

        self.place_label(positive_label)
        self.place_label(loop_label)
        self.emit_2word(Opcode.LOAD_MEM, n_slot, dst=Reg.R0)
        self.emit_2word(Opcode.CMP_IMM, 0, dst=Reg.R0)
        self.emit_jump_to_label(Opcode.JZ, zero_label)

        self.emit_2word(Opcode.LOAD_MEM, n_slot, dst=Reg.R0)
        self.emit_2word(Opcode.MOD_MEM, ten_slot, dst=Reg.R0)
        self.emit_2word(Opcode.ADD_IMM, ord("0"), dst=Reg.R0)
        self.emit_w0(Opcode.PUSH, src=Reg.R0)
        self.emit_2word(Opcode.LOAD_MEM, count_slot, dst=Reg.R0)
        self.emit_2word(Opcode.ADD_IMM, 1, dst=Reg.R0)
        self.emit_2word(Opcode.STORE_MEM, count_slot, src=Reg.R0)
        self.emit_2word(Opcode.LOAD_MEM, n_slot, dst=Reg.R0)
        self.emit_2word(Opcode.DIV_MEM, ten_slot, dst=Reg.R0)
        self.emit_2word(Opcode.STORE_MEM, n_slot, src=Reg.R0)
        self.emit_jump_to_label(Opcode.JMP, loop_label)

        self.place_label(zero_label)
        sign_skip_label = self.new_label("pi_sign_skip")
        self.emit_2word(Opcode.LOAD_MEM, sign_slot, dst=Reg.R0)
        self.emit_2word(Opcode.CMP_IMM, 0, dst=Reg.R0)
        self.emit_jump_to_label(Opcode.JZ, sign_skip_label)
        self.emit_2word(Opcode.LOAD_IMM, ord("-"), dst=Reg.R0)
        self.emit_2word(Opcode.OUT_PORT, 0x01, src=Reg.R0)
        self.place_label(sign_skip_label)

        self.place_label(out_loop_label)
        self.emit_2word(Opcode.LOAD_MEM, count_slot, dst=Reg.R0)
        self.emit_2word(Opcode.CMP_IMM, 0, dst=Reg.R0)
        self.emit_jump_to_label(Opcode.JZ, out_done_label)
        self.emit_w0(Opcode.POP, dst=Reg.R0)
        self.emit_2word(Opcode.OUT_PORT, 0x01, src=Reg.R0)
        self.emit_2word(Opcode.LOAD_MEM, count_slot, dst=Reg.R0)
        self.emit_2word(Opcode.SUB_IMM, 1, dst=Reg.R0)
        self.emit_2word(Opcode.STORE_MEM, count_slot, src=Reg.R0)
        self.emit_jump_to_label(Opcode.JMP, out_loop_label)

        self.place_label(out_done_label)
        self.emit_w0(Opcode.RET)


def pack_words_be(words: list[int]) -> bytes:
    return b"".join(struct.pack(">I", to_unsigned32(w)) for w in words)


def write_instr_binary(instrs: list[int], path: Path) -> None:
    path.write_bytes(pack_words_be(instrs))


def write_data_binary(data: list[int], path: Path) -> None:
    path.write_bytes(pack_words_be(data))


def write_instr_hex_dump(instrs: list[int], path: Path) -> None:
    lines: list[str] = []
    addr = 0
    n = len(instrs)
    while addr < n:
        word0 = instrs[addr]
        n_words = instruction_word_count_from_word0(word0)
        if addr < INSTR_PROGRAM_START and word0 == 0:
            addr += 1
            continue
        instr = decode(instrs, addr)
        words_hex = " ".join(f"{w:08X}" for w in instr.raw_words)
        mn = mnemonic(instr)
        lines.append(f"0x{addr:04X} - {words_hex:<17} - {mn}")
        addr += n_words
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_data_hex_dump(data: list[int], path: Path) -> None:
    lines: list[str] = []
    for addr, word in enumerate(data):
        if word == 0:
            continue
        ascii_hint = ""
        if 0x20 <= word <= 0x7E:
            ascii_hint = f"  ; '{chr(word)}'"
        lines.append(f"0x{addr:04X} - {word:08X}{ascii_hint}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def translate_source(source: str, out_dir: Path, name: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    forms = parse(source)
    (out_dir / f"{name}.ast").write_text(
        "\n".join(repr(f) for f in forms) + "\n",
        encoding="utf-8",
    )

    compiler = Compiler()
    program = compiler.compile_program(forms)

    write_instr_binary(program.instrs, out_dir / f"{name}.instr.bin")
    write_data_binary(program.data, out_dir / f"{name}.data.bin")
    write_instr_hex_dump(program.instrs, out_dir / f"{name}.instr.hex")
    write_data_hex_dump(program.data, out_dir / f"{name}.data.hex")


def main() -> None:
    parser = argparse.ArgumentParser(description="Lisp translator (CISC/Harvard)")
    parser.add_argument("source", type=Path, help="Source .lisp file")
    parser.add_argument("--out-dir", type=Path, default=Path("out"), help="Output directory")
    parser.add_argument("--name", default=None, help="Output base name (defaults to source stem)")
    args = parser.parse_args()

    source = args.source.read_text(encoding="utf-8")
    name = args.name if args.name else args.source.stem
    translate_source(source, args.out_dir, name)
    print(f"Translated {args.source} -> {args.out_dir}/{name}.*")


if __name__ == "__main__":
    main()
