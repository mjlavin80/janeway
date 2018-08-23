__copyright__ = "Copyright 2017 Birkbeck, University of London"
__author__ = "Martin Paul Eve & Andy Byers"
__license__ = "AGPL v3"
__maintainer__ = "Birkbeck Centre for Technology and Publishing"


from utils import notify, render_template


def send_slack(request, slack_message, slack_channels):
    notify_contents = {
        'slack_message': slack_message,
        'action': slack_channels,
        'request': request,
    }
    notify.notification(**notify_contents)


def send_email_with_body_from_setting_template(request, template, subject, to, context, log_dict=None):
    notify_contents = {
        'subject': subject,
        'to': to,
        'html': render_template.get_message_content(request, context, template),
        'action': ['email'],
        'request': request,
        'log_dict': log_dict
    }
    notify.notification(**notify_contents)


def send_email_with_body_from_user(request, subject, to, body, log_dict=None):
    notify_contents = {
        'subject': subject,
        'to': to,
        'html': body,
        'action': ['email'],
        'request': request,
        'log_dict': log_dict,
    }
    notify.notification(**notify_contents)
