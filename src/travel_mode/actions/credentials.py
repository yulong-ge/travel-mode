"""Credential acquisition is an executor action."""

from .base import Command


class Authenticate(Command):
    requires_journal = False
    description = "impl.sudo_required_enter_your_password_in"
    sudo = True
    interactive = True

    def command(self):
        return ["-v"]
