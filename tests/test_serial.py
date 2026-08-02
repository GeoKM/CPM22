"""Tests for the 8251 USART (M2)."""

from __future__ import annotations

import pytest

from cpm22.cpu8080 import CPU8080
from cpm22.memory import Memory
from cpm22.serial import USART8251


def make_system():
    mem = Memory()
    cpu = CPU8080(mem)
    usart = USART8251()
    usart.attach_to_cpu(cpu)
    return cpu, usart


def test_usart_status_tx_ready():
    cpu, usart = make_system()
    # Status register should be readable
    cpu.PC = 0
    cpu.mem.wb(0, 0xDB)  # IN
    cpu.mem.wb(1, 0x11)  # control port
    cpu.step()
    assert usart.STATUS_TX_READY & cpu.A


def test_usart_data_write():
    cpu, usart = make_system()
    cpu.PC = 0
    cpu.mem.wb(0, 0x3E)  # MVI A
    cpu.mem.wb(1, 0x42)  # value
    cpu.mem.wb(2, 0xD3)  # OUT
    cpu.mem.wb(3, 0x10)  # data port
    cpu.run(2)
    assert usart.has_output()
    assert usart.read_output() == 0x42


def test_usart_data_read():
    cpu, usart = make_system()
    usart.rx_push(0x55)
    cpu.PC = 0
    cpu.mem.wb(0, 0xDB)  # IN
    cpu.mem.wb(1, 0x10)  # data port
    cpu.step()
    assert cpu.A == 0x55


def test_usart_console_out_callback():
    captured = []
    usart = USART8251(console_out=captured.append)
    usart._out_data(None, 0x41)
    assert captured == [0x41]


def test_usart_has_input_false_initially():
    usart = USART8251()
    assert not usart.has_input()


def test_usart_rx_push():
    usart = USART8251()
    usart.rx_push(0x41)
    usart.rx_push(0x42)
    assert usart.has_input()
    assert usart._in_data(None) == 0x41
    assert usart._in_data(None) == 0x42
    assert not usart.has_input()


def test_usart_command_disable_rx():
    cpu, usart = make_system()
    usart.rx_push(0x42)
    # Disable RX
    cpu.PC = 0
    cpu.mem.wb(0, 0x3E)  # MVI A
    cpu.mem.wb(1, 0x00)  # value: nothing enabled
    cpu.mem.wb(2, 0xD3)  # OUT
    cpu.mem.wb(3, 0x11)  # control port
    cpu.run(2)
    # RX should be cleared
    assert not usart.has_input()


def test_usart_status_rx_ready_set_on_push():
    usart = USART8251()
    assert not (usart.status & usart.STATUS_RX_READY)
    usart.rx_push(0x41)
    assert usart.status & usart.STATUS_RX_READY
