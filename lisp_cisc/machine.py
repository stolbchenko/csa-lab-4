from __future__ import annotations

import argparse
import ast
import struct
from dataclasses import dataclass, field
from pathlib import Path

from lisp_cisc.isa import (
    DATA_STACK_TOP,
    FLAG_IF,
    FLAG_NF,
    FLAG_ZF,
    INSTR_INTERRUPT_VECTOR_0,
    Opcode,
    Reg,
    instruction_word_count_from_word0,
    to_signed32,
    to_unsigned32,
)


@dataclass
class IOController:
    schedule: list[tuple[int, str]] = field(default_factory=list)
    output_buffer: list[str] = field(default_factory=list)
    input_port_value: int = 0
    input_ready: bool = False
    irq_pending: bool = False
    sched_index: int = 0
    overrun_count: int = 0

    def tick_update(self, current_tick: int) -> list[str]:
        events: list[str] = []
        while self.sched_index < len(self.schedule) and self.schedule[self.sched_index][0] <= current_tick:
            _, ch = self.schedule[self.sched_index]
            if self.input_ready:
                self.overrun_count += 1
                events.append(f"INPUT overrun: unread 0x{self.input_port_value:02X} replaced by 0x{ord(ch):02X}")
            else:
                events.append(f"INPUT ready: port 0x00 <- 0x{ord(ch):02X}")
            self.input_port_value = ord(ch)
            self.input_ready = True
            self.irq_pending = True
            self.sched_index += 1
        return events

    def has_irq(self) -> bool:
        return self.irq_pending

    def ack_irq(self) -> None:
        self.irq_pending = False

    def read_port(self, port: int) -> int:
        if port == 0x00:
            value = self.input_port_value if self.input_ready else 0
            self.input_ready = False
            self.irq_pending = False
            return value
        return 0

    def write_port(self, port: int, value: int) -> None:
        if port == 0x01:
            self.output_buffer.append(chr(value & 0xFF))


@dataclass
class DataPath:
    instr_mem: list[int]
    data_mem: list[int]
    io: IOController

    r0: int = 0
    r1: int = 0
    pc: int = 0
    sp: int = DATA_STACK_TOP
    mar: int = 0
    ir_words: list[int] = field(default_factory=list)
    flags: int = 0

    def get_reg(self, sel: int) -> int:
        if sel == Reg.R0:
            return self.r0
        if sel == Reg.R1:
            return self.r1
        if sel == Reg.SP:
            return self.sp
        if sel == Reg.PC:
            return self.pc
        raise ValueError(f"Bad reg sel {sel}")

    def set_reg(self, sel: int, value: int) -> None:
        value = to_unsigned32(value)
        if sel == Reg.R0:
            self.r0 = value
        elif sel == Reg.R1:
            self.r1 = value
        elif sel == Reg.SP:
            self.sp = value
        elif sel == Reg.PC:
            self.pc = value
        else:
            raise ValueError(f"Bad reg sel {sel}")

    def update_zn_flags(self, value: int) -> None:
        signed = to_signed32(value)
        if signed == 0:
            self.flags |= FLAG_ZF
        else:
            self.flags &= ~FLAG_ZF
        if signed < 0:
            self.flags |= FLAG_NF
        else:
            self.flags &= ~FLAG_NF

    def set_if(self, enabled: bool) -> None:
        if enabled:
            self.flags |= FLAG_IF
        else:
            self.flags &= ~FLAG_IF

    def get_zf(self) -> bool:
        return bool(self.flags & FLAG_ZF)

    def get_nf(self) -> bool:
        return bool(self.flags & FLAG_NF)

    def get_if(self) -> bool:
        return bool(self.flags & FLAG_IF)

    def signal_fetch_word(self) -> None:
        word = self.instr_mem[self.pc] if self.pc < len(self.instr_mem) else 0
        self.ir_words.append(to_unsigned32(word))
        self.pc = to_unsigned32(self.pc + 1)

    def reset_ir(self) -> None:
        self.ir_words = []

    def signal_data_read(self) -> int:
        return self.data_mem[self.mar]

    def signal_data_write(self, value: int) -> None:
        self.data_mem[self.mar] = value & 0xFFFF_FFFF

    def signal_alu_add(self, lhs: int, rhs: int) -> int:
        result = to_unsigned32(to_signed32(lhs) + to_signed32(rhs))
        self.update_zn_flags(result)
        return result

    def signal_alu_sub(self, lhs: int, rhs: int) -> int:
        result = to_unsigned32(to_signed32(lhs) - to_signed32(rhs))
        self.update_zn_flags(result)
        return result

    def signal_alu_mul(self, lhs: int, rhs: int) -> int:
        result = to_unsigned32(to_signed32(lhs) * to_signed32(rhs))
        self.update_zn_flags(result)
        return result

    def signal_alu_div(self, lhs: int, rhs: int) -> int:
        a, b = to_signed32(lhs), to_signed32(rhs)
        if b == 0:
            raise ZeroDivisionError("ALU div by zero")
        q = abs(a) // abs(b)
        if (a < 0) ^ (b < 0):
            q = -q
        result = to_unsigned32(q)
        self.update_zn_flags(result)
        return result

    def signal_alu_mod(self, lhs: int, rhs: int) -> int:
        a, b = to_signed32(lhs), to_signed32(rhs)
        if b == 0:
            raise ZeroDivisionError("ALU mod by zero")
        q = abs(a) // abs(b)
        if (a < 0) ^ (b < 0):
            q = -q
        r = a - q * b
        result = to_unsigned32(r)
        self.update_zn_flags(result)
        return result

    def signal_alu_and(self, lhs: int, rhs: int) -> int:
        result = to_unsigned32(lhs & rhs)
        self.update_zn_flags(result)
        return result

    def signal_alu_or(self, lhs: int, rhs: int) -> int:
        result = to_unsigned32(lhs | rhs)
        self.update_zn_flags(result)
        return result

    def signal_alu_cmp(self, lhs: int, rhs: int) -> None:
        diff = to_unsigned32(to_signed32(lhs) - to_signed32(rhs))
        self.update_zn_flags(diff)

    def signal_stack_push_value(self, value: int) -> None:
        self.sp = to_unsigned32(self.sp - 1)
        self.mar = self.sp
        self.signal_data_write(value)

    def signal_stack_pop(self) -> int:
        self.mar = self.sp
        result = self.signal_data_read()
        self.sp = to_unsigned32(self.sp + 1)
        return result

    def signal_io_in(self, port: int) -> int:
        return self.io.read_port(port)

    def signal_io_out(self, port: int, value: int) -> None:
        self.io.write_port(port, value)


@dataclass
class _DecodedInstr:
    opcode: int
    dst: int
    src: int
    mode: int
    operand: int
    n_words: int
    raw_words: tuple[int, ...]


@dataclass(slots=True)
class _LogRecord:
    tick: int
    fields: tuple[str, ...]
    action: str


def _decode_words(words: list[int]) -> _DecodedInstr:
    word0 = words[0] & 0xFFFF_FFFF
    opcode = (word0 >> 24) & 0xFF
    dst = (word0 >> 20) & 0x0F
    src = (word0 >> 16) & 0x0F
    mode = (word0 >> 8) & 0xFF
    n_words = instruction_word_count_from_word0(word0)
    operand = 0
    if n_words >= 2 and len(words) >= 2:
        operand = to_signed32(words[1] & 0xFFFF_FFFF)
    raw_words = tuple(w & 0xFFFF_FFFF for w in words)
    return _DecodedInstr(opcode, dst, src, mode, operand, n_words, raw_words)


def _mnemonic_for(instr: _DecodedInstr) -> str:
    from lisp_cisc.isa import DecodedInstr as IsaDecodedInstr
    from lisp_cisc.isa import mnemonic

    isa_instr = IsaDecodedInstr(
        opcode=instr.opcode,
        dst=instr.dst,
        src=instr.src,
        mode=instr.mode,
        operand=instr.operand,
        n_words=instr.n_words,
        raw_words=instr.raw_words,
    )
    return mnemonic(isa_instr)


def _format_log_records(records: list[_LogRecord]) -> list[str]:
    if not records:
        return []
    tick_width = max(4, *(len(str(record.tick)) for record in records))
    rows = [(f"TICK: {record.tick:0{tick_width}d}", *record.fields) for record in records]
    widths = [max(len(row[index]) for row in rows) + 2 for index in range(len(rows[0]))]
    lines = []
    for row, record in zip(rows, records, strict=True):
        prefix = " ".join(f"{field:<{widths[index]}}|" for index, field in enumerate(row))
        lines.append(f"{prefix} ACT: {record.action}")
    return lines


class ControlUnit:
    def __init__(self, dp: DataPath, max_ticks: int = 1_000_000) -> None:
        self.dp = dp
        self.tick_count = 0
        self.instr_count = 0
        self.max_ticks = max_ticks
        self.state = "FETCH_W0"
        self.instr: _DecodedInstr | None = None
        self.in_interrupt = False
        self.halted = False
        self.log: list[_LogRecord] = []
        self.int_step = 0
        self.pop_value: int = 0
        self.tick_action: str = ""
        self.tick_phase: str = self.state
        self.io_events: list[str] = []
        self.current_instr_pc = 0
        self.interrupt_return_pc = 0
        self.iret_flags_value = 0
        self.iret_pc_value = 0
        self.iret_r1_value = 0

    def step(self) -> bool:
        if self.halted:
            return False
        if self.tick_count >= self.max_ticks:
            self.io_events = []
            self.tick_action = "LIMIT REACHED, halting"
            self.tick_phase = "LIMIT"
            self.instr = None
            self._log_tick()
            self.halted = True
            return False

        self.tick_count += 1
        self.io_events = self.dp.io.tick_update(self.tick_count)
        self.tick_action = ""
        self.tick_phase = self.state

        if self.state == "FETCH_W0" and not self.in_interrupt:
            self.current_instr_pc = self.dp.pc

        if self.dp.io.has_irq() and self.dp.get_if() and not self.in_interrupt:
            self._accept_interrupt()
        else:
            self._dispatch()
        self._log_tick()
        return not self.halted

    def _accept_interrupt(self) -> None:
        self.dp.io.ack_irq()
        self.interrupt_return_pc = to_unsigned32(self.current_instr_pc)
        self.dp.pc = self.interrupt_return_pc
        self.dp.reset_ir()
        self.instr = None
        self.in_interrupt = True
        self.int_step = 0
        self.state = "INT_PUSH_R0"
        self.tick_phase = "INT_ACCEPT"
        self.tick_action = f"[INT] interrupt accepted: restart PC=0x{self.interrupt_return_pc:04X}"

    def _log_tick(self) -> None:
        phase = self._format_phase()
        op = self._format_op()
        ir = "[" + ", ".join(f"{w:08X}" for w in self.dp.ir_words) + "]"
        action_parts = []
        if self.io_events:
            action_parts.append("; ".join(self.io_events))
        action_parts.append(self.tick_action or self.state)
        action = " | ".join(action_parts)
        self.log.append(
            _LogRecord(
                tick=self.tick_count,
                fields=(
                    phase,
                    f"PC: {self.dp.pc:04X}",
                    f"OP: {op}",
                    f"R0: {self.dp.r0:08X}",
                    f"R1: {self.dp.r1:08X}",
                    f"SP: {self.dp.sp:04X}",
                    f"MAR: {self.dp.mar:04X}",
                    f"Z: {int(self.dp.get_zf())}",
                    f"N: {int(self.dp.get_nf())}",
                    f"I: {int(self.dp.get_if())}",
                    f"IRQ: {int(self.dp.io.has_irq())}",
                    f"IR: {ir}",
                ),
                action=action,
            )
        )

    def _format_phase(self) -> str:
        labels = {
            "FETCH_W0": "FETCH1",
            "FETCH_NEXT": "FETCH2",
            "INT_ACCEPT": "INT ACCEPT",
            "INT_LOAD_VECTOR": "INT VECTOR",
        }
        phase = labels.get(self.tick_phase, self.tick_phase)
        if phase.startswith("EXEC_"):
            phase = "EXEC " + phase.removeprefix("EXEC_")
        elif phase.startswith("INT_"):
            phase = "INT " + phase.removeprefix("INT_")
        if self.in_interrupt and not phase.startswith("INT"):
            phase = "INT " + phase
        return phase

    def _format_op(self) -> str:
        if self.instr is None or len(self.instr.raw_words) < self.instr.n_words:
            return ""
        return _mnemonic_for(self.instr)

    def _dispatch(self) -> None:
        s = self.state
        if s == "FETCH_W0":
            self.dp.reset_ir()
            self.dp.signal_fetch_word()
            self.instr = _decode_words(self.dp.ir_words)
            if self.instr.n_words > 1:
                self.tick_action = (
                    f"FETCH+DECODE word0 -> 0x{self.dp.ir_words[0]:08X}," f" need {self.instr.n_words - 1} more"
                )
                self.state = "FETCH_NEXT"
            else:
                self.tick_action = (
                    f"FETCH+DECODE word0 -> 0x{self.dp.ir_words[0]:08X}," f" begin {_mnemonic_for(self.instr)}"
                )
                self._begin_execute()
            return
        if s == "FETCH_NEXT":
            self.dp.signal_fetch_word()
            assert self.instr is not None
            self.instr = _decode_words(self.dp.ir_words)
            got = len(self.dp.ir_words)
            needed = self.instr.n_words
            if got < needed:
                self.tick_action = f"FETCH word{got - 1} -> 0x{self.dp.ir_words[-1]:08X}, need {needed - got} more"
            else:
                self.tick_action = (
                    f"FETCH word{got - 1} -> 0x{self.dp.ir_words[-1]:08X}, " f"begin {_mnemonic_for(self.instr)}"
                )
                self._begin_execute()
            return

        if s == "EXEC_LOAD_IMM":
            self._exec_load_imm()
        elif s == "EXEC_LOAD_MEM":
            self._exec_load_mem()
        elif s == "EXEC_STORE_MEM":
            self._exec_store_mem()
        elif s == "EXEC_MOVE_REG":
            self._exec_move_reg()
        elif s == "EXEC_LOAD_IND":
            self._exec_load_ind()
        elif s == "EXEC_STORE_IND":
            self._exec_store_ind()
        elif s == "EXEC_ADD_REG":
            self._exec_add_reg()
        elif s == "EXEC_ALU_IMM":
            self._exec_alu_imm()
        elif s == "EXEC_ALU_MEM":
            self._exec_alu_mem()
        elif s == "EXEC_PUSH":
            self._exec_push()
        elif s == "EXEC_POP":
            self._exec_pop()
        elif s == "EXEC_JMP":
            self._exec_jmp()
        elif s == "EXEC_BRANCH":
            self._exec_branch()
        elif s == "EXEC_CALL_PUSH":
            self._exec_call_push()
        elif s == "EXEC_CALL_JUMP":
            self._exec_call_jump()
        elif s == "EXEC_RET":
            self._exec_ret()
        elif s == "EXEC_IRET_POP_FLAGS":
            self._exec_iret_pop_flags()
        elif s == "EXEC_IRET_POP_PC":
            self._exec_iret_pop_pc()
        elif s == "EXEC_IRET_POP_R1":
            self._exec_iret_pop_r1()
        elif s == "EXEC_IRET_POP_R0":
            self._exec_iret_pop_r0()
        elif s == "EXEC_SUM_IMM":
            self._exec_sum_imm()
        elif s == "EXEC_VMUL_IMM":
            self._exec_vmul_imm()
        elif s == "EXEC_VSUB_IMM":
            self._exec_vsub_imm()
        elif s == "EXEC_IO":
            self._exec_io()
        elif s == "EXEC_EI":
            self.dp.set_if(True)
            self.tick_action = "EI: FLAGS.IF = 1"
            self._finalize()
        elif s == "EXEC_DI":
            self.dp.set_if(False)
            self.tick_action = "DI: FLAGS.IF = 0"
            self._finalize()
        elif s == "EXEC_HALT":
            self.halted = True
            self.tick_action = "HALT"
        elif s == "EXEC_NOP":
            self.tick_action = "NOP"
            self._finalize()
        elif s == "INT_PUSH_R0":
            self._int_push_r0()
        elif s == "INT_PUSH_R1":
            self._int_push_r1()
        elif s == "INT_PUSH_PC":
            self._int_push_pc()
        elif s == "INT_PUSH_FLAGS":
            self._int_push_flags()
        elif s == "INT_LOAD_VECTOR":
            self._int_load_vector()
        else:
            raise RuntimeError(f"Unknown state: {s}")

    def _begin_execute(self) -> None:
        assert self.instr is not None
        op = self.instr.opcode
        state_map = {
            Opcode.NOP: "EXEC_NOP",
            Opcode.HALT: "EXEC_HALT",
            Opcode.LOAD_IMM: "EXEC_LOAD_IMM",
            Opcode.LOAD_MEM: "EXEC_LOAD_MEM",
            Opcode.STORE_MEM: "EXEC_STORE_MEM",
            Opcode.MOVE_REG: "EXEC_MOVE_REG",
            Opcode.LOAD_IND: "EXEC_LOAD_IND",
            Opcode.STORE_IND: "EXEC_STORE_IND",
            Opcode.ADD_REG: "EXEC_ADD_REG",
            Opcode.PUSH: "EXEC_PUSH",
            Opcode.POP: "EXEC_POP",
            Opcode.JMP: "EXEC_JMP",
            Opcode.CALL: "EXEC_CALL_PUSH",
            Opcode.RET: "EXEC_RET",
            Opcode.IRET: "EXEC_IRET_POP_FLAGS",
            Opcode.EI: "EXEC_EI",
            Opcode.DI: "EXEC_DI",
        }
        if op in state_map:
            self.state = state_map[Opcode(op)]
        elif op in (Opcode.ADD_IMM, Opcode.SUB_IMM, Opcode.CMP_IMM):
            self.state = "EXEC_ALU_IMM"
        elif op in (
            Opcode.ADD_MEM,
            Opcode.SUB_MEM,
            Opcode.MUL_MEM,
            Opcode.DIV_MEM,
            Opcode.MOD_MEM,
            Opcode.CMP_MEM,
            Opcode.AND_MEM,
            Opcode.OR_MEM,
        ):
            self.state = "EXEC_ALU_MEM"
        elif op in (Opcode.JZ, Opcode.JNZ, Opcode.JN, Opcode.JP):
            self.state = "EXEC_BRANCH"
        elif op in (Opcode.IN_PORT, Opcode.OUT_PORT):
            self.state = "EXEC_IO"
        elif op == Opcode.SUM_IMM:
            self.state = "EXEC_SUM_IMM"
        elif op == Opcode.VMUL_IMM:
            self.state = "EXEC_VMUL_IMM"
        elif op == Opcode.VSUB_IMM:
            self.state = "EXEC_VSUB_IMM"
        else:
            raise RuntimeError(f"Unknown opcode 0x{op:02X}")

    def _exec_load_imm(self) -> None:
        assert self.instr is not None
        self.dp.set_reg(self.instr.dst, to_unsigned32(self.instr.operand))
        self.tick_action = f"LOAD reg{self.instr.dst}, #{self.instr.operand}"
        self._finalize()

    def _exec_load_mem(self) -> None:
        assert self.instr is not None
        self.dp.mar = to_unsigned32(self.instr.operand)
        result = self.dp.signal_data_read()
        self.dp.set_reg(self.instr.dst, result)
        self.tick_action = f"LOAD reg{self.instr.dst}, [0x{self.dp.mar:04X}]"
        self._finalize()

    def _exec_store_mem(self) -> None:
        assert self.instr is not None
        self.dp.mar = to_unsigned32(self.instr.operand)
        value = self.dp.get_reg(self.instr.src)
        self.dp.signal_data_write(value)
        self.tick_action = f"STORE [0x{self.dp.mar:04X}], reg{self.instr.src}=0x{value:08X}"
        self._finalize()

    def _exec_move_reg(self) -> None:
        assert self.instr is not None
        value = self.dp.get_reg(self.instr.src)
        self.dp.set_reg(self.instr.dst, value)
        self.tick_action = f"MOVE reg{self.instr.dst}, reg{self.instr.src}"
        self._finalize()

    def _exec_load_ind(self) -> None:
        assert self.instr is not None
        self.dp.mar = self.dp.get_reg(self.instr.src)
        result = self.dp.signal_data_read()
        self.dp.set_reg(self.instr.dst, result)
        self.tick_action = f"LOAD reg{self.instr.dst}, [reg{self.instr.src}]=0x{self.dp.mar:04X}"
        self._finalize()

    def _exec_store_ind(self) -> None:
        assert self.instr is not None
        self.dp.mar = self.dp.get_reg(self.instr.dst)
        value = self.dp.get_reg(self.instr.src)
        self.dp.signal_data_write(value)
        self.tick_action = f"STORE [reg{self.instr.dst}]=0x{self.dp.mar:04X}, reg{self.instr.src}=0x{value:08X}"
        self._finalize()

    def _exec_add_reg(self) -> None:
        assert self.instr is not None
        lhs = self.dp.get_reg(self.instr.dst)
        rhs = self.dp.get_reg(self.instr.src)
        self.dp.set_reg(self.instr.dst, self.dp.signal_alu_add(lhs, rhs))
        self.tick_action = f"ADD reg{self.instr.dst}, reg{self.instr.src}"
        self._finalize()

    def _exec_alu_imm(self) -> None:
        assert self.instr is not None
        op = self.instr.opcode
        lhs = self.dp.get_reg(self.instr.dst)
        rhs = to_unsigned32(self.instr.operand)
        if op == Opcode.ADD_IMM:
            self.dp.set_reg(self.instr.dst, self.dp.signal_alu_add(lhs, rhs))
            self.tick_action = f"ADD reg{self.instr.dst}, #{self.instr.operand}"
        elif op == Opcode.SUB_IMM:
            self.dp.set_reg(self.instr.dst, self.dp.signal_alu_sub(lhs, rhs))
            self.tick_action = f"SUB reg{self.instr.dst}, #{self.instr.operand}"
        elif op == Opcode.CMP_IMM:
            self.dp.signal_alu_cmp(lhs, rhs)
            self.tick_action = f"CMP reg{self.instr.dst}, #{self.instr.operand}"
        self._finalize()

    def _exec_alu_mem(self) -> None:
        assert self.instr is not None
        self.dp.mar = to_unsigned32(self.instr.operand)
        result = self.dp.signal_data_read()
        lhs = self.dp.get_reg(self.instr.dst)
        op = self.instr.opcode
        op_name = Opcode(self.instr.opcode).name
        if op == Opcode.ADD_MEM:
            self.dp.set_reg(self.instr.dst, self.dp.signal_alu_add(lhs, result))
        elif op == Opcode.SUB_MEM:
            self.dp.set_reg(self.instr.dst, self.dp.signal_alu_sub(lhs, result))
        elif op == Opcode.MUL_MEM:
            self.dp.set_reg(self.instr.dst, self.dp.signal_alu_mul(lhs, result))
        elif op == Opcode.DIV_MEM:
            self.dp.set_reg(self.instr.dst, self.dp.signal_alu_div(lhs, result))
        elif op == Opcode.MOD_MEM:
            self.dp.set_reg(self.instr.dst, self.dp.signal_alu_mod(lhs, result))
        elif op == Opcode.CMP_MEM:
            self.dp.signal_alu_cmp(lhs, result)
        elif op == Opcode.AND_MEM:
            self.dp.set_reg(self.instr.dst, self.dp.signal_alu_and(lhs, result))
        elif op == Opcode.OR_MEM:
            self.dp.set_reg(self.instr.dst, self.dp.signal_alu_or(lhs, result))
        self.tick_action = f"{op_name} reg{self.instr.dst}, [0x{self.dp.mar:04X}]"
        self._finalize()

    def _exec_push(self) -> None:
        assert self.instr is not None
        value = self.dp.get_reg(self.instr.src)
        self.dp.sp = to_unsigned32(self.dp.sp - 1)
        self.dp.mar = self.dp.sp
        self.dp.signal_data_write(value)
        self.tick_action = f"PUSH reg{self.instr.src}=0x{value:08X} -> [0x{self.dp.sp:04X}]"
        self._finalize()

    def _exec_pop(self) -> None:
        assert self.instr is not None
        self.dp.mar = self.dp.sp
        result = self.dp.signal_data_read()
        self.dp.set_reg(self.instr.dst, result)
        self.dp.sp = to_unsigned32(self.dp.sp + 1)
        self.tick_action = f"POP reg{self.instr.dst} <- [0x{self.dp.mar:04X}]"
        self._finalize()

    def _exec_jmp(self) -> None:
        assert self.instr is not None
        self.dp.pc = to_unsigned32(self.instr.operand)
        self.tick_action = f"JMP -> 0x{self.dp.pc:04X}"
        self._finalize()

    def _exec_branch(self) -> None:
        assert self.instr is not None
        op = self.instr.opcode
        taken = False
        if (
            (op == Opcode.JZ and self.dp.get_zf())
            or (op == Opcode.JNZ and not self.dp.get_zf())
            or (op == Opcode.JN and self.dp.get_nf())
            or (op == Opcode.JP and not self.dp.get_nf())
        ):
            taken = True
        if taken:
            self.dp.pc = to_unsigned32(self.instr.operand)
            self.tick_action = f"{Opcode(op).name} taken -> 0x{self.dp.pc:04X}"
        else:
            self.tick_action = f"{Opcode(op).name} not taken"
        self._finalize()

    def _exec_call_push(self) -> None:
        assert self.instr is not None
        self.tick_action = f"CALL prepare return PC=0x{self.dp.pc:04X}"
        self.state = "EXEC_CALL_JUMP"

    def _exec_call_jump(self) -> None:
        assert self.instr is not None
        return_pc = self.dp.pc
        self.dp.sp = to_unsigned32(self.dp.sp - 1)
        self.dp.mar = self.dp.sp
        self.dp.signal_data_write(return_pc)
        self.dp.pc = to_unsigned32(self.instr.operand)
        self.tick_action = (
            f"CALL commit: push PC=0x{return_pc:04X} -> [0x{self.dp.sp:04X}], " f"jump -> 0x{self.dp.pc:04X}"
        )
        self._finalize()

    def _exec_ret(self) -> None:
        self.dp.mar = self.dp.sp
        result = self.dp.signal_data_read()
        self.dp.pc = to_unsigned32(result)
        self.dp.sp = to_unsigned32(self.dp.sp + 1)
        self.tick_action = f"RET pop PC <- [0x{self.dp.mar:04X}]"
        self._finalize()

    def _exec_iret_pop_flags(self) -> None:
        self.dp.mar = self.dp.sp
        self.iret_flags_value = self.dp.signal_data_read() & 0xFF
        self.tick_action = f"IRET read FLAGS <- [0x{self.dp.mar:04X}]"
        self.state = "EXEC_IRET_POP_PC"

    def _exec_iret_pop_pc(self) -> None:
        self.dp.mar = to_unsigned32(self.dp.sp + 1)
        self.iret_pc_value = to_unsigned32(self.dp.signal_data_read())
        self.tick_action = f"IRET read PC <- [0x{self.dp.mar:04X}]"
        self.state = "EXEC_IRET_POP_R1"

    def _exec_iret_pop_r1(self) -> None:
        self.dp.mar = to_unsigned32(self.dp.sp + 2)
        self.iret_r1_value = to_unsigned32(self.dp.signal_data_read())
        self.tick_action = f"IRET read R1 <- [0x{self.dp.mar:04X}]"
        self.state = "EXEC_IRET_POP_R0"

    def _exec_iret_pop_r0(self) -> None:
        self.dp.mar = to_unsigned32(self.dp.sp + 3)
        r0_value = to_unsigned32(self.dp.signal_data_read())
        old_sp = self.dp.sp
        self.dp.flags = self.iret_flags_value
        self.dp.pc = self.iret_pc_value
        self.dp.r1 = self.iret_r1_value
        self.dp.r0 = r0_value
        self.dp.sp = to_unsigned32(self.dp.sp + 4)
        self.tick_action = (
            f"IRET commit from frame [0x{old_sp:04X}]: "
            f"FLAGS=0x{self.dp.flags:02X}, PC=0x{self.dp.pc:04X}, "
            f"R1=0x{self.dp.r1:08X}, R0=0x{self.dp.r0:08X}"
        )
        self.in_interrupt = False
        self._finalize()

    def _exec_sum_imm(self) -> None:
        assert self.instr is not None
        n = self.instr.mode
        values = [to_signed32(self.dp.ir_words[i + 1] & 0xFFFF_FFFF) for i in range(n)]
        result = sum(values)
        self.dp.set_reg(self.instr.dst, to_unsigned32(result))
        self.dp.update_zn_flags(to_unsigned32(result))
        self.tick_action = f"SUM reg{self.instr.dst} values={values} -> {result}"
        self._finalize()

    def _exec_vmul_imm(self) -> None:
        assert self.instr is not None
        n = self.instr.mode
        values = [to_signed32(self.dp.ir_words[i + 1] & 0xFFFF_FFFF) for i in range(n)]
        result = 1
        for v in values:
            result *= v
        self.dp.set_reg(self.instr.dst, to_unsigned32(result))
        self.dp.update_zn_flags(to_unsigned32(result))
        self.tick_action = f"MUL reg{self.instr.dst} values={values} -> {result}"
        self._finalize()

    def _exec_vsub_imm(self) -> None:
        assert self.instr is not None
        n = self.instr.mode
        values = [to_signed32(self.dp.ir_words[i + 1] & 0xFFFF_FFFF) for i in range(n)]
        result = values[0]
        for v in values[1:]:
            result -= v
        self.dp.set_reg(self.instr.dst, to_unsigned32(result))
        self.dp.update_zn_flags(to_unsigned32(result))
        self.tick_action = f"SUB reg{self.instr.dst} values={values} -> {result}"
        self._finalize()

    def _exec_io(self) -> None:
        assert self.instr is not None
        if self.instr.opcode == Opcode.IN_PORT:
            port = to_unsigned32(self.instr.operand)
            value = self.dp.signal_io_in(port)
            self.dp.set_reg(self.instr.dst, value)
            self.tick_action = f"IN port=0x{port:02X} -> reg{self.instr.dst}=0x{value:02X}"
        else:
            port = to_unsigned32(self.instr.operand)
            value = self.dp.get_reg(self.instr.src)
            self.dp.signal_io_out(port, value)
            ascii_hint = f" ('{chr(value & 0xFF)}')" if 0x20 <= (value & 0xFF) <= 0x7E else ""
            self.tick_action = f"OUT port=0x{port:02X}, reg{self.instr.src}=0x{value:02X}{ascii_hint}"
        self._finalize()

    def _int_push_r0(self) -> None:
        self.dp.mar = to_unsigned32(self.dp.sp - 1)
        self.dp.sp = self.dp.mar
        self.dp.signal_data_write(self.dp.r0)
        self.tick_action = f"[INT] push R0=0x{self.dp.r0:08X}"
        self.state = "INT_PUSH_R1"

    def _int_push_r1(self) -> None:
        self.dp.mar = to_unsigned32(self.dp.sp - 1)
        self.dp.sp = self.dp.mar
        self.dp.signal_data_write(self.dp.r1)
        self.tick_action = f"[INT] push R1=0x{self.dp.r1:08X}"
        self.state = "INT_PUSH_PC"

    def _int_push_pc(self) -> None:
        self.dp.mar = to_unsigned32(self.dp.sp - 1)
        self.dp.sp = self.dp.mar
        self.dp.signal_data_write(self.interrupt_return_pc)
        self.tick_action = f"[INT] push restart PC=0x{self.interrupt_return_pc:04X}"
        self.state = "INT_PUSH_FLAGS"

    def _int_push_flags(self) -> None:
        self.dp.mar = to_unsigned32(self.dp.sp - 1)
        self.dp.sp = self.dp.mar
        self.dp.signal_data_write(self.dp.flags)
        self.tick_action = f"[INT] push FLAGS=0x{self.dp.flags:02X}"
        self.dp.set_if(False)
        self.state = "INT_LOAD_VECTOR"

    def _int_load_vector(self) -> None:
        target = self.dp.instr_mem[INSTR_INTERRUPT_VECTOR_0] if len(self.dp.instr_mem) > INSTR_INTERRUPT_VECTOR_0 else 0
        self.dp.pc = to_unsigned32(target)
        self.tick_action = f"[INT] load vector -> PC=0x{self.dp.pc:04X}"
        self.state = "FETCH_W0"

    def _finalize(self) -> None:
        self.dp.reset_ir()
        self.instr_count += 1
        self.state = "FETCH_W0"


@dataclass
class SimulationResult:
    output: str
    log: list[str]
    tick_count: int
    instr_count: int


def unpack_words_be(data: bytes) -> list[int]:
    n = len(data) // 4
    return list(struct.unpack(f">{n}I", data[: n * 4]))


def load_schedule(path: Path | None) -> list[tuple[int, str]]:
    if path is None or not path.exists():
        return []
    raw = ast.literal_eval(path.read_text(encoding="utf-8").strip())
    result: list[tuple[int, str]] = []
    for tick, value in raw:
        char = chr(value) if isinstance(value, int) else str(value)
        result.append((int(tick), char))
    return result


def _coerce_char(value: object) -> str:
    if isinstance(value, int):
        return chr(value)
    if isinstance(value, str):
        return value
    raise TypeError(f"schedule value must be str or int, got {type(value).__name__}")


def simulate(
    instr_words: list[int],
    data_words: list[int],
    schedule: list[tuple[int, str]],
    max_ticks: int = 1_000_000,
) -> SimulationResult:
    io = IOController(schedule=list(schedule))
    dp = DataPath(instr_mem=instr_words, data_mem=data_words, io=io)
    cu = ControlUnit(dp, max_ticks=max_ticks)

    while cu.step():
        pass

    return SimulationResult(
        output="".join(io.output_buffer),
        log=_format_log_records(cu.log),
        tick_count=cu.tick_count,
        instr_count=cu.instr_count,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="CISC/Harvard processor simulator")
    parser.add_argument("--instr", type=Path, required=True, help="Instruction memory binary")
    parser.add_argument("--data", type=Path, required=True, help="Data memory binary")
    parser.add_argument("--input", type=Path, default=None, help="Interrupt schedule TXT")
    parser.add_argument("--max-ticks", type=int, default=1_000_000, help="Hard limit on ticks")
    parser.add_argument(
        "--stop-at-tick", type=int, default=None, metavar="N", help="Pause after tick N and print processor state"
    )
    parser.add_argument("--log", type=Path, default=None, help="Where to save tick log")
    args = parser.parse_args()

    instrs = unpack_words_be(args.instr.read_bytes())
    data = unpack_words_be(args.data.read_bytes())
    schedule = load_schedule(args.input)

    effective_max = args.stop_at_tick if args.stop_at_tick is not None else args.max_ticks
    result = simulate(instrs, data, schedule, effective_max)

    if args.log is not None:
        args.log.write_text("\n".join(result.log) + "\n", encoding="utf-8")

    if args.stop_at_tick is not None:
        last_line = next(
            (line for line in reversed(result.log) if "LIMIT REACHED" not in line),
            "(no ticks executed)",
        )
        print(f"--- Paused at tick {result.tick_count} ---")
        print(last_line)
        print(f"Output so far ({len(result.output)} chars): {result.output!r}")
        print(f"Instructions retired: {result.instr_count}")
    else:
        print(f"--- Output ({len(result.output)} chars) ---")
        print(result.output)
        print("--- Stats ---")
        print(f"Ticks: {result.tick_count}")
        print(f"Instructions: {result.instr_count}")


if __name__ == "__main__":
    main()
