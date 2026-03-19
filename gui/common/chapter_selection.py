from typing import Iterable

import qtawesome as qta
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QToolButton, QWidget


def apply_select_icon(button: QToolButton, is_selected: bool) -> None:
    color = '#ff8a7a' if is_selected else '#9b7670'
    icon_name = 'fa5s.check-circle' if is_selected else 'fa5s.circle'
    button.setIcon(qta.icon(icon_name, color=color))


def set_selector_visibility(row: QWidget, button: QToolButton, *, force: bool = False, hide_when_inactive: bool = False) -> None:
    show_selector = force or button.isChecked() or row.underMouse()
    if show_selector:
        apply_select_icon(button, button.isChecked())
        button.setEnabled(True)
        button.setCursor(Qt.PointingHandCursor)
        button.show()
        return
    if hide_when_inactive:
        button.hide()
    else:
        button.setIcon(QIcon())
        button.setEnabled(False)
        button.setCursor(Qt.ArrowCursor)


def selector_buttons(container: QWidget, property_name: str) -> list[QToolButton]:
    return [
        button
        for button in container.findChildren(QToolButton)
        if button.property(property_name)
    ]


def selector_row(button: QToolButton, container: QWidget) -> QWidget | None:
    row = button.parentWidget()
    while row is not None and row.parentWidget() is not container:
        row = row.parentWidget()
    return row


def refresh_selector_visibility(
    container: QWidget,
    property_name: str,
    *,
    force: bool,
    hide_when_inactive: bool = False,
) -> None:
    for button in selector_buttons(container, property_name):
        row = selector_row(button, container)
        if row is None:
            continue
        set_selector_visibility(row, button, force=force, hide_when_inactive=hide_when_inactive)


def sync_selector_checked_state(
    container: QWidget,
    property_name: str,
    selected_values: Iterable[str],
    *,
    hide_when_inactive: bool = False,
    force: bool,
) -> None:
    selected = set(selected_values)
    for button in selector_buttons(container, property_name):
        value = button.property(property_name)
        checked = value in selected
        button.blockSignals(True)
        button.setChecked(checked)
        button.blockSignals(False)
        apply_select_icon(button, checked)
    refresh_selector_visibility(
        container,
        property_name,
        force=force,
        hide_when_inactive=hide_when_inactive,
    )
