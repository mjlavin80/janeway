__copyright__ = "Copyright 2017 Birkbeck, University of London"
__author__ = "Martin Paul Eve & Andy Byers"
__license__ = "AGPL v3"
__maintainer__ = "Birkbeck Centre for Technology and Publishing"

import json as jason
import os
from uuid import uuid4
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

from django.utils import timezone
from django.core.serializers import json
from django.db import models
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericForeignKey
from django.conf import settings

from hvad.models import TranslatableModel, TranslatedFields
from utils.shared import get_ip_address
from utils import notify


LOG_TYPES = [
    ('Email', 'Email'),
    ('PageView', 'PageView'),
    ('EditorialAction', 'EditorialAction'),
    ('Error', 'Error'),
    ('Authentication', 'Authentication'),
    ('Submission', 'Submission'),
    ('Publication', 'Publication')
]

LOG_LEVELS = [
    ('Error', 'Error'),
    ('Debug', 'Debug'),
    ('Info', 'Info'),
]

MESSAGE_STATUS = [
    ('no_information', 'No Information'),
    ('accepted', 'Sending'),
    ('delivered', 'Delivered'),
    ('failed', 'Failed'),
]


class LogEntry(models.Model):
    types = models.CharField(max_length=255, null=True, blank=True)
    date = models.DateTimeField(auto_now_add=True)
    subject = models.TextField(null=True, blank=True)
    description = models.TextField(null=True, blank=True)
    level = models.CharField(max_length=20, null=True, blank=True, choices=LOG_LEVELS)
    actor = models.ForeignKey('core.Account', null=True, blank=True, related_name='actor', on_delete=models.SET_NULL)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, related_name='content_type', null=True)
    object_id = models.PositiveIntegerField(blank=True, null=True)
    target = GenericForeignKey('content_type', 'object_id')

    is_email = models.BooleanField(default=False)
    to = models.EmailField(blank=True, null=True)
    message_id = models.TextField(blank=True, null=True)
    message_status = models.CharField(max_length=255, choices=MESSAGE_STATUS, default='no_information')
    number_status_checks = models.IntegerField(default=0)
    status_checks_complete = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = 'log entries'

    def __str__(self):
        return u'[{0}] {1} - {2} {3}'.format(self.types, self.date, self.subject, self.message_id)

    def __repr__(self):
        return u'[{0}] {1} - {2} {3}'.format(self.types, self.date, self.subject, self.message_id)

    def message_status_class(self):
        if self.message_status == 'delivered':
            return 'green'
        elif self.message_status == 'failed':
            return 'red'
        else:
            return 'amber'

    @staticmethod
    def add_entry(types, description, level, actor=None, request=None, target=None, is_email=False, to=None,
                  message_id=None, subject=None):

        if actor is not None and callable(getattr(actor, "is_anonymous", None)):
            if actor.is_anonymous():
                actor = None

        kwargs = {
            'types': types,
            'description': description,
            'level': level,
            # if no actor is supplied, assume anonymous
            'actor': actor if actor else None,
            'ip_address': get_ip_address(request),
            'target': target,
            'is_email': is_email,
            'to': to,
            'message_id': message_id,
            'subject': subject,
        }

        new_entry = LogEntry.objects.create(**kwargs).save()

        return new_entry


class Plugin(models.Model):
    name = models.CharField(max_length=200)
    version = models.CharField(max_length=10)
    date_installed = models.DateTimeField(auto_now_add=True)
    enabled = models.BooleanField(default=True)
    display_name = models.CharField(max_length=200, blank=True, null=True)
    press_wide = models.BooleanField(default=False)

    def __str__(self):
        return u'[{0}] {1} - {2}'.format(self.name, self.version, self.enabled)

    def __repr__(self):
        return u'[{0}] {1} - {2}'.format(self.name, self.version, self.enabled)

    def best_name(self):
        if self.display_name:
            return self.display_name

        return self.name


setting_types = (
    ('rich-text', 'Rich Text'),
    ('text', 'Text'),
    ('char', 'Characters'),
    ('number', 'Number'),
    ('boolean', 'Boolean'),
    ('file', 'File'),
    ('select', 'Select'),
    ('json', 'JSON'),
)


class PluginSetting(models.Model):
    name = models.CharField(max_length=100)
    plugin = models.ForeignKey(Plugin)
    types = models.CharField(max_length=20, choices=setting_types, default='text')
    pretty_name = models.CharField(max_length=100, default='')
    description = models.TextField(null=True, blank=True)
    is_translatable = models.BooleanField(default=False)

    class Meta:
        ordering = ('plugin', 'name')

    def __str__(self):
        return u'%s' % self.name

    def __repr__(self):
        return u'%s' % self.name


class PluginSettingValue(TranslatableModel):
    journal = models.ForeignKey('journal.Journal', blank=True, null=True)
    setting = models.ForeignKey(PluginSetting)

    translations = TranslatedFields(
        value=models.TextField(null=True, blank=True)
    )

    def __repr__(self):
        return "{0}, {1}".format(self.setting.name, self.value)

    def __str__(self):
        return "[{0}]: {1}".format(self.journal, self.setting.name)

    @property
    def processed_value(self):
        return self.process_value()

    def process_value(self):
        """ Converts string values of settings to proper values

        :return: a value
        """

        if self.setting.types == 'boolean' and self.value == 'on':
            return True
        elif self.setting.types == 'boolean':
            return False
        elif self.setting.types == 'number':
            try:
                return int(self.value)
            except BaseException:
                return 0
        elif self.setting.types == 'json' and self.value:
            return json.loads(self.value)
        else:
            return self.value


class ImportCacheEntry(models.Model):
    url = models.TextField(max_length=800, blank=False, null=False)
    on_disk = models.TextField(max_length=800, blank=False, null=False)
    mime_type = models.CharField(max_length=200, null=True, blank=True)
    date_time = models.DateTimeField(default=timezone.now)

    @staticmethod
    def nuke():
        for cache in ImportCacheEntry.objects.all():
            os.remove(cache.on_disk)
            cache.delete()

    @staticmethod
    def fetch(url, up_auth_file='', up_base_url='', ojs_auth_file=''):
        try:
            cached = ImportCacheEntry.objects.get(url=url)

            if cached.date_time < timezone.now() - timezone.timedelta(minutes=30):
                cached.delete()
                if not settings.SILENT_IMPORT_CACHE:
                    print("[CACHE] Found old cached entry, expiring.")
                ImportCacheEntry.fetch(url, up_auth_file, up_base_url, ojs_auth_file)
            else:
                cached.date_time = timezone.now()
                cached.save()

            if not settings.SILENT_IMPORT_CACHE:
                print("[CACHE] Using cached version of {0}".format(url))

            with open(cached.on_disk, 'rb') as on_disk_file:
                return on_disk_file.read(), cached.mime_type

        except ImportCacheEntry.DoesNotExist:
            if not settings.SILENT_IMPORT_CACHE:
                print("[CACHE] Fetching remote version of {0}".format(url))

            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_1) AppleWebKit/537.36 (KHTML, like Gecko) '
                              'Chrome/39.0.2171.95 Safari/537.36'}

            # disable SSL checking
            requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

            # setup auth variables
            do_auth = False
            username = ''
            password = ''

            session = requests.Session()

            # first, check whether there's an auth file
            if up_auth_file != '':
                with open(up_auth_file, 'r') as auth_in:
                    auth_dict = jason.loads(auth_in.read())
                    do_auth = True
                    username = auth_dict['username']
                    password = auth_dict['password']

            if do_auth:
                # load the login page
                auth_url = '{0}{1}'.format(up_base_url, '/login/')
                fetched = session.get(auth_url, headers=headers, stream=True, verify=False)

                post_dict = {'username': username, 'password': password, 'login': 'login'}
                fetched = session.post('{0}{1}'.format(up_base_url, '/login/signIn/'), data=post_dict,
                                       headers={'Referer': auth_url,
                                                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_10_1) AppleWebKit/537.36 (KHTML, like Gecko) '
                                                              'Chrome/39.0.2171.95 Safari/537.36'
                                                })
                if not settings.SILENT_IMPORT_CACHE:
                    print("[CACHE] Sending auth")

            fetched = session.get(url, headers=headers, stream=True, verify=False)

            resp = bytes()

            for chunk in fetched.iter_content(chunk_size=512 * 1024):
                resp += chunk

            # set the filename to a unique UUID4 identifier with the passed file extension
            filename = '{0}'.format(uuid4())

            # set the path to save to be the sub-directory for the article
            path = os.path.join(settings.BASE_DIR, 'files', 'import_cache')

            # create the sub-folders as necessary
            if not os.path.exists(path):
                os.makedirs(path, 0o0775)

            with open(os.path.join(path, filename), 'wb') as f:
                f.write(resp)

            ImportCacheEntry.objects.create(url=url, mime_type=fetched.headers.get('content-type'),
                                            on_disk=os.path.join(path, filename)).save()

            return resp, fetched.headers.get('content-type')

    def __str__(self):
        return self.url
