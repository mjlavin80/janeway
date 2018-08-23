import hashlib
import hmac

from django.conf import settings

from utils import models, notify_helpers
from cron.models import Request


def parse_mailgun_webhook(post):
    message_id = post.get('Message-Id')
    token = post.get('token')
    timestamp = post.get('timestamp')
    signature = post.get('signature')
    mailgun_event = post.get('event')

    try:
        event = models.LogEntry.objects.get(message_id=message_id)
    except models.LogEntry.DoesNotExist:
        return 'No log entry with that message ID found.'

    if event and (mailgun_event == 'dropped' or mailgun_event == 'bounced'):
        event.message_status = 'failed'
        event.save()
        attempt_actor_email(event)
        return 'Message dropped, actor notified.'
    elif event and mailgun_event == 'delivered':
        event.message_status = 'delivered'
        event.save()
        return 'Message marked as delivered.'


def verify_webhook(token, timestamp, signature):
    api_key = settings.MAILGUN_ACCESS_KEY.encode('utf-8')
    timestamp = timestamp.encode('utf-8')
    token = token.encode('utf-8')
    signature = signature.encode('utf-8')

    hmac_digest = hmac.new(key=api_key,
                           msg='{}{}'.format(timestamp, token).encode('utf-8'),
                           digestmod=hashlib.sha256).hexdigest()

    return hmac.compare_digest(signature, hmac_digest.encode('utf-8'))


def attempt_actor_email(event):

    actor = event.actor
    article = event.target

    # Set To to the main contact, and then attempt to find a better match.
    from press import models as pm
    press = pm.Press.objects.all()[0]
    to = press.main_contact

    if actor and article:
        if actor.is_staff or actor.is_superuser:
            # Send an email to this actor
            to = actor.email
            pass
        elif actor and article.journal and actor in article.journal.editors():
            # Send email to this actor
            to = actor.email
        elif actor and not article.journal and actor in press.preprint_editors():
            to = actor.email

    # Fake a request object
    request = Request()
    request.press = press
    request.site_type = press

    body = """
        <p>A message sent to {0} from article {1} (ID: {2}) has been marked as failed.<p>
        <p>Regards,</p>
        <p>Janeway</p>
    """.format(event.to, article.title, article.pk)
    notify_helpers.send_email_with_body_from_user(request,
                                                  'Email Bounced',
                                                  to,
                                                  body,
                                                  log_dict=None)
