"""What the errors that are a message for the user have in common."""


class UserFacingError(Exception):
    """An error whose words are meant for the user: they say what happened or what to do,
    and the program shows them as they are (the device not ready yet, not on the account,
    an answer of a server, a refusal). The log of the window writes it in one line, without
    a traceback: that is kept for an error nobody expected."""


class OrdinaryStop(UserFacingError):
    """Not an error: the program stopped as it should, waiting for the user or for something
    else to end (the choice of the first sync, another sync running, a notice about the USB
    mode of the device…). The user is told as before; the log says it in one line of INFO,
    `log_reason`, never as a failure."""

    log_reason = "stopped as it should"
