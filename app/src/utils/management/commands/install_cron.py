import os

from django.core.management.base import BaseCommand
from django.conf import settings

from crontab import CronTab


def find_job(tab, comment):
    for job in tab:
        if job.comment == comment:
            return job
    return None


class Command(BaseCommand):
    """
    Installs a cron tasks.
    """

    help = "Installs a cron tasks."

    def add_arguments(self, parser):
        parser.add_argument('--action', default="")

    def handle(self, *args, **options):
        """Installs Cron

        :param args: None
        :param options: None
        :return: None
        """
        action = options.get('action')
        tab = CronTab(user=True)
        virtualenv = os.environ.get('VIRTUAL_ENV', None)

        jobs = [
            {'name': 'janeway_cron_job', 'time': 30, 'task': 'execute_cron_tasks'},
            {'name': 'janeway_ithenticate_job', 'time': 30, 'task': 'store_ithenticate_scores'},
        ]

        if settings.ENABLE_ENHANCED_MAILGUN_FEATURES:
            jobs.append({'name': 'janeway_mailgun_job', 'time': 60, 'task': 'check_mailgun_stat'})

        for job in jobs:
            current_job = find_job(tab, job['name'])

            if not current_job:
                django_command = "{0}/manage.py {1}".format(settings.BASE_DIR, job['task'])
                if virtualenv:
                    command = '%s/bin/python3 %s' % (virtualenv, django_command)
                else:
                    command = '%s' % (django_command)

                cron_job = tab.new(command, comment=job['name'])
                cron_job.minute.every(job['time'])

            else:
                print("{name} cron job already exists.".format(name=job['name']))

        if action == 'test':
            print(tab.render())
        elif action == 'quiet':
            pass
        else:
            tab.write()
