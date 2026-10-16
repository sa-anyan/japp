"""Readable Excel progress labels, separate from SQLite's detailed workflow states."""

PIPELINE_OPTIONS = (
    "Not Started", "Exported", "Researched", "Contacts Ready", "Documents Ready",
    "Draft Pending", "Email Drafted", "Email Queued", "Sending", "Completed",
    "Awaiting User Inputs", "Failed", "Cancelled",
)

ALIASES = {
    "PENDING_RESEARCH": "Not Started", "RESEARCH_READY": "Researched",
    "PENDING_APPROVAL": "Researched", "APPROVED": "Not Started",
    "APPLICATION_PACKAGE_READY": "Researched", "AWAITING_AI_OUTPUT": "Researched",
    "DOCUMENTS_READY": "Documents Ready", "CONTACTS_PENDING": "Documents Ready",
    "CONTACTS_READY": "Documents Ready", "EMAIL_DRAFTED": "Email Drafted",
    "AWAITING_BATCH_APPROVAL": "Draft Pending", "EMAIL_QUEUED": "Email Queued",
    "EMAIL_SUBMITTED": "Sending", "AWAITING_USER_INPUTS": "Awaiting User Inputs",
}


def display_status(value) -> str:
    text = str(value or "").strip()
    if not text:
        return "Not Started"
    for option in PIPELINE_OPTIONS:
        if option.casefold() == text.casefold():
            return option
    key = text.upper().replace(" ", "_")
    if key.startswith("FAILED:"):
        return "Failed"
    if key in ALIASES:
        return ALIASES[key]
    raise ValueError(f"Unknown pipeline status: {value}")


def next_display_status(current, incoming) -> str:
    old, new = display_status(current), display_status(incoming)
    # Earlier stages may be rerun while contacts/documents are already ready.
    # Completion is sticky; Excel edits never reset SQLite's send history.
    progress = PIPELINE_OPTIONS[:10]
    if old == "Completed":
        return old
    if old in progress and new in progress and progress.index(old) > progress.index(new):
        return old
    return new
