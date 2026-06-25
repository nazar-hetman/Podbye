"""Tests for Scheduled Task parsing in startup_detector.

_extract_task_info() turns a parsed Windows Task <Task> element into the
startup-relevant fields, or None when the task is not a startup item.
"""
import xml.etree.ElementTree as ET

from app.services.startup_detector import _extract_task_info

_NS = 'xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"'


def _info(body: str):
    return _extract_task_info(ET.fromstring(f"<Task {_NS}>{body}</Task>"))


def test_logon_exec_task_is_detected():
    info = _info(
        "<RegistrationInfo><Author>Acme Corp</Author></RegistrationInfo>"
        "<Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>"
        "<Settings><Enabled>true</Enabled></Settings>"
        "<Actions><Exec><Command>C:\\Acme\\acme.exe</Command>"
        "<Arguments>/bg</Arguments></Exec></Actions>"
    )
    assert info is not None
    assert info["trigger"] == "logon"
    assert info["command"] == "C:\\Acme\\acme.exe"
    assert info["arguments"] == "/bg"
    assert info["enabled"] is True
    assert info["author"] == "Acme Corp"


def test_boot_trigger_is_detected():
    info = _info(
        "<Triggers><BootTrigger/></Triggers>"
        "<Actions><Exec><Command>svc.exe</Command></Exec></Actions>"
    )
    assert info is not None and info["trigger"] == "boot"


def test_time_triggered_task_is_ignored():
    info = _info(
        "<Triggers><TimeTrigger>"
        "<StartBoundary>2020-01-01T03:00:00</StartBoundary>"
        "</TimeTrigger></Triggers>"
        "<Actions><Exec><Command>maint.exe</Command></Exec></Actions>"
    )
    assert info is None


def test_logon_task_without_exec_action_is_ignored():
    info = _info(
        "<Triggers><LogonTrigger/></Triggers>"
        "<Actions><ComHandler><ClassId>{GUID}</ClassId></ComHandler></Actions>"
    )
    assert info is None


def test_disabled_task_reports_disabled():
    info = _info(
        "<Triggers><LogonTrigger/></Triggers>"
        "<Settings><Enabled>false</Enabled></Settings>"
        "<Actions><Exec><Command>x.exe</Command></Exec></Actions>"
    )
    assert info is not None and info["enabled"] is False


def test_disabled_logon_trigger_is_skipped():
    info = _info(
        "<Triggers><LogonTrigger><Enabled>false</Enabled></LogonTrigger></Triggers>"
        "<Actions><Exec><Command>x.exe</Command></Exec></Actions>"
    )
    assert info is None
