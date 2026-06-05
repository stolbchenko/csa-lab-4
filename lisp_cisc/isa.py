from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Opcode(IntEnum):
    NOP = 0x00
    HALT = 0x01
    LOAD_MEM = 0x10
    LOAD_IMM = 0x11
    STORE_MEM = 0x12
    MOVE_REG = 0x13
    LOAD_IND = 0x14
    STORE_IND = 0x15
    ADD_REG = 0x16
    ADD_MEM = 0x20
    ADD_IMM = 0x21
    SUB_MEM = 0x22
    SUB_IMM = 0x23
    MUL_MEM = 0x24
    DIV_MEM = 0x25
    MOD_MEM = 0x26
    CMP_MEM = 0x27
    CMP_IMM = 0x28
    AND_MEM = 0x29
    OR_MEM = 0x2A
    PUSH = 0x30
    POP = 0x31
    JMP = 0x40
    JZ = 0x41
    JNZ = 0x42
    JN = 0x43
    JP = 0x44
    CALL = 0x50
    RET = 0x51
    IRET = 0x52
    IN_PORT = 0x60
    OUT_PORT = 0x61
    EI = 0x70
    DI = 0x71
    VSUM_IMM = 0x80
    VMUL_IMM = 0x81
    VSUB_IMM = 0x82


INSTR_WORDS: dict[int, int] = {
    Opcode.NOP: 1,
    Opcode.HALT: 1,
    Opcode.LOAD_MEM: 2,
    Opcode.LOAD_IMM: 2,
    Opcode.STORE_MEM: 2,
    Opcode.MOVE_REG: 1,
    Opcode.LOAD_IND: 1,
    Opcode.STORE_IND: 1,
    Opcode.ADD_REG: 1,
    Opcode.ADD_MEM: 2,
    Opcode.ADD_IMM: 2,
    Opcode.SUB_MEM: 2,
    Opcode.SUB_IMM: 2,
    Opcode.MUL_MEM: 2,
    Opcode.DIV_MEM: 2,
    Opcode.MOD_MEM: 2,
    Opcode.CMP_MEM: 2,
    Opcode.CMP_IMM: 2,
    Opcode.AND_MEM: 2,
    Opcode.OR_MEM: 2,
    Opcode.PUSH: 1,
    Opcode.POP: 1,
    Opcode.JMP: 2,
    Opcode.JZ: 2,
    Opcode.JNZ: 2,
    Opcode.JN: 2,
    Opcode.JP: 2,
    Opcode.CALL: 2,
    Opcode.RET: 1,
    Opcode.IRET: 1,
    Opcode.IN_PORT: 2,
    Opcode.OUT_PORT: 2,
    Opcode.EI: 1,
    Opcode.DI: 1,
}


VARARG_INSTRS: frozenset[int] = frozenset({Opcode.VSUM_IMM, Opcode.VMUL_IMM, Opcode.VSUB_IMM})


def instruction_word_count_from_word0(word0: int) -> int:
    opcode = (word0 >> 24) & 0xFF
    mode = (word0 >> 8) & 0xFF
    if opcode in VARARG_INSTRS:
        return 1 + mode
    return INSTR_WORDS.get(opcode, 1)


class Reg(IntEnum):
    R0 = 0
    R1 = 1
    SP = 4
    PC = 5


REG_NAMES: dict[int, str] = {r.value: r.name for r in Reg}


FLAG_ZF = 1 << 0
FLAG_NF = 1 << 1
FLAG_IF = 1 << 2


INSTR_PROGRAM_START = 0x0010
INSTR_INTERRUPT_VECTOR_0 = 0x0002
INSTR_INTERRUPT_HANDLER_BASE = 0x0E00

DATA_LITERAL_BASE = 0x0000
DATA_VARIABLE_BASE = 0x0400
DATA_STACK_TOP = 0x0F00


@dataclass(frozen=True)
class DecodedInstr:
    opcode: int
    dst: int
    src: int
    mode: int
    operand: int
    n_words: int
    raw_words: tuple[int, ...]


def encode_word0(opcode: int, dst: int = 0, src: int = 0, mode: int = 0) -> int:
    return ((opcode & 0xFF) << 24) | ((dst & 0x0F) << 20) | ((src & 0x0F) << 16) | ((mode & 0xFF) << 8)


def decode(words: list[int], pc: int) -> DecodedInstr:
    word0 = words[pc] & 0xFFFF_FFFF
    opcode = (word0 >> 24) & 0xFF
    dst = (word0 >> 20) & 0x0F
    src = (word0 >> 16) & 0x0F
    mode = (word0 >> 8) & 0xFF
    n_words = instruction_word_count_from_word0(word0)
    operand = 0
    raw = tuple(words[pc + i] & 0xFFFF_FFFF for i in range(min(n_words, len(words) - pc)))
    if n_words >= 2 and len(raw) >= 2:
        operand = to_signed32(words[pc + 1] & 0xFFFF_FFFF)
    return DecodedInstr(
        opcode=opcode,
        dst=dst,
        src=src,
        mode=mode,
        operand=operand,
        n_words=n_words,
        raw_words=raw,
    )


def to_signed32(value: int) -> int:
    value &= 0xFFFF_FFFF
    if value & 0x8000_0000:
        return value - 0x1_0000_0000
    return value


def to_unsigned32(value: int) -> int:
    return value & 0xFFFF_FFFF


def mnemonic(instr: DecodedInstr) -> str:
    op = Opcode(instr.opcode) if instr.opcode in (o.value for o in Opcode) else None
    if op is None:
        return f"??? 0x{instr.opcode:02X}"

    dst_name = REG_NAMES.get(instr.dst, f"R?{instr.dst}")
    src_name = REG_NAMES.get(instr.src, f"R?{instr.src}")
    arg_unsigned = instr.raw_words[1] if len(instr.raw_words) >= 2 else 0

    if op == Opcode.NOP:
        return "NOP"
    if op == Opcode.HALT:
        return "HALT"
    if op == Opcode.LOAD_IMM:
        return f"LOAD {dst_name}, #{instr.operand}"
    if op == Opcode.LOAD_MEM:
        return f"LOAD {dst_name}, [0x{arg_unsigned:04X}]"
    if op == Opcode.STORE_MEM:
        return f"STORE [0x{arg_unsigned:04X}], {src_name}"
    if op == Opcode.MOVE_REG:
        return f"MOVE {dst_name}, {src_name}"
    if op == Opcode.LOAD_IND:
        return f"LOAD {dst_name}, [{src_name}]"
    if op == Opcode.STORE_IND:
        return f"STORE [{dst_name}], {src_name}"
    if op == Opcode.ADD_REG:
        return f"ADD {dst_name}, {src_name}"
    if op == Opcode.ADD_MEM:
        return f"ADD {dst_name}, [0x{arg_unsigned:04X}]"
    if op == Opcode.ADD_IMM:
        return f"ADD {dst_name}, #{instr.operand}"
    if op == Opcode.SUB_MEM:
        return f"SUB {dst_name}, [0x{arg_unsigned:04X}]"
    if op == Opcode.SUB_IMM:
        return f"SUB {dst_name}, #{instr.operand}"
    if op == Opcode.MUL_MEM:
        return f"MUL {dst_name}, [0x{arg_unsigned:04X}]"
    if op == Opcode.DIV_MEM:
        return f"DIV {dst_name}, [0x{arg_unsigned:04X}]"
    if op == Opcode.MOD_MEM:
        return f"MOD {dst_name}, [0x{arg_unsigned:04X}]"
    if op == Opcode.CMP_MEM:
        return f"CMP {dst_name}, [0x{arg_unsigned:04X}]"
    if op == Opcode.CMP_IMM:
        return f"CMP {dst_name}, #{instr.operand}"
    if op == Opcode.AND_MEM:
        return f"AND {dst_name}, [0x{arg_unsigned:04X}]"
    if op == Opcode.OR_MEM:
        return f"OR {dst_name}, [0x{arg_unsigned:04X}]"
    if op == Opcode.PUSH:
        return f"PUSH {src_name}"
    if op == Opcode.POP:
        return f"POP {dst_name}"
    if op == Opcode.JMP:
        return f"JMP 0x{arg_unsigned:04X}"
    if op == Opcode.JZ:
        return f"JZ 0x{arg_unsigned:04X}"
    if op == Opcode.JNZ:
        return f"JNZ 0x{arg_unsigned:04X}"
    if op == Opcode.JN:
        return f"JN 0x{arg_unsigned:04X}"
    if op == Opcode.JP:
        return f"JP 0x{arg_unsigned:04X}"
    if op == Opcode.CALL:
        return f"CALL 0x{arg_unsigned:04X}"
    if op == Opcode.RET:
        return "RET"
    if op == Opcode.IRET:
        return "IRET"
    if op == Opcode.IN_PORT:
        return f"IN  port=0x{arg_unsigned:02X}"
    if op == Opcode.OUT_PORT:
        return f"OUT port=0x{arg_unsigned:02X}, {src_name}"
    if op == Opcode.EI:
        return "EI"
    if op == Opcode.DI:
        return "DI"
    if op == Opcode.VSUM_IMM:
        values = ", ".join(str(to_signed32(w)) for w in instr.raw_words[1:])
        return f"SUM {dst_name}, n={instr.mode}; {values}"
    if op == Opcode.VMUL_IMM:
        values = ", ".join(str(to_signed32(w)) for w in instr.raw_words[1:])
        return f"MUL {dst_name}, n={instr.mode}; {values}"
    if op == Opcode.VSUB_IMM:
        values = ", ".join(str(to_signed32(w)) for w in instr.raw_words[1:])
        return f"SUB {dst_name}, n={instr.mode}; {values}"
    return "???"
