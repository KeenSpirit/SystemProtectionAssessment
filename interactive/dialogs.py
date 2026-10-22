"""
Shared dialog plumbing for the interactive frontend.

Exit buttons used to call sys.exit() from inside a tkinter callback.
That ended the whole PowerFactory script run, and in a merged codebase
it would also be a trap for any caller that is not the top-level
script. They now record the cancellation and close the window; the
dialog function raises UserCancelled once mainloop returns, and the
tool or entry point that owns the run decides what to do with it.

The flag is needed because tkinter swallows ordinary exceptions raised
inside a button callback (it prints them and carries on), so raising
UserCancelled directly from the callback would be silently ignored.

Classes:
    UserCancelled: The user pressed Exit in a dialog.
    CancelFlag: Records an Exit press inside a tkinter callback.
"""

import tkinter as tk


class UserCancelled(Exception):
    """Raised after the user presses Exit in an interactive dialog."""


class CancelFlag:
    """
    Record an Exit-button press made inside a tkinter callback.

    Example:
        >>> flag = CancelFlag()
        >>> ttk.Button(root, text='Exit',
        ...            command=lambda: flag.cancel(root))
        >>> root.mainloop()
        >>> flag.raise_if_cancelled()
    """

    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self, root: tk.Tk) -> None:
        """
        Mark the dialog as cancelled and close it.

        Args:
            root: The dialog's root window.
        """
        self.cancelled = True
        root.destroy()

    def raise_if_cancelled(self) -> None:
        """
        Raise UserCancelled if cancel() was called.

        Raises:
            UserCancelled: If the user pressed Exit.
        """
        if self.cancelled:
            raise UserCancelled()
