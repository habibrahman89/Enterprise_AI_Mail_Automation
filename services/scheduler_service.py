from apscheduler.schedulers.background import BackgroundScheduler
from services.mail_service import get_mail_service
from models import db,Email,Attachment
import os
def sync_job(app):
    with app.app_context():
        try:
            ms=get_mail_service(); msgs=ms.fetch_recent(int(os.getenv('SCHEDULE_SYNC_LIMIT','50')),'inbox')
            for m in msgs:
                if Email.query.filter_by(provider_message_id=m['id']).first():continue
                e=Email(provider=os.getenv('MAIL_PROVIDER','gmail'),provider_message_id=m['id'],thread_id=m.get('thread_id'),conversation_id=m.get('conversation_id'),sender=m.get('sender',''),recipients=m.get('recipients',''),cc=m.get('cc',''),subject=m.get('subject',''),body=m.get('body',''),received_at=m.get('received_at'),is_read=m.get('is_read',False),folder=m.get('folder','Inbox'));db.session.add(e);db.session.flush()
                for a in m.get('attachments',[]):db.session.add(Attachment(email_id=e.id,provider_attachment_id=a.get('id'),filename=a.get('filename'),content_type=a.get('content_type'),size_bytes=a.get('size_bytes',0),storage_path=a.get('storage_path'),extracted_text=a.get('extracted_text','')))
            db.session.commit()
        except:db.session.rollback()
def start_scheduler(app):
    s=BackgroundScheduler(daemon=True);s.add_job(lambda:sync_job(app),'interval',minutes=int(os.getenv('SCHEDULE_MINUTES','15')),id='mail_sync',replace_existing=True);s.start();return s
