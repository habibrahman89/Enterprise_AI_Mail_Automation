from services.gmail_service import GmailService
from services.outlook_service import OutlookService


def get_mail_service(account):
    """
    Build the right provider service for a specific connected
    MailAccount row (as opposed to a single global mailbox).
    """
    if account.provider == 'gmail':
        return GmailService(account)
    if account.provider == 'outlook':
        return OutlookService(account)
    raise ValueError(f"Unknown mail account provider: {account.provider!r}")
